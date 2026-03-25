"""
Database Storage Module

Handles connection to PostgreSQL with pgvector extension,
schema initialization, and storage of articles with vector embeddings.
"""

import os
import json
import hashlib
from typing import List, Dict, Any, Optional
from datetime import datetime, date
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import Json, execute_batch
from psycopg2.pool import SimpleConnectionPool
from pgvector.psycopg2 import register_vector

from ..utils.logger import get_logger

logger = get_logger(__name__)


class DatabaseManager:
    """
    Manages PostgreSQL database with pgvector extension for RAG system.
    """

    @staticmethod
    def _sanitize_text(text: Optional[str]) -> Optional[str]:
        """
        Remove or replace Unicode surrogate characters and other bytes
        that PostgreSQL would reject as invalid UTF-8 sequences.
        Surrogate escapes (U+D800-U+DFFF) can appear when web scrapers
        read latin-1/binary content and Python stores them internally.
        """
        if not text:
            return text
        return text.encode('utf-8', errors='surrogatepass').decode('utf-8', errors='replace')

    def __init__(self, connection_url: Optional[str] = None):
        """
        Initialize database manager with connection pooling.

        Args:
            connection_url: PostgreSQL connection URL. If None, reads from environment.
        """
        # Get connection URL from environment or parameter
        if connection_url is None:
            connection_url = os.getenv('DATABASE_URL')
            if not connection_url:
                # Fallback to individual env vars
                db_host = os.getenv("DB_HOST", "localhost")
                db_name = os.getenv("DB_NAME", "intelligence_ita")
                db_user = os.getenv("DB_USER", "postgres")
                db_pass = os.getenv("DB_PASS", "")
                db_port = os.getenv("DB_PORT", "5432")
                connection_url = f"postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"

        self.connection_url = connection_url

        # Create connection pool (min 1, max 10 connections)
        try:
            self.pool = SimpleConnectionPool(
                minconn=1,
                maxconn=10,
                dsn=connection_url
            )
            logger.info(f"✓ Database connection pool initialized")
        except Exception as e:
            logger.error(f"Failed to create connection pool: {e}")
            raise

        # Cache: feed_name -> (source_id, domain) from intelligence_sources.
        # Loaded lazily on first save_article() call. _source_cache_loaded prevents
        # repeated DB calls when the table is empty or not yet migrated.
        self._source_cache: dict = {}
        self._source_cache_loaded: bool = False

    def _load_source_cache(self) -> None:
        """Load feed_name -> (source_id, domain) mapping from intelligence_sources."""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, domain, feed_names FROM intelligence_sources "
                        "WHERE feed_names != '{}'"
                    )
                    for source_id, domain, feed_names in cur.fetchall():
                        for feed_name in (feed_names or []):
                            self._source_cache[feed_name] = (source_id, domain)
            logger.debug(f"Source cache loaded: {len(self._source_cache)} feed name(s) mapped")
        except Exception as e:
            # Table may not exist yet (pre-migration). Non-fatal: articles saved without source_id.
            logger.warning(f"Could not load source cache (migration 024 applied?): {e}")
        finally:
            self._source_cache_loaded = True

    def _get_source_info(self, source_id: int) -> Optional[dict]:
        """Get source name, domain, authority_score by source_id. Cached after first call."""
        if not hasattr(self, '_source_info_cache'):
            self._source_info_cache = {}

        if source_id in self._source_info_cache:
            return self._source_info_cache[source_id]

        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT name, domain, authority_score, source_type "
                        "FROM intelligence_sources WHERE id = %s",
                        (source_id,)
                    )
                    row = cur.fetchone()
                    if row:
                        info = {
                            'name': row[0], 'domain': row[1],
                            'authority_score': float(row[2]) if row[2] else None,
                            'source_type': row[3],
                        }
                        self._source_info_cache[source_id] = info
                        return info
        except Exception as e:
            logger.debug(f"Could not fetch source info for id={source_id}: {e}")
        return None

    @contextmanager
    def get_connection(self):
        """
        Context manager for database connections.
        Automatically returns connection to pool after use.
        """
        conn = self.pool.getconn()
        try:
            # Register pgvector type for this connection
            register_vector(conn)
            # Ensure UTF-8 client encoding (python:slim uses C locale by default)
            conn.set_client_encoding('UTF8')
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Database error: {e}")
            raise
        finally:
            self.pool.putconn(conn)

    def init_db(self):
        """
        Initialize database schema: enable pgvector extension and create tables.
        """
        logger.info("Initializing database schema...")

        schema_sql = """
        -- Enable pgvector extension
        CREATE EXTENSION IF NOT EXISTS vector;

        -- Articles table
        CREATE TABLE IF NOT EXISTS articles (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            link TEXT UNIQUE NOT NULL,
            published_date TIMESTAMP WITH TIME ZONE,
            source TEXT,
            category TEXT,
            subcategory TEXT,
            summary TEXT,
            full_text TEXT,
            entities JSONB,              -- Extracted entities (PERSON, ORG, GPE, etc.)
            nlp_metadata JSONB,          -- NLP statistics (word count, tokens, etc.)
            full_text_embedding vector(384),  -- Full article embedding for similarity
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );

        -- Chunks table for RAG
        CREATE TABLE IF NOT EXISTS chunks (
            id SERIAL PRIMARY KEY,
            article_id INTEGER REFERENCES articles(id) ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            embedding vector(384),       -- Chunk embedding for semantic search
            word_count INTEGER,
            sentence_count INTEGER,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );

        -- Performance indexes
        CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_date DESC);
        CREATE INDEX IF NOT EXISTS idx_articles_category ON articles(category);
        CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source);
        CREATE INDEX IF NOT EXISTS idx_chunks_article_id ON chunks(article_id);

        -- HNSW index for fast approximate nearest neighbor search
        CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON chunks
            USING hnsw (embedding vector_cosine_ops);

        CREATE INDEX IF NOT EXISTS idx_articles_full_embedding ON articles
            USING hnsw (full_text_embedding vector_cosine_ops);

        -- Reports table (Phase 4: LLM-generated reports)
        CREATE TABLE IF NOT EXISTS reports (
            id SERIAL PRIMARY KEY,
            report_date DATE NOT NULL,
            generated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            model_used TEXT,
            draft_content TEXT NOT NULL,          -- Original LLM-generated report
            final_content TEXT,                   -- Human-edited version (NULL if not reviewed)
            status TEXT DEFAULT 'draft',          -- draft, reviewed, approved
            report_type TEXT DEFAULT 'daily',     -- daily, weekly (for meta-analysis)
            metadata JSONB,                       -- focus_areas, article_count, etc.
            sources JSONB,                        -- Links to source articles and chunks
            human_reviewed_at TIMESTAMP WITH TIME ZONE,
            human_reviewer TEXT,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );

        -- Report feedback table (Phase 5: Human-in-the-Loop)
        CREATE TABLE IF NOT EXISTS report_feedback (
            id SERIAL PRIMARY KEY,
            report_id INTEGER REFERENCES reports(id) ON DELETE CASCADE,
            section_name TEXT,                    -- e.g., "Executive Summary", "Cybersecurity"
            feedback_type TEXT NOT NULL,          -- 'correction', 'addition', 'removal', 'rating'
            original_text TEXT,                   -- What LLM originally wrote
            corrected_text TEXT,                  -- What human changed it to
            comment TEXT,                         -- Human explanation/notes
            rating INTEGER CHECK (rating >= 1 AND rating <= 5),  -- 1-5 stars for quality
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );

        -- Report indexes
        CREATE INDEX IF NOT EXISTS idx_reports_date ON reports(report_date DESC);
        CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status);
        CREATE INDEX IF NOT EXISTS idx_reports_type ON reports(report_type);
        CREATE INDEX IF NOT EXISTS idx_report_feedback_report_id ON report_feedback(report_id);

        -- Migration: Add report_type column to existing reports table
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='reports' AND column_name='report_type'
            ) THEN
                ALTER TABLE reports ADD COLUMN report_type TEXT DEFAULT 'daily';
                CREATE INDEX idx_reports_type ON reports(report_type);
            END IF;
        END $$;

        -- Update timestamp trigger
        CREATE OR REPLACE FUNCTION update_updated_at_column()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = CURRENT_TIMESTAMP;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS update_articles_updated_at ON articles;
        CREATE TRIGGER update_articles_updated_at
            BEFORE UPDATE ON articles
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column();

        DROP TRIGGER IF EXISTS update_reports_updated_at ON reports;
        CREATE TRIGGER update_reports_updated_at
            BEFORE UPDATE ON reports
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column();
        """

        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(schema_sql)
            logger.info("✓ Database schema initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize database schema: {e}")
            raise

    def refresh_entity_idf(self) -> None:
        """
        Refresh the entity_idf materialized view used by the TF-IDF weighted Jaccard
        graph algorithm. Uses CONCURRENTLY to avoid blocking API reads during refresh.
        Falls back to non-concurrent refresh if the unique index is missing.
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY entity_idf")
                    logger.info("entity_idf materialized view refreshed (concurrent)")
                except Exception as e:
                    # Fallback: non-concurrent refresh (blocks reads briefly)
                    logger.warning(f"Concurrent refresh failed ({e}), falling back to blocking refresh")
                    conn.rollback()
                    cur.execute("REFRESH MATERIALIZED VIEW entity_idf")
                    logger.info("entity_idf materialized view refreshed (blocking)")

    def refresh_entity_bridge(self) -> None:
        """
        Refresh mv_entity_storyline_bridge materialized view.
        Uses CONCURRENTLY to avoid blocking API reads.
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_entity_storyline_bridge")
                    logger.info("mv_entity_storyline_bridge refreshed (concurrent)")
                except Exception as e:
                    logger.warning(f"Concurrent refresh failed ({e}), falling back to blocking")
                    conn.rollback()
                    cur.execute("REFRESH MATERIALIZED VIEW mv_entity_storyline_bridge")
                    logger.info("mv_entity_storyline_bridge refreshed (blocking)")

    def compute_intelligence_scores(self) -> int:
        """
        Compute intelligence_score for all entities.
        Formula: 0.3*mention_freq + 0.3*connectivity + 0.2*recency + 0.2*momentum

        - mention_freq: normalized log(mention_count) / log(max_mention_count)
        - connectivity: # linked active storylines (capped at 10)
        - recency: exp(-0.1 * days_since_last_seen), capped at 90 days
        - momentum: avg momentum_score of connected storylines

        Returns:
            Number of entities updated.
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    WITH max_mentions AS (
                        SELECT GREATEST(LN(MAX(mention_count) + 1), 1.0) AS max_log
                        FROM entities
                    ),
                    bridge_agg AS (
                        SELECT
                            entity_id,
                            COUNT(DISTINCT storyline_id) AS storyline_count,
                            COALESCE(AVG(momentum_score), 0) AS avg_momentum
                        FROM mv_entity_storyline_bridge
                        GROUP BY entity_id
                    ),
                    scores AS (
                        SELECT
                            e.id,
                            LEAST(1.0, GREATEST(0.0,
                                0.3 * (LN(e.mention_count + 1) / mm.max_log)
                                + 0.3 * (LEAST(COALESCE(ba.storyline_count, 0), 10) / 10.0)
                                + 0.2 * EXP(-0.1 * LEAST(
                                    EXTRACT(EPOCH FROM (NOW() - COALESCE(e.last_seen, e.created_at))) / 86400.0,
                                    90
                                ))
                                + 0.2 * COALESCE(ba.avg_momentum, 0)
                            )) AS score
                        FROM entities e
                        CROSS JOIN max_mentions mm
                        LEFT JOIN bridge_agg ba ON ba.entity_id = e.id
                    )
                    UPDATE entities e SET intelligence_score = s.score
                    FROM scores s
                    WHERE e.id = s.id
                """)
                updated = cur.rowcount
            conn.commit()

        logger.info(f"intelligence_score computed for {updated} entities")
        return updated

    def save_article(self, article: Dict[str, Any],
                     _known_links: Optional[set] = None,
                     _known_hashes: Optional[set] = None) -> Optional[int]:
        """
        Save a single processed article with its chunks and embeddings.

        Args:
            article: Article dictionary with nlp_data
            _known_links: Optional pre-loaded set of existing article links (skips DB link check)
            _known_hashes: Optional pre-loaded set of existing content hashes in last 7 days (skips DB hash check)

        Returns:
            Article ID if saved successfully, None if skipped (duplicate or error)
        """
        # Only save articles with successful NLP processing
        if not article.get('nlp_processing', {}).get('success', False):
            logger.debug(f"Skipping article without NLP data: {article.get('title', 'Unknown')[:50]}...")
            return None

        nlp_data = article.get('nlp_data', {})

        # Parse published date
        pub_date = article.get('published')
        if isinstance(pub_date, str):
            try:
                pub_date = datetime.fromisoformat(pub_date.replace('Z', '+00:00'))
            except:
                pub_date = None

        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    # Check if article already exists (by link)
                    if _known_links is not None:
                        # Fast path: use pre-loaded set from batch_save()
                        if article.get('link') in _known_links:
                            logger.debug(f"Article already exists: {article.get('title', '')[:50]}...")
                            return None
                    else:
                        cur.execute("SELECT id FROM articles WHERE link = %s", (article.get('link'),))
                        existing = cur.fetchone()
                        if existing:
                            logger.debug(f"Article already exists: {article.get('title', '')[:50]}...")
                            return None

                    # PHASE 2: Compute content hash for content-based deduplication
                    clean_text = nlp_data.get('clean_text', '')
                    content_hash = hashlib.md5(clean_text.encode('utf-8')).hexdigest() if clean_text else None

                    # PHASE 2: Check for duplicate content in last 7 days
                    if content_hash:
                        if _known_hashes is not None:
                            # Fast path: use pre-loaded set from batch_save()
                            if content_hash in _known_hashes:
                                logger.info(
                                    f"Skipping duplicate content: '{article.get('title', 'N/A')[:50]}...' "
                                    f"(content_hash match in pre-loaded cache)"
                                )
                                return None
                        else:
                            cur.execute("""
                                SELECT id, title, source, link
                                FROM articles
                                WHERE content_hash = %s
                                AND published_date > NOW() - INTERVAL '7 days'
                                LIMIT 1
                            """, (content_hash,))

                            existing_content = cur.fetchone()
                            if existing_content:
                                logger.info(
                                    f"Skipping duplicate content: '{article.get('title', 'N/A')[:50]}...' "
                                    f"(same as article_id={existing_content[0]} "
                                    f"'{existing_content[1][:50]}...' from {existing_content[2]})"
                                )
                                return None

                    # Lookup source_id and domain from intelligence_sources cache
                    if not self._source_cache_loaded:
                        self._load_source_cache()
                    source_name = article.get('source', '')
                    src_id, src_domain = self._source_cache.get(source_name, (None, None))

                    # Resolve extraction_method from article metadata
                    extraction_method = article.get('extraction_method')
                    if not extraction_method:
                        fc = article.get('full_content')
                        if isinstance(fc, dict):
                            extraction_method = fc.get('extraction_method')

                    # Insert article
                    cur.execute("""
                        INSERT INTO articles
                        (title, link, published_date, source, category, subcategory, summary,
                         full_text, entities, nlp_metadata, full_text_embedding, content_hash,
                         source_id, domain, extraction_method)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                    """, (
                        self._sanitize_text(article.get('title')),
                        article.get('link'),
                        pub_date,
                        article.get('source'),
                        article.get('category'),
                        article.get('subcategory'),
                        self._sanitize_text(article.get('summary')),
                        self._sanitize_text(nlp_data.get('clean_text', '')),
                        Json(nlp_data.get('entities', {})),
                        Json({
                            'original_length': nlp_data.get('original_length', 0),
                            'clean_length': nlp_data.get('clean_length', 0),
                            'num_tokens': nlp_data.get('preprocessed', {}).get('num_tokens', 0),
                            'num_sentences': nlp_data.get('preprocessed', {}).get('num_sentences', 0),
                            'entity_count': nlp_data.get('entities', {}).get('entity_count', 0)
                        }),
                        nlp_data.get('full_text_embedding', []),
                        content_hash,  # PHASE 2: Save content hash
                        src_id,
                        src_domain,
                        extraction_method,
                    ))

                    article_id = cur.fetchone()[0]

                    # Insert chunks in batch (with source_id + metadata prefix)
                    chunks = nlp_data.get('chunks', [])
                    if chunks:
                        # Build metadata prefix for authority-aware RAG
                        source_prefix = ''
                        if src_id:
                            source_info = self._get_source_info(src_id)
                            if source_info:
                                source_prefix = (
                                    f"[Fonte: {source_info.get('name', '')} | "
                                    f"Dominio: {source_info.get('domain', '')} | "
                                    f"Autorevolezza: {source_info.get('authority_score', '')}]"
                                )

                        chunk_data = []
                        for idx, chunk in enumerate(chunks):
                            chunk_text = chunk['text']
                            # Inject metadata prefix + section title
                            prefix_parts = []
                            if source_prefix:
                                section_title = chunk.get('section_title')
                                if section_title:
                                    prefix_parts.append(f"{source_prefix} | Sezione: {section_title}]"[:-1])
                                else:
                                    prefix_parts.append(source_prefix)
                            elif chunk.get('section_title'):
                                prefix_parts.append(f"[Sezione: {chunk['section_title']}]")

                            if prefix_parts:
                                chunk_text = prefix_parts[0] + '\n' + chunk_text

                            chunk_data.append((
                                article_id,
                                idx,
                                chunk_text,
                                chunk['embedding'],
                                chunk.get('word_count', 0),
                                chunk.get('sentence_count', 0),
                                src_id,
                            ))

                        execute_batch(cur, """
                            INSERT INTO chunks
                            (article_id, chunk_index, content, embedding, word_count, sentence_count,
                             source_id)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """, chunk_data, page_size=100)

                    logger.debug(f"✓ Saved article {article_id} with {len(chunks)} chunks")
                    return article_id

        except Exception as e:
            logger.error(f"Error saving article '{article.get('title', 'Unknown')[:50]}...': {e}")
            return None

    def batch_save(self, articles: List[Dict]) -> Dict[str, int]:
        """
        Save multiple articles in batch.

        Pre-loads existing links and content hashes in two bulk queries to avoid
        N+1 duplicate-check queries (saves ~2 round-trips per article).

        Args:
            articles: List of article dictionaries

        Returns:
            Statistics dictionary with counts
        """
        stats = {
            "saved": 0,
            "skipped": 0,
            "errors": 0,
            "total_chunks": 0
        }

        logger.info(f"Saving {len(articles)} articles to database...")

        # --- Bulk duplicate pre-check (eliminates 2 DB round-trips per article) ---
        known_links: set = set()
        known_hashes: set = set()
        try:
            candidate_links = [a.get('link') for a in articles if a.get('link')]
            candidate_hashes = []
            for a in articles:
                nlp_data = a.get('nlp_data', {})
                clean_text = nlp_data.get('clean_text', '')
                if clean_text:
                    candidate_hashes.append(
                        hashlib.md5(clean_text.encode('utf-8')).hexdigest()
                    )

            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    if candidate_links:
                        cur.execute(
                            "SELECT link FROM articles WHERE link = ANY(%s)",
                            (candidate_links,)
                        )
                        known_links = {row[0] for row in cur.fetchall()}

                    if candidate_hashes:
                        cur.execute(
                            """SELECT content_hash FROM articles
                               WHERE content_hash = ANY(%s)
                               AND published_date > NOW() - INTERVAL '7 days'""",
                            (candidate_hashes,)
                        )
                        known_hashes = {row[0] for row in cur.fetchall()}

            logger.info(
                f"Bulk dedup check: {len(known_links)} existing links, "
                f"{len(known_hashes)} duplicate hashes found among {len(articles)} candidates"
            )
        except Exception as e:
            logger.warning(f"Bulk dedup pre-check failed, falling back to per-article checks: {e}")
            known_links = None  # type: ignore[assignment]
            known_hashes = None  # type: ignore[assignment]
        # -------------------------------------------------------------------------

        for i, article in enumerate(articles):
            article_id = self.save_article(article, _known_links=known_links, _known_hashes=known_hashes)

            if article_id is not None:
                stats["saved"] += 1
                chunks_count = len(article.get('nlp_data', {}).get('chunks', []))
                stats["total_chunks"] += chunks_count

                # Save AI-generated bullet points if available
                bullet_points = article.get('nlp_data', {}).get('bullet_points')
                if bullet_points:
                    try:
                        self.update_article_analysis(article_id, {'bullet_points': bullet_points})
                    except Exception as e:
                        logger.warning(f"Failed to save bullet points for article {article_id}: {e}")

            elif article.get('nlp_processing', {}).get('success', False):
                stats["skipped"] += 1  # Duplicate
            else:
                stats["errors"] += 1  # No NLP data

            if (i + 1) % 50 == 0:
                logger.info(f"Progress: {i + 1}/{len(articles)} articles processed")

        logger.info(f"✓ Batch save complete: {stats['saved']} saved, "
                   f"{stats['skipped']} skipped (duplicates), "
                   f"{stats['errors']} errors (no NLP data)")
        logger.info(f"  Total chunks inserted: {stats['total_chunks']}")

        return stats

    def semantic_search(
        self,
        query_embedding: List[float],
        top_k: int = 10,
        category: Optional[str] = None,
        # NEW FILTERS for enhanced search
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        sources: Optional[List[str]] = None,
        gpe_entities: Optional[List[str]] = None,
        min_similarity: float = 0.0
    ) -> List[Dict[str, Any]]:
        """
        Perform semantic search using vector similarity with optional filters.

        Args:
            query_embedding: Query embedding vector (384 dimensions)
            top_k: Number of results to return
            category: Optional category filter
            start_date: Filter articles published after this date
            end_date: Filter articles published before this date
            sources: Filter by article sources (e.g., ['Reuters', 'Bloomberg'])
            gpe_entities: Filter by geographic entities (GPE) from JSONB (e.g., ['Taiwan', 'China'])
            min_similarity: Minimum similarity threshold (0-1)

        Returns:
            List of matching chunks with article metadata
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    query = """
                        SELECT
                            c.id as chunk_id,
                            c.content,
                            c.chunk_index,
                            c.word_count,
                            a.id as article_id,
                            a.title,
                            a.link,
                            a.source,
                            a.published_date,
                            a.category,
                            c.embedding,
                            1 - (c.embedding <=> %s::vector) as similarity,
                            s.source_type,
                            s.authority_score
                        FROM chunks c
                        JOIN articles a ON c.article_id = a.id
                        LEFT JOIN intelligence_sources s ON a.source_id = s.id
                        WHERE 1=1
                    """

                    params = [query_embedding]

                    # Category filter
                    if category:
                        query += " AND a.category = %s"
                        params.append(category)

                    # Date range filters
                    if start_date:
                        query += " AND a.published_date >= %s"
                        params.append(start_date)

                    if end_date:
                        query += " AND a.published_date <= %s"
                        params.append(end_date)

                    # Source filter
                    if sources:
                        query += " AND a.source = ANY(%s)"
                        params.append(sources)

                    # Geographic filtering via GPE entities (JSONB)
                    if gpe_entities:
                        query += """
                            AND EXISTS (
                                SELECT 1 FROM jsonb_array_elements_text(a.entities->'by_type'->'GPE') gpe
                                WHERE gpe = ANY(%s)
                            )
                        """
                        params.append(gpe_entities)

                    query += " ORDER BY c.embedding <=> %s::vector LIMIT %s"
                    params.extend([query_embedding, top_k])

                    cur.execute(query, params)

                    results = []
                    for row in cur.fetchall():
                        similarity = float(row[11])
                        # Apply min_similarity filter
                        if similarity >= min_similarity:
                            results.append({
                                'chunk_id': row[0],
                                'content': row[1],
                                'chunk_index': row[2],
                                'word_count': row[3],
                                'article_id': row[4],
                                'title': row[5],
                                'link': row[6],
                                'source': row[7],
                                'published_date': row[8],
                                'category': row[9],
                                'embedding': row[10],
                                'similarity': similarity,
                                'source_type': row[12],
                                'authority_score': float(row[13]) if row[13] else None,
                            })

                    return results

        except Exception as e:
            logger.error(f"Semantic search error: {e}")
            return []

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get database statistics.

        Returns:
            Dictionary with statistics
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    stats = {}

                    # Total articles
                    cur.execute("SELECT COUNT(*) FROM articles")
                    stats['total_articles'] = cur.fetchone()[0]

                    # Total chunks
                    cur.execute("SELECT COUNT(*) FROM chunks")
                    stats['total_chunks'] = cur.fetchone()[0]

                    # Articles by category
                    cur.execute("""
                        SELECT category, COUNT(*)
                        FROM articles
                        GROUP BY category
                        ORDER BY COUNT(*) DESC
                    """)
                    stats['by_category'] = dict(cur.fetchall())

                    # Recent articles count (last 7 days)
                    cur.execute("""
                        SELECT COUNT(*)
                        FROM articles
                        WHERE published_date > NOW() - INTERVAL '7 days'
                    """)
                    stats['recent_articles'] = cur.fetchone()[0]

                    # Top sources
                    cur.execute("""
                        SELECT source, COUNT(*)
                        FROM articles
                        GROUP BY source
                        ORDER BY COUNT(*) DESC
                        LIMIT 10
                    """)
                    stats['top_sources'] = dict(cur.fetchall())

                    return stats

        except Exception as e:
            logger.error(f"Error getting statistics: {e}")
            return {}

    def get_recent_articles(
        self,
        days: int = 1,
        category: Optional[str] = None,
        from_time: Optional[datetime] = None,
        to_time: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """
        Get recent articles from database.

        Args:
            days: Number of days to look back (used if from_time/to_time not specified)
            category: Optional category filter
            from_time: Optional start time for explicit time window (takes precedence over days)
            to_time: Optional end time for explicit time window (takes precedence over days)

        Returns:
            List of article dictionaries
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    query = """
                        SELECT
                            id, title, link, published_date, source, category,
                            subcategory, summary, full_text, entities, nlp_metadata, full_text_embedding
                        FROM articles
                        WHERE 1=1
                    """
                    params = []

                    # Explicit time window takes precedence over days
                    if from_time or to_time:
                        if from_time:
                            query += " AND published_date >= %s"
                            params.append(from_time)
                        if to_time:
                            query += " AND published_date <= %s"
                            params.append(to_time)
                    else:
                        query += " AND published_date > NOW() - INTERVAL '%s days'"
                        params.append(days)

                    if category:
                        query += " AND category = %s"
                        params.append(category)

                    query += " ORDER BY published_date DESC"

                    cur.execute(query, params)

                    articles = []
                    for row in cur.fetchall():
                        articles.append({
                            'id': row[0],
                            'title': row[1],
                            'link': row[2],
                            'published_date': row[3],
                            'source': row[4],
                            'category': row[5],
                            'subcategory': row[6],
                            'summary': row[7],
                            'full_text': row[8],
                            'entities': row[9],
                            'nlp_metadata': row[10],
                            'full_text_embedding': row[11]
                        })

                    return articles

        except Exception as e:
            logger.error(f"Error getting recent articles: {e}")
            return []

    def get_all_article_embeddings(
        self,
        days: int = 30,
        exclude_assigned: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Get all article embeddings for batch clustering (DBSCAN/HDBSCAN).

        Args:
            days: Time window in days (0 = all articles)
            exclude_assigned: If True, exclude articles already assigned to storylines

        Returns:
            List of {id, title, embedding, entities, category, published_date, source}
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    query = """
                        SELECT
                            a.id,
                            a.title,
                            a.full_text_embedding,
                            a.entities,
                            a.category,
                            a.published_date,
                            a.source
                        FROM articles a
                        WHERE a.full_text_embedding IS NOT NULL
                    """
                    params = []

                    # Time window filter
                    if days > 0:
                        query += " AND a.published_date > NOW() - INTERVAL '%s days'"
                        params.append(days)

                    # Exclude already assigned articles
                    if exclude_assigned:
                        query += """
                            AND NOT EXISTS (
                                SELECT 1 FROM article_storylines als
                                WHERE als.article_id = a.id
                            )
                        """

                    query += " ORDER BY a.published_date DESC"

                    cur.execute(query, params)

                    articles = []
                    for row in cur.fetchall():
                        # Handle embedding conversion
                        embedding = row[2]
                        if embedding is not None:
                            # pgvector returns as list or numpy array
                            if hasattr(embedding, 'tolist'):
                                embedding = embedding.tolist()
                            elif not isinstance(embedding, list):
                                embedding = list(embedding)

                        articles.append({
                            'id': row[0],
                            'title': row[1],
                            'embedding': embedding,
                            'entities': row[3] or {},
                            'category': row[4],
                            'published_date': row[5],
                            'source': row[6]
                        })

                    logger.info(f"✓ Loaded {len(articles)} articles with embeddings for clustering")
                    return articles

        except Exception as e:
            logger.error(f"Error getting article embeddings: {e}")
            return []

    def get_article_by_link(self, link: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific article by its link.

        Args:
            link: Article link/URL

        Returns:
            Article dictionary if found, None otherwise
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT
                            id, title, link, published_date, source, category,
                            subcategory, summary, full_text, entities, nlp_metadata,
                            full_text_embedding, ai_analysis
                        FROM articles
                        WHERE link = %s
                        LIMIT 1
                    """, (link,))

                    row = cur.fetchone()
                    if not row:
                        return None

                    return {
                        'id': row[0],
                        'title': row[1],
                        'link': row[2],
                        'published_date': row[3],
                        'source': row[4],
                        'category': row[5],
                        'subcategory': row[6],
                        'summary': row[7],
                        'full_text': row[8],
                        'entities': row[9],
                        'nlp_metadata': row[10],
                        'full_text_embedding': row[11],
                        'ai_analysis': row[12]
                    }

        except Exception as e:
            logger.error(f"Error getting article by link: {e}")
            return None

    def update_article_analysis(self, article_id: int, analysis_data: Dict[str, Any]) -> bool:
        """
        Update the ai_analysis column for an article with structured analysis data.

        Args:
            article_id: Article ID
            analysis_data: Structured analysis dictionary (from IntelligenceReport schema)

        Returns:
            True if successful, False otherwise
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    import json
                    cur.execute("""
                        UPDATE articles
                        SET ai_analysis = %s, updated_at = NOW()
                        WHERE id = %s
                    """, (json.dumps(analysis_data), article_id))

                    conn.commit()
                    return cur.rowcount > 0

        except Exception as e:
            logger.error(f"Error updating article analysis: {e}")
            return False

    def save_report(self, report: Dict[str, Any]) -> Optional[int]:
        """
        Save LLM-generated report to database.

        Args:
            report: Report dictionary from ReportGenerator

        Returns:
            Report ID if saved successfully, None otherwise
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO reports
                        (report_date, model_used, draft_content, status, report_type, metadata, sources)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                    """, (
                        datetime.now().date(),
                        report.get('metadata', {}).get('model_used', 'unknown'),
                        report.get('report_text', ''),
                        'draft',
                        report.get('report_type', 'daily'),  # Default to 'daily' for backward compatibility
                        Json(report.get('metadata', {})),
                        Json(report.get('sources', {}))
                    ))

                    report_id = cur.fetchone()[0]
                    logger.info(f"✓ Report saved to database with ID: {report_id}")
                    return report_id

        except Exception as e:
            logger.error(f"Error saving report: {e}")
            return None

    def get_report(self, report_id: int) -> Optional[Dict[str, Any]]:
        """
        Get report by ID.

        Args:
            report_id: Report ID

        Returns:
            Report dictionary or None if not found
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT id, report_date, generated_at, model_used,
                               draft_content, final_content, status, metadata, sources,
                               human_reviewed_at, human_reviewer
                        FROM reports
                        WHERE id = %s
                    """, (report_id,))

                    row = cur.fetchone()
                    if not row:
                        return None

                    return {
                        'id': row[0],
                        'report_date': row[1],
                        'generated_at': row[2],
                        'model_used': row[3],
                        'draft_content': row[4],
                        'final_content': row[5],
                        'status': row[6],
                        'metadata': row[7],
                        'sources': row[8],
                        'human_reviewed_at': row[9],
                        'human_reviewer': row[10]
                    }

        except Exception as e:
            logger.error(f"Error getting report: {e}")
            return None

    def get_all_reports(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Get all reports ordered by date (most recent first).

        Args:
            limit: Maximum number of reports to return

        Returns:
            List of report dictionaries
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT id, report_date, generated_at, model_used, status,
                               metadata, human_reviewed_at
                        FROM reports
                        ORDER BY report_date DESC, generated_at DESC
                        LIMIT %s
                    """, (limit,))

                    reports = []
                    for row in cur.fetchall():
                        reports.append({
                            'id': row[0],
                            'report_date': row[1],
                            'generated_at': row[2],
                            'model_used': row[3],
                            'status': row[4],
                            'metadata': row[5],
                            'human_reviewed_at': row[6]
                        })

                    return reports

        except Exception as e:
            logger.error(f"Error getting reports: {e}")
            return []

    def get_latest_reports(self, n: int = 5, days_back: int = 14) -> List[Dict[str, Any]]:
        """
        Get the N most recent reports by date, regardless of embedding status.
        Used by Oracle for recency-based retrieval and as fallback when semantic
        search returns few results.
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT id, report_date, draft_content, final_content,
                               status, report_type, metadata,
                               CASE WHEN content_embedding IS NOT NULL THEN true ELSE false END as has_embedding
                        FROM reports
                        WHERE report_date >= NOW() - INTERVAL '%s days'
                        ORDER BY report_date DESC, generated_at DESC
                        LIMIT %s
                    """, (days_back, n))

                    reports = []
                    for row in cur.fetchall():
                        reports.append({
                            'id': row[0],
                            'report_date': row[1],
                            'draft_content': row[2],
                            'final_content': row[3],
                            'status': row[4],
                            'report_type': row[5],
                            'metadata': row[6],
                            'has_embedding': row[7],
                        })
                    return reports
        except Exception as e:
            logger.error(f"Error getting latest reports: {e}")
            return []

    def update_report(
        self,
        report_id: int,
        final_content: str,
        status: str = 'reviewed',
        reviewer: Optional[str] = None
    ) -> bool:
        """
        Update report with human-edited content.

        Args:
            report_id: Report ID
            final_content: Human-edited report text
            status: Report status (reviewed, approved)
            reviewer: Name/email of reviewer

        Returns:
            True if updated successfully, False otherwise
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE reports
                        SET final_content = %s,
                            status = %s,
                            human_reviewed_at = CURRENT_TIMESTAMP,
                            human_reviewer = %s
                        WHERE id = %s
                    """, (final_content, status, reviewer, report_id))

                    logger.info(f"✓ Report {report_id} updated with status: {status}")
                    return True

        except Exception as e:
            logger.error(f"Error updating report: {e}")
            return False

    def save_feedback(
        self,
        report_id: int,
        section_name: Optional[str],
        feedback_type: str,
        original_text: Optional[str] = None,
        corrected_text: Optional[str] = None,
        comment: Optional[str] = None,
        rating: Optional[int] = None
    ) -> Optional[int]:
        """
        Save human feedback for a report section.

        Args:
            report_id: Report ID
            section_name: Section being reviewed (e.g., "Executive Summary")
            feedback_type: Type of feedback (correction, addition, removal, rating)
            original_text: Original LLM text
            corrected_text: Human-corrected text
            comment: Human notes/explanation
            rating: Quality rating 1-5

        Returns:
            Feedback ID if saved successfully, None otherwise
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO report_feedback
                        (report_id, section_name, feedback_type, original_text,
                         corrected_text, comment, rating)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                    """, (
                        report_id, section_name, feedback_type,
                        original_text, corrected_text, comment, rating
                    ))

                    feedback_id = cur.fetchone()[0]
                    logger.debug(f"✓ Feedback saved with ID: {feedback_id}")
                    return feedback_id

        except Exception as e:
            logger.error(f"Error saving feedback: {e}")
            return None

    def upsert_approval_feedback(
        self,
        report_id: int,
        rating: Optional[int] = None,
        comment: Optional[str] = None
    ) -> Optional[int]:
        """
        Insert or update approval feedback for a report.
        If feedback already exists for this report, update it.
        Otherwise, create new feedback.

        Args:
            report_id: Report ID
            rating: Quality rating 1-5
            comment: Human notes/explanation

        Returns:
            Feedback ID if saved successfully, None otherwise
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    # Use PostgreSQL's ON CONFLICT to upsert
                    # First, check if feedback exists
                    cur.execute("""
                        SELECT id FROM report_feedback
                        WHERE report_id = %s AND feedback_type = 'rating'
                        LIMIT 1
                    """, (report_id,))

                    existing = cur.fetchone()

                    if existing:
                        # Update existing feedback
                        cur.execute("""
                            UPDATE report_feedback
                            SET rating = %s,
                                comment = %s,
                                created_at = CURRENT_TIMESTAMP
                            WHERE id = %s
                            RETURNING id
                        """, (rating, comment, existing[0]))
                        feedback_id = cur.fetchone()[0]
                        logger.debug(f"✓ Feedback updated with ID: {feedback_id}")
                    else:
                        # Insert new feedback
                        cur.execute("""
                            INSERT INTO report_feedback
                            (report_id, section_name, feedback_type, rating, comment)
                            VALUES (%s, NULL, 'rating', %s, %s)
                            RETURNING id
                        """, (report_id, rating, comment))
                        feedback_id = cur.fetchone()[0]
                        logger.debug(f"✓ Feedback created with ID: {feedback_id}")

                    return feedback_id

        except Exception as e:
            logger.error(f"Error upserting approval feedback: {e}")
            return None

    def get_report_feedback(self, report_id: int) -> List[Dict[str, Any]]:
        """
        Get all feedback for a report.

        Args:
            report_id: Report ID

        Returns:
            List of feedback dictionaries
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT id, section_name, feedback_type, original_text,
                               corrected_text, comment, rating, created_at
                        FROM report_feedback
                        WHERE report_id = %s
                        ORDER BY created_at ASC
                    """, (report_id,))

                    feedback = []
                    for row in cur.fetchall():
                        feedback.append({
                            'id': row[0],
                            'section_name': row[1],
                            'feedback_type': row[2],
                            'original_text': row[3],
                            'corrected_text': row[4],
                            'comment': row[5],
                            'rating': row[6],
                            'created_at': row[7]
                        })

                    return feedback

        except Exception as e:
            logger.error(f"Error getting feedback: {e}")
            return []

    def get_reports_by_date_range(
        self,
        start_date: datetime.date,
        end_date: datetime.date,
        status_filter: Optional[str] = None,
        report_type: str = 'daily'
    ) -> List[Dict[str, Any]]:
        """
        Get reports within a date range for meta-analysis.

        Implements priority logic: approved > draft for each date.

        Args:
            start_date: Start date (inclusive)
            end_date: End date (inclusive)
            status_filter: Optional status filter ('approved', 'draft', None for priority logic)
            report_type: Report type filter (default: 'daily')

        Returns:
            List of report dictionaries with full content and metadata
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    # Priority logic: For each date, prefer approved over draft
                    if status_filter is None:
                        query = """
                            WITH ranked_reports AS (
                                SELECT
                                    id, report_date, generated_at, model_used,
                                    draft_content, final_content, status, report_type,
                                    metadata, sources, human_reviewed_at, human_reviewer,
                                    ROW_NUMBER() OVER (
                                        PARTITION BY report_date
                                        ORDER BY
                                            CASE WHEN status = 'approved' THEN 1
                                                 WHEN status = 'reviewed' THEN 2
                                                 ELSE 3
                                            END,
                                            generated_at DESC
                                    ) as rn
                                FROM reports
                                WHERE report_date BETWEEN %s AND %s
                                  AND report_type = %s
                            )
                            SELECT
                                id, report_date, generated_at, model_used,
                                draft_content, final_content, status, report_type,
                                metadata, sources, human_reviewed_at, human_reviewer
                            FROM ranked_reports
                            WHERE rn = 1
                            ORDER BY report_date ASC
                        """
                        params = (start_date, end_date, report_type)
                    else:
                        # Simple filter by status
                        query = """
                            SELECT
                                id, report_date, generated_at, model_used,
                                draft_content, final_content, status, report_type,
                                metadata, sources, human_reviewed_at, human_reviewer
                            FROM reports
                            WHERE report_date BETWEEN %s AND %s
                              AND status = %s
                              AND report_type = %s
                            ORDER BY report_date ASC
                        """
                        params = (start_date, end_date, status_filter, report_type)

                    cur.execute(query, params)

                    reports = []
                    for row in cur.fetchall():
                        reports.append({
                            'id': row[0],
                            'report_date': row[1],
                            'generated_at': row[2],
                            'model_used': row[3],
                            'draft_content': row[4],
                            'final_content': row[5],
                            'status': row[6],
                            'report_type': row[7],
                            'metadata': row[8],
                            'sources': row[9],
                            'human_reviewed_at': row[10],
                            'human_reviewer': row[11]
                        })

                    logger.info(f"✓ Found {len(reports)} {report_type} reports from {start_date} to {end_date}")
                    return reports

        except Exception as e:
            logger.error(f"Error getting reports by date range: {e}")
            return []

    def get_weekly_reports_by_date_range(
        self,
        start_date: datetime.date,
        end_date: datetime.date
    ) -> List[Dict[str, Any]]:
        """
        Get ONLY weekly reports within a date range.

        Weekly reports are identified by having 'reports_count' in metadata,
        which distinguishes them from daily reports (which have 'days_covered').

        Args:
            start_date: Start date (inclusive)
            end_date: End date (inclusive)

        Returns:
            List of weekly report dictionaries
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    query = """
                        SELECT
                            id, report_date, generated_at, model_used,
                            draft_content, final_content, status, report_type,
                            metadata, sources, human_reviewed_at, human_reviewer
                        FROM reports
                        WHERE report_date BETWEEN %s AND %s
                          AND metadata->>'reports_count' IS NOT NULL
                        ORDER BY report_date ASC
                    """

                    cur.execute(query, (start_date, end_date))

                    reports = []
                    for row in cur.fetchall():
                        reports.append({
                            'id': row[0],
                            'report_date': row[1],
                            'generated_at': row[2],
                            'model_used': row[3],
                            'draft_content': row[4],
                            'final_content': row[5],
                            'status': row[6],
                            'report_type': row[7],
                            'metadata': row[8],
                            'sources': row[9],
                            'human_reviewed_at': row[10],
                            'human_reviewer': row[11]
                        })

                    logger.info(f"✓ Found {len(reports)} weekly reports from {start_date} to {end_date}")
                    return reports

        except Exception as e:
            logger.error(f"Error getting weekly reports by date range: {e}")
            return []

    def get_recent_feedback(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get recent feedback across all reports.

        Args:
            limit: Maximum number of feedback entries to return

        Returns:
            List of feedback dictionaries with report info
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT
                            rf.id, rf.report_id, rf.rating, rf.comment, rf.created_at,
                            r.report_date, r.human_reviewer
                        FROM report_feedback rf
                        JOIN reports r ON rf.report_id = r.id
                        WHERE rf.feedback_type = 'rating'
                        ORDER BY rf.created_at DESC
                        LIMIT %s
                    """, (limit,))

                    feedback = []
                    for row in cur.fetchall():
                        feedback.append({
                            'id': row[0],
                            'report_id': row[1],
                            'rating': row[2],
                            'comment': row[3],
                            'created_at': row[4],
                            'report_date': row[5],
                            'reviewer': row[6]
                        })

                    return feedback

        except Exception as e:
            logger.error(f"Error getting recent feedback: {e}")
            return []

    def close(self):
        """Close all connections in the pool."""
        if hasattr(self, 'pool'):
            self.pool.closeall()
            logger.info("Database connection pool closed")


    # ===================================================================
    # Entity Management Methods (for Intelligence Map)
    # ===================================================================

    def save_entity(
        self,
        name: str,
        entity_type: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[int]:
        """
        Save or update an entity.
        
        Args:
            name: Entity name
            entity_type: Entity type (PERSON, ORG, GPE, LOC, etc.)
            metadata: Optional metadata dictionary
        
        Returns:
            Entity ID if saved successfully, None otherwise
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO entities (name, entity_type, metadata)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (name, entity_type) 
                        DO UPDATE SET
                            mention_count = entities.mention_count + 1,
                            last_seen = CURRENT_TIMESTAMP,
                            metadata = COALESCE(EXCLUDED.metadata, entities.metadata)
                        RETURNING id
                    """, (name, entity_type, Json(metadata or {})))
                    
                    entity_id = cur.fetchone()[0]
                    return entity_id
        
        except Exception as e:
            logger.error(f"Error saving entity: {e}")
            return None

    def update_entity_coordinates(
        self,
        entity_id: int,
        latitude: float,
        longitude: float,
        status: str = 'FOUND'
    ) -> bool:
        """
        Update entity with geographic coordinates.
        
        Args:
            entity_id: Entity ID
            latitude: Latitude (-90 to 90)
            longitude: Longitude (-180 to 180)
            status: Geocoding status (FOUND, NOT_FOUND, RETRY)
        
        Returns:
            True if updated successfully, False otherwise
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE entities
                        SET latitude = %s,
                            longitude = %s,
                            geo_status = %s,
                            geocoded_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (latitude, longitude, status, entity_id))
                    
                    return True
        
        except Exception as e:
            logger.error(f"Error updating entity coordinates: {e}")
            return False

    def get_entities_with_coordinates(
        self,
        limit: int = 1000
    ) -> Dict[str, Any]:
        """
        Get entities with coordinates in GeoJSON format for map display.
        
        Args:
            limit: Maximum number of entities to return
        
        Returns:
            GeoJSON FeatureCollection
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT 
                            id, name, entity_type, latitude, longitude,
                            mention_count, metadata
                        FROM entities
                        WHERE latitude IS NOT NULL 
                          AND longitude IS NOT NULL
                          AND geo_status = 'FOUND'
                        ORDER BY mention_count DESC
                        LIMIT %s
                    """, (limit,))
                    
                    features = []
                    for row in cur.fetchall():
                        features.append({
                            'type': 'Feature',
                            'geometry': {
                                'type': 'Point',
                                'coordinates': [float(row[4]), float(row[3])]  # [lng, lat]
                            },
                            'properties': {
                                'id': row[0],
                                'name': row[1],
                                'entity_type': row[2],
                                'mention_count': row[5],
                                'metadata': row[6]
                            }
                        })
                    
                    return {
                        'type': 'FeatureCollection',
                        'features': features
                    }
        
        except Exception as e:
            logger.error(f"Error getting entities with coordinates: {e}")
            return {'type': 'FeatureCollection', 'features': []}

    def get_entities_for_map(
        self,
        limit: int = 5000,
        entity_types: Optional[List[str]] = None,
        days: Optional[int] = None,
        min_mentions: Optional[int] = None,
        min_score: Optional[float] = None,
        search: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get entities with coordinates in GeoJSON format with filtering support.

        This is the primary method for the Intelligence Map. It supports
        filtering by entity_type, recency, significance, and name search.

        Args:
            limit: Maximum number of entities to return
            entity_types: Filter by entity types (GPE, ORG, PERSON, LOC, FAC)
            days: Only entities seen in the last N days
            min_mentions: Minimum mention_count threshold
            min_score: Minimum intelligence_score threshold (0–1)
            search: Case-insensitive name search (ILIKE)

        Returns:
            GeoJSON FeatureCollection with total_count and filtered_count.
            Each feature includes: intelligence_score, storyline_count,
            top_storyline, primary_community_id, hours_ago.
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    # Build dynamic WHERE clause (using e. prefix for the JOIN)
                    conditions = [
                        "e.latitude IS NOT NULL",
                        "e.longitude IS NOT NULL",
                        "e.geo_status = 'FOUND'",
                    ]
                    params: list = []

                    if entity_types:
                        conditions.append("e.entity_type = ANY(%s)")
                        params.append(entity_types)

                    if days:
                        conditions.append("e.last_seen >= NOW() - INTERVAL '%s days'")
                        params.append(days)

                    if min_mentions:
                        conditions.append("e.mention_count >= %s")
                        params.append(min_mentions)

                    if min_score is not None:
                        conditions.append("COALESCE(e.intelligence_score, 0) >= %s")
                        params.append(min_score)

                    if search:
                        conditions.append("e.name ILIKE %s")
                        params.append(f"%{search}%")

                    where_clause = " AND ".join(conditions)

                    # Get total geocoded count (unfiltered) for HUD display
                    cur.execute("""
                        SELECT COUNT(*) FROM entities
                        WHERE latitude IS NOT NULL
                          AND longitude IS NOT NULL
                          AND geo_status = 'FOUND'
                    """)
                    total_count = cur.fetchone()[0]

                    # Get filtered entities with intelligence enrichment
                    query = f"""
                        SELECT
                            e.id, e.name, e.entity_type, e.latitude, e.longitude,
                            e.mention_count, e.metadata, e.first_seen, e.last_seen,
                            COALESCE(e.intelligence_score, 0.0)               AS intelligence_score,
                            COALESCE(esb.storyline_count, 0)                  AS storyline_count,
                            esb.top_storyline,
                            esb.primary_community_id,
                            EXTRACT(EPOCH FROM (
                                NOW() - COALESCE(e.last_seen, e.created_at)
                            )) / 3600.0                                        AS hours_ago
                        FROM entities e
                        LEFT JOIN (
                            SELECT
                                entity_id,
                                COUNT(DISTINCT storyline_id)                                              AS storyline_count,
                                (ARRAY_AGG(storyline_title ORDER BY momentum_score DESC NULLS LAST))[1]  AS top_storyline,
                                (ARRAY_AGG(community_id    ORDER BY momentum_score DESC NULLS LAST))[1]  AS primary_community_id
                            FROM mv_entity_storyline_bridge
                            GROUP BY entity_id
                        ) esb ON esb.entity_id = e.id
                        WHERE {where_clause}
                        ORDER BY e.intelligence_score DESC NULLS LAST, e.mention_count DESC
                        LIMIT %s
                    """
                    params.append(limit)
                    cur.execute(query, params)

                    features = []
                    for row in cur.fetchall():
                        hours_ago_val = row[13]
                        features.append({
                            'type': 'Feature',
                            'geometry': {
                                'type': 'Point',
                                'coordinates': [float(row[4]), float(row[3])]  # [lng, lat]
                            },
                            'properties': {
                                'id': row[0],
                                'name': row[1],
                                'entity_type': row[2],
                                'mention_count': row[5],
                                'metadata': row[6] or {},
                                'first_seen': row[7].isoformat() if row[7] else None,
                                'last_seen': row[8].isoformat() if row[8] else None,
                                'intelligence_score': float(row[9]),
                                'storyline_count': int(row[10]),
                                'top_storyline': row[11],
                                'primary_community_id': row[12],
                                'hours_ago': int(hours_ago_val) if hours_ago_val is not None else 9999,
                            }
                        })

                    return {
                        'type': 'FeatureCollection',
                        'features': features,
                        'total_count': total_count,
                        'filtered_count': len(features),
                    }

        except Exception as e:
            logger.error(f"Error getting entities for map: {e}")
            return {
                'type': 'FeatureCollection',
                'features': [],
                'total_count': 0,
                'filtered_count': 0,
            }

    def get_entity_detail_with_storylines(
        self,
        entity_id: int,
    ) -> Optional[Dict[str, Any]]:
        """
        Get full entity detail including related articles AND related storylines.

        The storyline connection traverses the 4-hop join path:
        entities → entity_mentions → articles → article_storylines → storylines

        Args:
            entity_id: Entity ID

        Returns:
            Entity dict with related_articles and related_storylines, or None
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    # 1. Get entity base data
                    cur.execute("""
                        SELECT
                            id, name, entity_type, latitude, longitude,
                            mention_count, first_seen, last_seen, metadata
                        FROM entities
                        WHERE id = %s
                    """, (entity_id,))

                    row = cur.fetchone()
                    if not row:
                        return None

                    entity = {
                        'id': row[0],
                        'name': row[1],
                        'entity_type': row[2],
                        'latitude': float(row[3]) if row[3] else None,
                        'longitude': float(row[4]) if row[4] else None,
                        'mention_count': row[5],
                        'first_seen': row[6].isoformat() if row[6] else None,
                        'last_seen': row[7].isoformat() if row[7] else None,
                        'metadata': row[8] or {},
                    }

                    # 2. Get related articles (most recent 15)
                    cur.execute("""
                        SELECT DISTINCT
                            a.id, a.title, a.link, a.published_date, a.source
                        FROM articles a
                        JOIN entity_mentions em ON a.id = em.article_id
                        WHERE em.entity_id = %s
                        ORDER BY a.published_date DESC NULLS LAST
                        LIMIT 15
                    """, (entity_id,))

                    entity['related_articles'] = [
                        {
                            'id': r[0],
                            'title': r[1],
                            'link': r[2],
                            'published_date': r[3].isoformat() if r[3] else None,
                            'source': r[4],
                        }
                        for r in cur.fetchall()
                    ]

                    # 3. Get related storylines via the 4-hop join
                    #    entity → entity_mentions → articles → article_storylines → storylines
                    #    Only active/emerging/stabilized (non-archived)
                    cur.execute("""
                        SELECT DISTINCT
                            s.id, s.title, s.narrative_status,
                            s.momentum_score, s.article_count, s.community_id
                        FROM storylines s
                        JOIN article_storylines ast ON s.id = ast.storyline_id
                        JOIN entity_mentions em ON ast.article_id = em.article_id
                        WHERE em.entity_id = %s
                          AND s.narrative_status IN ('emerging', 'active', 'stabilized')
                        ORDER BY s.momentum_score DESC
                        LIMIT 10
                    """, (entity_id,))

                    entity['related_storylines'] = [
                        {
                            'id': r[0],
                            'title': r[1],
                            'narrative_status': r[2],
                            'momentum_score': float(r[3]) if r[3] else 0.0,
                            'article_count': r[4],
                            'community_id': r[5],
                        }
                        for r in cur.fetchall()
                    ]

                    return entity

        except Exception as e:
            logger.error(f"Error getting entity detail with storylines: {e}")
            return None

    def get_entity_arcs(
        self,
        min_score: float = 0.3,
        limit: int = 300,
    ) -> Dict[str, Any]:
        """
        Get entity pairs that share at least one active storyline, as GeoJSON LineStrings.

        Used by the Intelligence Map arc/connection layer to show entity co-occurrence.
        Only entities with intelligence_score >= min_score are included to avoid clutter.

        Args:
            min_score: Minimum intelligence_score for both endpoints
            limit: Maximum number of arcs to return (ordered by shared storylines desc)

        Returns:
            GeoJSON FeatureCollection of LineString features with properties:
            source_name, target_name, shared_storylines, max_momentum
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT
                            e1.name        AS source_name,
                            e1.longitude   AS source_lng,
                            e1.latitude    AS source_lat,
                            e2.name        AS target_name,
                            e2.longitude   AS target_lng,
                            e2.latitude    AS target_lat,
                            COUNT(DISTINCT b1.storyline_id)  AS shared_storylines,
                            MAX(b1.momentum_score)           AS max_momentum
                        FROM mv_entity_storyline_bridge b1
                        JOIN mv_entity_storyline_bridge b2
                            ON  b1.storyline_id = b2.storyline_id
                            AND b1.entity_id < b2.entity_id
                        JOIN entities e1
                            ON  e1.id = b1.entity_id
                            AND e1.geo_status = 'FOUND'
                            AND e1.latitude IS NOT NULL
                            AND COALESCE(e1.intelligence_score, 0) >= %s
                        JOIN entities e2
                            ON  e2.id = b2.entity_id
                            AND e2.geo_status = 'FOUND'
                            AND e2.latitude IS NOT NULL
                            AND COALESCE(e2.intelligence_score, 0) >= %s
                        GROUP BY
                            e1.name, e1.longitude, e1.latitude,
                            e2.name, e2.longitude, e2.latitude
                        ORDER BY shared_storylines DESC, max_momentum DESC
                        LIMIT %s
                    """, (min_score, min_score, limit))

                    features = []
                    for row in cur.fetchall():
                        (source_name, source_lng, source_lat,
                         target_name, target_lng, target_lat,
                         shared, momentum) = row
                        features.append({
                            'type': 'Feature',
                            'geometry': {
                                'type': 'LineString',
                                'coordinates': [
                                    [float(source_lng), float(source_lat)],
                                    [float(target_lng), float(target_lat)],
                                ],
                            },
                            'properties': {
                                'source_name': source_name,
                                'target_name': target_name,
                                'shared_storylines': shared,
                                'max_momentum': float(momentum) if momentum else 0.0,
                            }
                        })

                    return {
                        'type': 'FeatureCollection',
                        'features': features,
                        'arc_count': len(features),
                    }

        except Exception as e:
            logger.error(f"Error getting entity arcs: {e}")
            return {'type': 'FeatureCollection', 'features': [], 'arc_count': 0}

    def get_entity_ids_by_storyline(self, storyline_id: int) -> Dict[str, Any]:
        """
        Get geocoded entity IDs linked to a specific storyline via mv_entity_storyline_bridge.

        Used by the cross-filter feature (stories graph → intelligence map).

        Returns:
            Dict with storyline_id, storyline_title, entity_ids list, entity_count.
            Returns None if storyline not found.
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    # Get storyline title
                    cur.execute(
                        "SELECT title FROM storylines WHERE id = %s",
                        (storyline_id,)
                    )
                    row = cur.fetchone()
                    if not row:
                        return None

                    storyline_title = row[0]

                    # Get entity IDs via bridge MV
                    cur.execute("""
                        SELECT DISTINCT e.id
                        FROM mv_entity_storyline_bridge b
                        JOIN entities e ON e.id = b.entity_id
                        WHERE b.storyline_id = %s
                          AND e.latitude IS NOT NULL
                          AND e.geo_status = 'FOUND'
                        ORDER BY e.intelligence_score DESC NULLS LAST
                    """, (storyline_id,))

                    entity_ids = [r[0] for r in cur.fetchall()]

                    return {
                        'storyline_id': storyline_id,
                        'storyline_title': storyline_title,
                        'entity_ids': entity_ids,
                        'entity_count': len(entity_ids),
                    }

        except Exception as e:
            logger.error(f"Error getting entities by storyline {storyline_id}: {e}")
            return None

    def get_map_stats(self) -> Dict[str, Any]:
        """
        Get live stats for the Intelligence Map HUD overlay.

        Returns:
            Dict with total_entities, geocoded_entities, active_storylines,
            and entity_types breakdown.
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    # Total entities
                    cur.execute("SELECT COUNT(*) FROM entities")
                    total = cur.fetchone()[0]

                    # Geocoded entities
                    cur.execute("""
                        SELECT COUNT(*) FROM entities
                        WHERE latitude IS NOT NULL AND geo_status = 'FOUND'
                    """)
                    geocoded = cur.fetchone()[0]

                    # Active storylines
                    cur.execute("""
                        SELECT COUNT(*) FROM storylines
                        WHERE narrative_status IN ('emerging', 'active', 'stabilized')
                    """)
                    active_storylines = cur.fetchone()[0]

                    # Entity type breakdown (geocoded only)
                    cur.execute("""
                        SELECT entity_type, COUNT(*)
                        FROM entities
                        WHERE latitude IS NOT NULL AND geo_status = 'FOUND'
                        GROUP BY entity_type
                        ORDER BY COUNT(*) DESC
                    """)
                    entity_types = {row[0]: row[1] for row in cur.fetchall()}

                    return {
                        'total_entities': total,
                        'geocoded_entities': geocoded,
                        'active_storylines': active_storylines,
                        'entity_types': entity_types,
                    }

        except Exception as e:
            logger.error(f"Error getting map stats: {e}")
            return {
                'total_entities': 0,
                'geocoded_entities': 0,
                'active_storylines': 0,
                'entity_types': {},
            }

    def get_pending_entities(
        self,
        entity_types: Optional[List[str]] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get entities that need geocoding.
        
        Args:
            entity_types: List of entity types to filter (None = all)
            limit: Maximum number of entities to return
        
        Returns:
            List of entity dictionaries
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    if entity_types:
                        cur.execute("""
                            SELECT id, name, entity_type, mention_count
                            FROM entities
                            WHERE geo_status = 'PENDING'
                              AND entity_type = ANY(%s)
                            ORDER BY mention_count DESC
                            LIMIT %s
                        """, (entity_types, limit))
                    else:
                        cur.execute("""
                            SELECT id, name, entity_type, mention_count
                            FROM entities
                            WHERE geo_status = 'PENDING'
                            ORDER BY mention_count DESC
                            LIMIT %s
                        """, (limit,))
                    
                    entities = []
                    for row in cur.fetchall():
                        entities.append({
                            'id': row[0],
                            'name': row[1],
                            'entity_type': row[2],
                            'mention_count': row[3]
                        })
                    
                    return entities
        
        except Exception as e:
            logger.error(f"Error getting pending entities: {e}")
            return []


    # ===================================================================
    # Report Embedding Methods (for The Oracle RAG)
    # ===================================================================

    def update_report_embedding(
        self,
        report_id: int,
        embedding: List[float]
    ) -> bool:
        """
        Update report with embedding vector for semantic search.

        Args:
            report_id: Report ID
            embedding: Embedding vector (384 dimensions)

        Returns:
            True if updated successfully, False otherwise
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE reports
                        SET content_embedding = %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (embedding, report_id))

                    if cur.rowcount > 0:
                        logger.debug(f"Updated embedding for report {report_id}")
                        return True
                    return False

        except Exception as e:
            logger.error(f"Error updating report embedding: {e}")
            return False

    def semantic_search_reports(
        self,
        query_embedding: List[float],
        top_k: int = 5,
        min_similarity: float = 0.3,
        # NEW FILTERS for enhanced search
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        status: Optional[str] = None,
        report_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Search reports by semantic similarity using vector search with optional filters.

        Args:
            query_embedding: Query embedding vector (384 dimensions)
            top_k: Maximum number of results to return
            min_similarity: Minimum similarity threshold (0-1)
            start_date: Filter reports after this date
            end_date: Filter reports before this date
            status: Filter by report status ('draft', 'reviewed', 'approved')
            report_type: Filter by report type ('daily', 'weekly')

        Returns:
            List of matching report dictionaries with similarity scores
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    query = """
                        SELECT
                            id,
                            report_date,
                            draft_content,
                            final_content,
                            status,
                            report_type,
                            metadata,
                            1 - (content_embedding <=> %s::vector) as similarity
                        FROM reports
                        WHERE content_embedding IS NOT NULL
                    """

                    params = [query_embedding]

                    # Date range filters
                    if start_date:
                        query += " AND report_date >= %s"
                        params.append(start_date)

                    if end_date:
                        query += " AND report_date <= %s"
                        params.append(end_date)

                    # Status filter
                    if status:
                        query += " AND status = %s"
                        params.append(status)

                    # Report type filter
                    if report_type:
                        query += " AND report_type = %s"
                        params.append(report_type)

                    query += " ORDER BY content_embedding <=> %s::vector LIMIT %s"
                    params.extend([query_embedding, top_k])

                    cur.execute(query, params)

                    results = []
                    for row in cur.fetchall():
                        similarity = float(row[7])
                        if similarity >= min_similarity:
                            results.append({
                                'id': row[0],
                                'report_date': row[1],
                                'draft_content': row[2],
                                'final_content': row[3],
                                'status': row[4],
                                'report_type': row[5],
                                'metadata': row[6],
                                'similarity': similarity
                            })

                    return results

        except Exception as e:
            logger.error(f"Semantic search reports error: {e}")
            return []

    def get_reports_without_embeddings(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get reports that don't have embeddings yet (for backfill).

        Args:
            limit: Maximum number of reports to return

        Returns:
            List of report dictionaries without embeddings
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT
                            id,
                            report_date,
                            draft_content,
                            final_content,
                            status
                        FROM reports
                        WHERE content_embedding IS NULL
                        ORDER BY report_date DESC
                        LIMIT %s
                    """, (limit,))

                    reports = []
                    for row in cur.fetchall():
                        reports.append({
                            'id': row[0],
                            'report_date': row[1],
                            'draft_content': row[2],
                            'final_content': row[3],
                            'status': row[4]
                        })

                    return reports

        except Exception as e:
            logger.error(f"Error getting reports without embeddings: {e}")
            return []

    # ===================================================================
    # Full-Text Search and Hybrid Search Methods (FASE 3)
    # ===================================================================

    def full_text_search(
        self,
        query: str,
        top_k: int = 20,
        category: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        sources: Optional[List[str]] = None,
        gpe_entities: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Full-text search using PostgreSQL ts_query.

        Requires migration 007 to be applied (adds tsvector columns and GIN indexes).

        Args:
            query: Search keywords (e.g., "Taiwan semiconductor")
            top_k: Max results
            category: Optional category filter
            start_date: Filter articles published after this date
            end_date: Filter articles published before this date
            sources: Filter by article sources
            gpe_entities: Filter by geographic entities (GPE)

        Returns:
            List of chunks with ts_rank scores

        Example:
            >>> results = db.full_text_search("Taiwan semiconductor", top_k=10)
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    # Build dynamic WHERE clause
                    where_clauses = ["1=1"]
                    params = [query]  # First param for ts_query

                    # Category filter
                    if category:
                        where_clauses.append("a.category = %s")
                        params.append(category)

                    # Date range filters
                    if start_date:
                        where_clauses.append("a.published_date >= %s")
                        params.append(start_date)

                    if end_date:
                        where_clauses.append("a.published_date <= %s")
                        params.append(end_date)

                    # Source filter
                    if sources:
                        where_clauses.append("a.source = ANY(%s)")
                        params.append(sources)

                    # Geographic filtering via GPE entities
                    if gpe_entities:
                        where_clauses.append("""
                            EXISTS (
                                SELECT 1 FROM jsonb_array_elements_text(a.entities->'by_type'->'GPE') gpe
                                WHERE gpe = ANY(%s)
                            )
                        """)
                        params.append(gpe_entities)

                    where_sql = " AND ".join(where_clauses)

                    # FTS query with ts_rank for scoring
                    # websearch_to_tsquery supports natural query syntax ("Taiwan AND semiconductor")
                    query_sql = f"""
                        SELECT
                            c.id as chunk_id,
                            c.content,
                            c.chunk_index,
                            c.word_count,
                            a.id as article_id,
                            a.title,
                            a.link,
                            a.source,
                            a.published_date,
                            a.category,
                            ts_rank(c.content_tsv, websearch_to_tsquery('multilingual', %s)) as fts_score
                        FROM chunks c
                        JOIN articles a ON c.article_id = a.id
                        WHERE {where_sql}
                          AND c.content_tsv @@ websearch_to_tsquery('multilingual', %s)
                        ORDER BY fts_score DESC
                        LIMIT %s
                    """

                    params.extend([query, top_k])
                    cur.execute(query_sql, params)

                    results = []
                    for row in cur.fetchall():
                        results.append({
                            'chunk_id': row[0],
                            'content': row[1],
                            'chunk_index': row[2],
                            'word_count': row[3],
                            'article_id': row[4],
                            'title': row[5],
                            'link': row[6],
                            'source': row[7],
                            'published_date': row[8],
                            'category': row[9],
                            'fts_score': float(row[10])
                        })

                    return results

        except Exception as e:
            logger.error(f"Full-text search error: {e}")
            # If migration not applied, return empty list
            if "column" in str(e) and "content_tsv" in str(e):
                logger.warning("Migration 007 not applied. Run: psql -d intelligence_ita -f migrations/007_add_full_text_search.sql")
            return []

    def hybrid_search(
        self,
        query: str,
        query_embedding: List[float],
        top_k: int = 10,
        vector_top_k: int = 50,
        keyword_top_k: int = 50,
        fusion_method: str = "rrf",
        vector_weight: float = 0.6,
        keyword_weight: float = 0.4,
        **filters
    ) -> List[Dict[str, Any]]:
        """
        Hybrid search combining vector similarity + keyword relevance.

        Uses Reciprocal Rank Fusion (RRF) or weighted score combination.

        Args:
            query: Search query string
            query_embedding: Query embedding vector
            top_k: Final number of results
            vector_top_k: Over-fetch for vector search (default 50)
            keyword_top_k: Over-fetch for keyword search (default 50)
            fusion_method: "rrf" (Reciprocal Rank Fusion) or "weighted"
            vector_weight: Weight for vector scores (if weighted fusion)
            keyword_weight: Weight for keyword scores (if weighted fusion)
            **filters: Same filters as semantic_search (category, start_date, etc.)

        Returns:
            List of fused and re-ranked chunks

        Example:
            >>> results = db.hybrid_search(
            ...     query="Taiwan semiconductor",
            ...     query_embedding=embedding,
            ...     top_k=10,
            ...     category="tech_economy"
            ... )
        """
        # 1. Run both searches in parallel (over-fetch)
        vector_results = self.semantic_search(
            query_embedding=query_embedding,
            top_k=vector_top_k,
            **filters
        )

        keyword_results = self.full_text_search(
            query=query,
            top_k=keyword_top_k,
            **filters
        )

        # 2. Fuse scores
        if fusion_method == "rrf":
            fused = self._reciprocal_rank_fusion(vector_results, keyword_results)
        else:  # weighted
            fused = self._weighted_fusion(
                vector_results, keyword_results,
                vector_weight, keyword_weight
            )

        # 3. Return top-k
        return fused[:top_k]

    def _reciprocal_rank_fusion(
        self,
        vector_results: List[Dict],
        keyword_results: List[Dict],
        k: int = 60
    ) -> List[Dict[str, Any]]:
        """
        Reciprocal Rank Fusion (RRF) for score-free result fusion.

        RRF formula: score = sum(1 / (k + rank_i))

        Advantages:
        - No score normalization needed
        - Robust to score distribution differences
        - Simple and effective

        Args:
            vector_results: Results from vector search
            keyword_results: Results from keyword search
            k: RRF constant (default 60, standard value from literature)

        Returns:
            Fused and sorted results
        """
        scores = {}

        # Score from vector search (by rank)
        for rank, result in enumerate(vector_results, 1):
            chunk_id = result['chunk_id']
            scores[chunk_id] = scores.get(chunk_id, 0) + 1 / (k + rank)

        # Score from keyword search (by rank)
        for rank, result in enumerate(keyword_results, 1):
            chunk_id = result['chunk_id']
            scores[chunk_id] = scores.get(chunk_id, 0) + 1 / (k + rank)

        # Merge results (use vector_results as base for metadata)
        chunk_map = {r['chunk_id']: r for r in vector_results + keyword_results}

        # Sort by RRF score
        fused = [
            {**chunk_map[chunk_id], 'fusion_score': score}
            for chunk_id, score in sorted(scores.items(), key=lambda x: x[1], reverse=True)
        ]

        return fused

    def _weighted_fusion(
        self,
        vector_results: List[Dict],
        keyword_results: List[Dict],
        vector_weight: float,
        keyword_weight: float
    ) -> List[Dict[str, Any]]:
        """
        Weighted linear combination of normalized scores.

        Formula: final_score = α * norm(vector_score) + β * norm(keyword_score)

        Requires score normalization (min-max scaling).

        Args:
            vector_results: Results from vector search
            keyword_results: Results from keyword search
            vector_weight: Weight for vector scores (alpha)
            keyword_weight: Weight for keyword scores (beta)

        Returns:
            Fused and sorted results
        """
        # Normalize vector scores (similarity: 0-1)
        vector_map = {r['chunk_id']: r for r in vector_results}
        max_vec_sim = max((r.get('similarity', 0) for r in vector_results), default=1.0)

        # Normalize keyword scores (ts_rank: variable range)
        keyword_map = {r['chunk_id']: r for r in keyword_results}
        max_kw_score = max((r.get('fts_score', 0) for r in keyword_results), default=1.0)

        # Compute weighted scores
        all_chunk_ids = set(vector_map.keys()) | set(keyword_map.keys())
        scores = {}

        for chunk_id in all_chunk_ids:
            vec_score = vector_map.get(chunk_id, {}).get('similarity', 0) / max_vec_sim
            kw_score = keyword_map.get(chunk_id, {}).get('fts_score', 0) / max_kw_score

            scores[chunk_id] = (vector_weight * vec_score + keyword_weight * kw_score)

        # Merge and sort
        chunk_map = {**vector_map, **keyword_map}
        fused = [
            {**chunk_map[chunk_id], 'fusion_score': score}
            for chunk_id, score in sorted(scores.items(), key=lambda x: x[1], reverse=True)
        ]

        return fused

    # ===================================================================
    # Oracle 2.0 — Query Logging
    # ===================================================================

    def log_oracle_query(
        self,
        session_id: str,
        query: str,
        intent: str,
        complexity: str,
        tools_used: list,
        execution_time: float,
        success: bool,
        metadata: dict = None,
    ):
        """Insert a record into oracle_query_log. Silently no-ops if table doesn't exist."""
        import json as _json
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO oracle_query_log
                            (session_id, query, intent, complexity, tools_used,
                             execution_time, success, metadata)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            session_id,
                            query,
                            intent,
                            complexity,
                            tools_used,
                            execution_time,
                            success,
                            _json.dumps(metadata or {}),
                        ),
                    )
        except Exception as e:
            # Non-critical — log silently
            logger.debug(f"log_oracle_query failed: {e}")


if __name__ == "__main__":
    # Example usage
    db = DatabaseManager()
    db.init_db()
    print("Database initialized successfully!")

    stats = db.get_statistics()
    print("\nDatabase Statistics:")
    print(f"  Total articles: {stats.get('total_articles', 0)}")
    print(f"  Total chunks: {stats.get('total_chunks', 0)}")
    print(f"  Recent articles (7 days): {stats.get('recent_articles', 0)}")
