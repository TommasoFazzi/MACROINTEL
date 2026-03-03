"""
LLM Report Generator with RAG

Generates daily intelligence reports using:
- Recent articles from database (last 24h)
- Historical context from semantic search (RAG)
- Google Gemini LLM for report generation
"""

import os
import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional

import numpy as np
import google.generativeai as genai
from pydantic import ValidationError

from ..storage.database import DatabaseManager
from ..nlp.processing import NLPProcessor
from ..utils.logger import get_logger
from .schemas import IntelligenceReportMVP, IntelligenceReport, MacroAnalysisResult, MacroDashboardItem

# Financial Intelligence v2 - Lazy import to avoid circular dependencies
def get_valuation_engine():
    """Lazy load ValuationEngine for signal enrichment."""
    try:
        from ..finance.validator import ValuationEngine
        return ValuationEngine
    except ImportError:
        logger.warning("Finance module not available - signals will not be enriched")
        return None

def get_signal_enricher():
    """Lazy load signal enrichment function."""
    try:
        from ..finance.scoring import enrich_signal_with_intelligence
        return enrich_signal_with_intelligence
    except ImportError:
        return None

# Lazy import for OpenBB integration
def get_openbb_service():
    """Lazy load OpenBB service to avoid circular imports."""
    try:
        from ..integrations.openbb_service import OpenBBMarketService
        return OpenBBMarketService
    except ImportError:
        return None

logger = get_logger(__name__)


class ReportGenerator:
    """
    Generates intelligence reports using LLM with RAG context.
    """

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        nlp_processor: Optional[NLPProcessor] = None,
        gemini_api_key: Optional[str] = None,
        model_name: str = "gemini-2.5-flash",
        enable_query_expansion: bool = True,
        expansion_variants: int = 2,
        dedup_similarity: float = 0.98,
        enable_reranking: bool = True,
        reranking_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
        reranking_top_k: int = 15
    ):
        """
        Initialize report generator.

        Args:
            db_manager: Database manager instance (creates new if None)
            nlp_processor: NLP processor instance (creates new if None)
            gemini_api_key: Gemini API key (reads from env if None)
            model_name: Gemini model to use
            enable_query_expansion: Enable automatic query expansion for RAG
            expansion_variants: Number of query variants to generate per focus area
            dedup_similarity: Similarity threshold for chunk deduplication (0-1)
            enable_reranking: Enable Cross-Encoder reranking for better precision
            reranking_model: Cross-Encoder model to use for reranking
            reranking_top_k: Number of top chunks to keep after reranking
        """
        self.db = db_manager or DatabaseManager()
        self.nlp = nlp_processor or NLPProcessor(
            spacy_model="xx_ent_wiki_sm",
            embedding_model="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        )

        # Query expansion configuration
        self.enable_query_expansion = enable_query_expansion
        self.expansion_variants = expansion_variants
        self.dedup_similarity = dedup_similarity

        # Reranking configuration
        self.enable_reranking = enable_reranking
        self.reranking_top_k = reranking_top_k

        # Lazy load Cross-Encoder (only if enabled)
        if self.enable_reranking:
            from sentence_transformers import CrossEncoder
            self.reranker = CrossEncoder(reranking_model)
            logger.info(f"  Reranking: ENABLED (model: {reranking_model}, top_k: {reranking_top_k})")
        else:
            self.reranker = None

        # Configure Gemini
        api_key = (gemini_api_key or os.getenv('GEMINI_API_KEY', '')).strip()
        if not api_key:
            raise ValueError("GEMINI_API_KEY not found in environment or parameters")

        genai.configure(api_key=api_key, transport='rest')
        self.model = genai.GenerativeModel(model_name)

        # Load ticker whitelist for Trade Signal context
        self.ticker_whitelist = self._load_ticker_whitelist()
        if self.ticker_whitelist:
            total_tickers = sum(len(tickers) for tickers in self.ticker_whitelist.values())
            logger.info(f"  Ticker whitelist: {total_tickers} tickers loaded across {len(self.ticker_whitelist)} categories")

        # Check hybrid search availability (Migration 007)
        self.hybrid_search_available = self._check_hybrid_search_support()

        logger.info(f"✓ Report generator initialized with {model_name}")
        if enable_query_expansion:
            logger.info(f"  Query expansion: ENABLED ({expansion_variants} variants, dedup threshold: {dedup_similarity})")

    def _load_ticker_whitelist(self) -> Dict[str, List[str]]:
        """
        Load top 50 ticker mappings from config/top_50_tickers.yaml

        Returns:
            Dict with structure: {
                'defense': ['LMT', 'RTX', 'NOC', ...],
                'semiconductors': ['TSM', 'NVDA', 'INTC', ...],
                ...
            }
        """
        import yaml
        from pathlib import Path

        config_path = Path(__file__).parent.parent.parent / 'config' / 'top_50_tickers.yaml'

        if not config_path.exists():
            logger.warning(f"Ticker config not found at {config_path}, using empty whitelist")
            return {}

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                ticker_config = yaml.safe_load(f)

            # Flatten to ticker-only list for prompt context
            tickers_by_category = {}
            all_tickers = []

            for category, companies in ticker_config.items():
                category_tickers = []
                for company in companies:
                    ticker = company['ticker']
                    category_tickers.append(ticker)
                    all_tickers.append(ticker)
                tickers_by_category[category] = category_tickers

            logger.debug(f"Loaded {len(all_tickers)} tickers across {len(tickers_by_category)} categories")
            return tickers_by_category

        except Exception as e:
            logger.error(f"Failed to load ticker whitelist: {e}")
            return {}

    def _check_hybrid_search_support(self) -> bool:
        """
        Check if hybrid search is available (requires Migration 007).
        Cache result to avoid repeated warning logs.

        Returns:
            True if hybrid_search() can be used, False otherwise
        """
        try:
            # Test query with minimal overhead
            self.db.hybrid_search(
                query="test",
                query_embedding=[0.0] * 384,
                top_k=1,
                vector_top_k=1,
                keyword_top_k=1
            )
            logger.info("  Hybrid search (BM25 + vector): AVAILABLE")
            return True
        except Exception:
            logger.warning("  Hybrid search: UNAVAILABLE (Migration 007 not applied) — using semantic_search fallback")
            return False

    def _format_ticker_whitelist(self) -> str:
        """
        Format ticker whitelist for prompt context

        Returns:
            Formatted string with tickers organized by category
        """
        if not self.ticker_whitelist:
            return "No ticker whitelist loaded"

        lines = []
        for category, tickers in self.ticker_whitelist.items():
            category_name = category.replace('_', ' ').title()
            lines.append(f"- {category_name}: {', '.join(tickers)}")

        return "\n".join(lines)

    def get_rag_context(
        self,
        query: str,
        top_k: int = 10,
        category: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get relevant historical context using RAG (semantic search).

        Args:
            query: Search query (e.g., "cybersecurity threats in Asia")
            top_k: Number of relevant chunks to retrieve
            category: Optional category filter

        Returns:
            List of relevant chunks with metadata
        """
        logger.info(f"Searching for RAG context: '{query}'")

        # Generate embedding for query
        query_embedding = self.nlp.embedding_model.encode(query).tolist()

        # Hybrid search (vector + BM25 RRF) — cached availability check
        if self.hybrid_search_available:
            results = self.db.hybrid_search(
                query=query,
                query_embedding=query_embedding,
                top_k=top_k,
                vector_top_k=top_k * 3,
                keyword_top_k=top_k * 3,
                fusion_method="rrf",
            )
        else:
            # Fallback to semantic search (Migration 007 not applied)
            results = self.db.semantic_search(
                query_embedding=query_embedding,
                top_k=top_k,
                category=category,
            )

        logger.info(f"✓ Found {len(results)} relevant chunks (similarity threshold applied)")
        return results

    def expand_rag_queries(self, queries: List[str]) -> List[str]:
        """
        Expand RAG queries using LLM to generate semantic variants.

        For each query, generates N variant sub-queries exploring different angles
        (economic, geopolitical, technological, etc.) to improve retrieval coverage.

        Args:
            queries: Original list of focus area queries

        Returns:
            Expanded list containing original queries + valid variants
        """
        if not self.enable_query_expansion:
            logger.info("Query expansion disabled - using original queries")
            return queries

        logger.info(f"Expanding {len(queries)} queries into {self.expansion_variants} variants each")
        expanded = []

        for query in queries:
            # Always include original query
            expanded.append(query)

            try:
                # Generate variant queries with Gemini Flash
                prompt = f"""Generate {self.expansion_variants} semantic variants of this intelligence query.

Original Query: "{query}"

Create {self.expansion_variants} alternative phrasings that explore different angles (economic impact, geopolitical implications, technological aspects, etc.) while maintaining the core intelligence focus.

Requirements:
- Each variant must be 5-15 words
- Must be related to: {query}
- Different perspective/angle from original
- Suitable for semantic search

Output ONLY the {self.expansion_variants} variant queries, one per line, without numbering or additional text."""

                response = self.model.generate_content(prompt)
                variants = response.text.strip().split('\n')

                # Filter and validate variants
                valid_variants = []
                for variant in variants:
                    variant = variant.strip()
                    # Remove numbering if present
                    if variant and variant[0].isdigit():
                        variant = variant.split('.', 1)[-1].strip()

                    # Validate length (5-15 words)
                    word_count = len(variant.split())
                    if 5 <= word_count <= 15 and variant.lower() != query.lower():
                        valid_variants.append(variant)

                # Limit to requested number of variants
                valid_variants = valid_variants[:self.expansion_variants]

                expanded.extend(valid_variants)
                logger.info(f"  '{query}' → +{len(valid_variants)} variants")

            except Exception as e:
                logger.warning(f"Query expansion failed for '{query}': {e} - using original only")
                continue

        logger.info(f"✓ Query expansion: {len(queries)} → {len(expanded)} total queries")
        return expanded

    def deduplicate_chunks_advanced(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Advanced deduplication using embedding similarity.

        Removes duplicate chunks based on:
        1. Exact chunk_id duplicates
        2. High embedding similarity (cosine > threshold)

        Args:
            chunks: List of chunk dictionaries with 'chunk_id' and embeddings

        Returns:
            Deduplicated list of chunks
        """
        if not chunks:
            return []

        logger.info(f"Deduplicating {len(chunks)} chunks (threshold: {self.dedup_similarity})")

        # Step 1: Remove exact ID duplicates
        seen_ids = set()
        unique_by_id = []
        for chunk in chunks:
            chunk_id = chunk.get('chunk_id')
            if chunk_id not in seen_ids:
                seen_ids.add(chunk_id)
                unique_by_id.append(chunk)

        if len(unique_by_id) < len(chunks):
            logger.info(f"  Removed {len(chunks) - len(unique_by_id)} exact ID duplicates")

        # Step 2: Similarity-based deduplication
        # Get embeddings from database for each chunk
        deduplicated = []
        for i, chunk in enumerate(unique_by_id):
            is_duplicate = False

            # Compare with already accepted chunks
            for accepted_chunk in deduplicated:
                # Calculate cosine similarity between embeddings
                # Note: chunks from DB should have embeddings available
                # If not available in chunk dict, we skip similarity check
                chunk_embedding = chunk.get('embedding')
                accepted_embedding = accepted_chunk.get('embedding')

                if chunk_embedding is not None and accepted_embedding is not None:
                    # Convert to numpy arrays for cosine similarity
                    vec1 = np.array(chunk_embedding)
                    vec2 = np.array(accepted_embedding)

                    # Cosine similarity
                    similarity = np.dot(vec1, vec2) / (np.linalg.norm(vec1) * np.linalg.norm(vec2))

                    if similarity > self.dedup_similarity:
                        is_duplicate = True
                        break

            if not is_duplicate:
                deduplicated.append(chunk)

        similarity_removed = len(unique_by_id) - len(deduplicated)
        if similarity_removed > 0:
            logger.info(f"  Removed {similarity_removed} similar chunks (cosine > {self.dedup_similarity})")

        logger.info(f"✓ Deduplication: {len(chunks)} → {len(deduplicated)} chunks")
        return deduplicated

    def _rerank_chunks(
        self,
        query: str,
        chunks: List[Dict[str, Any]],
        top_k: int
    ) -> List[Dict[str, Any]]:
        """
        Rerank chunks using Cross-Encoder for better precision.

        Uses bi-directional attention to score query-chunk pairs,
        providing more accurate relevance than cosine similarity alone.

        Args:
            query: Original search query
            chunks: List of chunks to rerank (from vector search)
            top_k: Number of top chunks to return

        Returns:
            Top-k reranked chunks with 'rerank_score' added
        """
        if not self.reranker or not chunks:
            return chunks[:top_k]

        logger.info(f"Reranking {len(chunks)} chunks with Cross-Encoder...")

        # Prepare pairs for Cross-Encoder: [(query, chunk_text), ...]
        pairs = []
        for chunk in chunks:
            # Chunks from database have 'content' field, not 'text'
            chunk_text = chunk.get('content', chunk.get('text', ''))
            if not chunk_text:  # Skip empty chunks
                chunk_text = ''
            pairs.append([query, chunk_text])

        # Get reranking scores (batch processing)
        scores = self.reranker.predict(pairs, batch_size=32, show_progress_bar=False)

        # Attach scores to chunks (handle NaN values)
        import math
        for i, chunk in enumerate(chunks):
            score = float(scores[i])
            # Replace NaN with 0.0 (lowest score)
            chunk['rerank_score'] = score if not math.isnan(score) else 0.0

        # Sort by rerank score (descending)
        reranked = sorted(chunks, key=lambda x: x.get('rerank_score', 0.0), reverse=True)

        # Log score distribution
        if reranked:
            top_score = reranked[0].get('rerank_score', 0.0)
            bottom_score = reranked[-1].get('rerank_score', 0.0)
            median_score = reranked[len(reranked)//2].get('rerank_score', 0.0)
            logger.info(
                f"✓ Reranked: scores range [{bottom_score:.3f} - {top_score:.3f}], "
                f"median: {median_score:.3f}"
            )

        return reranked[:top_k]

    def filter_relevant_articles(
        self,
        articles: List[Dict],
        focus_areas: List[str],
        top_n: int = 60,
        min_similarity: float = 0.30,
        min_fallback: int = 10
    ) -> List[Dict]:
        """
        Filter articles by relevance using cosine similarity with quality threshold.

        Implements a two-stage filtering approach:
        1. Quality Gate: Only articles with similarity >= min_similarity
        2. Quantity Limit: Take top N from those that passed quality gate
        3. Safety Net: If fewer than min_fallback articles pass, take top min_fallback regardless

        Args:
            articles: List of articles with embeddings
            focus_areas: List of focus area strings
            top_n: Maximum number of articles to return (default: 60)
            min_similarity: Minimum cosine similarity threshold (default: 0.30)
            min_fallback: Minimum articles to return even if below threshold (default: 10)

        Returns:
            Filtered list of relevant articles (between min_fallback and top_n)
        """
        import numpy as np

        if not articles:
            return []

        logger.info(f"Filtering {len(articles)} articles by relevance to focus areas...")
        logger.info(f"Parameters: top_n={top_n}, min_similarity={min_similarity}, min_fallback={min_fallback}")

        # Generate query embedding from focus areas
        query_text = " ".join(focus_areas)
        query_embedding = self.nlp.embedding_model.encode(query_text)

        # Calculate similarity for each article
        articles_with_similarity = []
        no_embedding_count = 0

        for article in articles:
            # Get article's full text embedding
            full_text_embedding = article.get('full_text_embedding')

            if full_text_embedding is None:
                no_embedding_count += 1
                logger.debug(f"Article '{article.get('title', 'Unknown')}' has no embedding, skipping")
                continue

            # Convert to numpy array if needed
            if isinstance(full_text_embedding, list):
                full_text_embedding = np.array(full_text_embedding)

            # Calculate cosine similarity
            similarity = np.dot(query_embedding, full_text_embedding) / (
                np.linalg.norm(query_embedding) * np.linalg.norm(full_text_embedding)
            )

            articles_with_similarity.append({
                'article': article,
                'similarity': float(similarity)
            })

        if no_embedding_count > 0:
            logger.warning(f"{no_embedding_count} articles had no embeddings and were skipped")

        if not articles_with_similarity:
            logger.error("No articles with embeddings found for filtering")
            return []

        # Sort by similarity (descending)
        articles_with_similarity.sort(key=lambda x: x['similarity'], reverse=True)

        # Log similarity distribution
        similarities = [x['similarity'] for x in articles_with_similarity]
        logger.info(f"Similarity distribution - Min: {min(similarities):.3f}, Max: {max(similarities):.3f}, "
                   f"Mean: {np.mean(similarities):.3f}, Median: {np.median(similarities):.3f}")

        # Stage 1: Filter by quality threshold
        above_threshold = [x for x in articles_with_similarity if x['similarity'] >= min_similarity]
        below_threshold_count = len(articles_with_similarity) - len(above_threshold)

        if below_threshold_count > 0:
            logger.info(f"Filtered out {below_threshold_count} articles below similarity threshold {min_similarity}")

        # Stage 2: Apply quantity limit (with fallback safety net)
        if len(above_threshold) >= min_fallback:
            # Normal path: take top N from articles above threshold
            selected_articles = above_threshold[:top_n]
            logger.info(f"✓ Selected {len(selected_articles)} articles from {len(above_threshold)} above threshold")

            # Warning if we're using fewer articles than expected
            if len(selected_articles) < 30:
                logger.warning(f"⚠ LOW RELEVANCE: Only {len(selected_articles)} articles met quality threshold. "
                             f"This suggests limited relevant news today.")
        else:
            # Fallback path: not enough articles above threshold, take top min_fallback regardless
            selected_articles = articles_with_similarity[:min_fallback]
            logger.warning(f"⚠ FALLBACK MODE ACTIVATED: Only {len(above_threshold)} articles above threshold {min_similarity}. "
                          f"Using emergency fallback: top {min_fallback} articles regardless of quality.")

        # Log final selection details
        if selected_articles:
            similarity_range = f"{selected_articles[-1]['similarity']:.3f} to {selected_articles[0]['similarity']:.3f}"
            avg_similarity = np.mean([x['similarity'] for x in selected_articles])
            logger.info(f"Final selection: {len(selected_articles)} articles "
                       f"(similarity range: {similarity_range}, avg: {avg_similarity:.3f})")

        return [item['article'] for item in selected_articles]

    def format_rag_context(self, rag_results: List[Dict]) -> str:
        """
        Format RAG search results into readable context for LLM.

        Args:
            rag_results: Results from semantic_search

        Returns:
            Formatted string with historical context
        """
        if not rag_results:
            return "No relevant historical context found."

        context_parts = []
        context_parts.append("=== RELEVANT HISTORICAL CONTEXT ===\n")

        for i, result in enumerate(rag_results, 1):
            pub_date = result.get('published_date', 'Unknown date')
            if pub_date and pub_date != 'Unknown date':
                pub_date = pub_date.strftime('%Y-%m-%d') if hasattr(pub_date, 'strftime') else str(pub_date)

            context_parts.append(
                f"\n[{i}] {result['title']}\n"
                f"Source: {result['source']} | Date: {pub_date} | "
                f"Category: {result['category']} | Similarity: {result['similarity']:.3f}\n"
                f"Relevant excerpt:\n{result['content']}\n"
                f"Link: {result['link']}"
            )

        return "\n".join(context_parts)

    def format_recent_articles(self, articles: List[Dict]) -> str:
        """
        Format recent articles for LLM prompt.

        Args:
            articles: List of recent articles from database

        Returns:
            Formatted string with recent news
        """
        if not articles:
            return "No recent articles found."

        formatted_parts = []
        formatted_parts.append("=== TODAY'S NEWS ARTICLES ===\n")

        for i, article in enumerate(articles, 1):
            pub_date = article.get('published_date', 'Unknown date')
            if pub_date and pub_date != 'Unknown date':
                pub_date = pub_date.strftime('%Y-%m-%d %H:%M') if hasattr(pub_date, 'strftime') else str(pub_date)

            entities = article.get('entities', {})
            entity_summary = []
            for entity_type in ['PERSON', 'ORG', 'GPE']:
                if entity_type in entities and entities[entity_type]:
                    top_entities = entities[entity_type][:3]  # Top 3 of each type
                    entity_summary.append(f"{entity_type}: {', '.join(top_entities)}")

            formatted_parts.append(
                f"\n[Article {i}]\n"
                f"Title: {article['title']}\n"
                f"Source: {article['source']} | Date: {pub_date} | Category: {article.get('category', 'N/A')}\n"
                f"Summary: {article.get('summary', 'No summary available')}\n"
            )

            if entity_summary:
                formatted_parts.append(f"Key entities: {' | '.join(entity_summary)}\n")

            # Include full text (truncated if too long)
            full_text = article.get('full_text', '')
            if full_text:
                if len(full_text) > 2000:
                    formatted_parts.append(f"Full text (excerpt): {full_text[:2000]}...\n")
                else:
                    formatted_parts.append(f"Full text: {full_text}\n")

            formatted_parts.append(f"Link: {article['link']}\n")

        return "\n".join(formatted_parts)

    def generate_structured_analysis(
        self,
        article_text: str,
        article_metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Generate structured JSON analysis for a single article (Sprint 2.1 MVP)

        Uses Gemini JSON mode with Pydantic validation for type-safe output.
        This is a NEW method separate from generate_report() to allow isolated testing.

        Args:
            article_text: Full text content of article
            article_metadata: Optional metadata (title, source, date, entities)

        Returns:
            Dictionary with:
            - success: bool (True if validation passed)
            - structured: dict (validated JSON output) if success=True
            - validation_errors: list of errors if success=False
            - raw_llm_output: str (original Gemini response for debugging)
        """
        logger.info("Generating structured analysis with JSON mode...")

        # Prepare metadata context (if provided)
        metadata_context = ""
        if article_metadata:
            metadata_parts = []
            if 'title' in article_metadata:
                metadata_parts.append(f"Title: {article_metadata['title']}")
            if 'source' in article_metadata:
                metadata_parts.append(f"Source: {article_metadata['source']}")
            if 'published_date' in article_metadata:
                metadata_parts.append(f"Date: {article_metadata['published_date']}")
            if 'entities' in article_metadata and article_metadata['entities']:
                # Format entities nicely
                entities = article_metadata['entities']
                if isinstance(entities, dict) and 'by_type' in entities:
                    entities_str = []
                    for etype, names in entities['by_type'].items():
                        if names:
                            entities_str.append(f"{etype}: {', '.join(names[:5])}")
                    metadata_parts.append(f"Key Entities: {' | '.join(entities_str)}")

            if metadata_parts:
                metadata_context = "\n".join(metadata_parts) + "\n\n"

        # Construct system instruction with JSON schema
        system_instruction = """You are a Senior Intelligence Analyst specializing in geopolitical risk assessment and investment implications.

Your task: Analyze the article and provide a structured intelligence assessment in JSON format.

OUTPUT REQUIREMENTS:
- Respond ONLY with valid JSON matching the schema below
- Use BLUF (Bottom Line Up Front) style for executive_summary
- Be concise but substantive (100-300 words for summary)
- Confidence score reflects certainty of your analysis (not article quality)

JSON SCHEMA (all fields REQUIRED):
{
  "title": "string (5-15 words, descriptive article title)",
  "category": "GEOPOLITICS | DEFENSE | ECONOMY | CYBER | ENERGY | OTHER",
  "executive_summary": "string (BLUF-style summary, 100-300 words)",
  "sentiment_label": "POSITIVE | NEUTRAL | NEGATIVE (investment/security outlook)",
  "confidence_score": float (0.0-1.0, your confidence in the assessment)
}

CATEGORY DEFINITIONS:
- GEOPOLITICS: Tensions, alliances, territorial disputes, diplomatic events
- DEFENSE: Military tech, weapons systems, defense spending, armed conflicts
- ECONOMY: Markets, trade, sanctions, economic policy, financial institutions
- CYBER: Cyberattacks, data breaches, espionage, critical infrastructure
- ENERGY: Oil, gas, renewables, OPEC, energy security, pipelines
- OTHER: Does not fit above categories clearly

SENTIMENT GUIDELINES:
- POSITIVE: Events likely to benefit markets, reduce risks, or improve stability
- NEGATIVE: Events increasing risks, market uncertainty, or instability
- NEUTRAL: Informational updates without clear directional impact

CONFIDENCE SCORE:
- 0.9-1.0: High confidence (verified facts, multiple sources)
- 0.7-0.8: Medium confidence (single source, or emerging story)
- 0.5-0.6: Low confidence (rumors, conflicting information, or highly speculative)

EXAMPLE OUTPUT:
{
  "title": "China Deploys Naval Forces Near Taiwan Strait",
  "category": "GEOPOLITICS",
  "executive_summary": "BLUF: China conducted large-scale naval exercises 100km from Taiwan coast on Dec 15, deploying 15 warships including 3 Type 055 destroyers. This represents the largest show of force since August 2024. Taiwan's defense ministry reports no direct incursions into territorial waters but increased surveillance flights. US 7th Fleet is monitoring. Investment implications: Heightened geopolitical risk premium likely for Taiwan-based semiconductor manufacturers (TSMC) and regional defense contractors. Short-term volatility expected in Asia-Pacific equity markets.",
  "sentiment_label": "NEGATIVE",
  "confidence_score": 0.85
}

Now analyze the article below and respond with JSON only:"""

        # User prompt with article content
        user_prompt = f"""Article to analyze:

{metadata_context}{article_text}

Respond with JSON analysis following the schema above:"""

        try:
            # Call Gemini with JSON mode
            response = self.model.generate_content(
                contents=[system_instruction, user_prompt],
                generation_config={
                    "response_mime_type": "application/json",
                    "temperature": 0.3,  # Lower temperature for more consistent JSON
                }
            )

            raw_output = response.text
            logger.debug(f"Raw LLM output: {raw_output[:200]}...")

            # Validate with Pydantic
            try:
                validated_report = IntelligenceReportMVP.model_validate_json(raw_output)
                logger.info("✅ Pydantic validation PASSED")

                return {
                    'success': True,
                    'structured': validated_report.model_dump(),
                    'raw_llm_output': raw_output,
                    'validation_errors': []
                }

            except ValidationError as e:
                logger.warning(f"⚠️ Pydantic validation FAILED: {e}")
                # Fallback: Return raw parsed JSON with errors flagged
                try:
                    raw_json = json.loads(raw_output)
                except json.JSONDecodeError as json_err:
                    raw_json = {"error": "Invalid JSON", "raw": raw_output[:500]}

                return {
                    'success': False,
                    'validation_errors': [str(err) for err in e.errors()],
                    'raw_llm_output': raw_output,
                    'parsed_attempt': raw_json
                }

        except Exception as e:
            logger.error(f"❌ LLM generation failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'validation_errors': [],
                'raw_llm_output': None
            }

    def generate_full_analysis(
        self,
        article_text: str,
        article_metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Generate full schema analysis with Trade Signals (Sprint 2.2)

        Uses Gemini JSON mode with full Pydantic schema validation.
        Includes: Impact Score, Sentiment, Trade Signals, Key Entities, Markdown content.

        Args:
            article_text: Full text content of article
            article_metadata: Optional metadata (title, source, date, entities)

        Returns:
            Dictionary with:
            - success: bool (True if validation passed)
            - structured: dict (validated IntelligenceReport) if success=True
            - validation_errors: list of errors if success=False
            - raw_llm_output: str (original Gemini response for debugging)
        """
        logger.info("Generating FULL schema analysis with Trade Signals...")

        # Prepare metadata context (same as MVP)
        metadata_context = ""
        if article_metadata:
            metadata_parts = []
            if 'title' in article_metadata:
                metadata_parts.append(f"Title: {article_metadata['title']}")
            if 'source' in article_metadata:
                metadata_parts.append(f"Source: {article_metadata['source']}")
            if 'published_date' in article_metadata:
                metadata_parts.append(f"Date: {article_metadata['published_date']}")
            if 'entities' in article_metadata and article_metadata['entities']:
                entities = article_metadata['entities']
                if isinstance(entities, dict) and 'by_type' in entities:
                    entities_str = []
                    for etype, names in entities['by_type'].items():
                        if names:
                            entities_str.append(f"{etype}: {', '.join(names[:5])}")
                    metadata_parts.append(f"Key Entities: {' | '.join(entities_str)}")

            if metadata_parts:
                metadata_context = "\n".join(metadata_parts) + "\n\n"

        # Prepare ticker context from whitelist
        ticker_context = self._format_ticker_whitelist()

        # System instruction with full JSON schema
        system_instruction = f"""You are a Senior Investment Strategist specializing in Geopolitical Risk and Market Intelligence.

TASK: Analyze the article and provide a comprehensive intelligence assessment with ACTIONABLE TRADE SIGNALS.

OUTPUT REQUIREMENTS:
- Respond ONLY with valid JSON matching the schema below
- Use Markdown formatting: **bold** for key entities, [Article N] for citations
- Trade Signals: Only mention tickers if DIRECTLY relevant to article events
- Impact Score: Rate event severity (0=noise, 10=systemic crisis)
- Be concise but substantive (150-400 words for executive_summary)

JSON SCHEMA (all fields REQUIRED):
{{
  "title": "string (5-15 words, descriptive title)",
  "category": "GEOPOLITICS | DEFENSE | ECONOMY | CYBER | ENERGY",
  "impact": {{
    "score": integer (0-10, event severity),
    "reasoning": "string (why this score, 1-2 sentences)"
  }},
  "sentiment": {{
    "label": "POSITIVE | NEUTRAL | NEGATIVE",
    "score": float (-1.0 to +1.0, sentiment polarity)
  }},
  "key_entities": ["string", ...] (top 5-10 organizations, people, locations),
  "related_tickers": [
    {{
      "ticker": "string (e.g., 'LMT', 'TSM')",
      "signal": "BULLISH | BEARISH | NEUTRAL | WATCHLIST",
      "timeframe": "SHORT_TERM | MEDIUM_TERM | LONG_TERM",
      "rationale": "string (specific catalyst, 1-2 sentences)"
    }}
  ],
  "executive_summary": "string (BLUF-style summary with **markdown** formatting)",
  "analysis_content": "string (full markdown analysis with ## headings)",
  "confidence_score": float (0.0-1.0, your confidence in this analysis)
}}

CATEGORY DEFINITIONS:
- GEOPOLITICS: Tensions, alliances, territorial disputes, diplomatic events
- DEFENSE: Military tech, weapons systems, defense spending, armed conflicts
- ECONOMY: Markets, trade, sanctions, economic policy, financial institutions
- CYBER: Cyberattacks, data breaches, espionage, critical infrastructure
- ENERGY: Oil, gas, renewables, OPEC, energy security, pipelines

IMPACT SCORE (0-10):
- 0-2: Noise (routine diplomatic statement, minor local incident)
- 3-4: Noteworthy (significant development, limited geographic scope)
- 5-6: Important (regional crisis, major policy shift)
- 7-8: Critical (high escalation risk, global market impact)
- 9-10: Systemic (war, financial crisis, critical infrastructure failure)

SENTIMENT GUIDELINES:
- POSITIVE (+0.3 to +1.0): Events reducing risks, improving stability, bullish for markets
- NEUTRAL (-0.2 to +0.2): Informational, no clear directional impact
- NEGATIVE (-1.0 to -0.3): Events increasing risks, uncertainty, or instability

TRADE SIGNAL RULES:
1. **Only use tickers from the whitelist below** - DO NOT invent tickers
2. **Timeframe definitions**:
   - SHORT_TERM: <3 months (immediate tactical positioning)
   - MEDIUM_TERM: 3-12 months (quarterly earnings impact)
   - LONG_TERM: >1 year (structural shifts, multi-year trends)
3. **Signal types**:
   - BULLISH: Clear positive catalyst (contracts, earnings, favorable policy)
   - BEARISH: Clear negative catalyst (sanctions, loss of market, regulation)
   - NEUTRAL: No strong directional bias but worth monitoring
   - WATCHLIST: Potential future impact, awaiting catalyst
4. **Rationale must be SPECIFIC**: Include concrete numbers, contract values, dates, causal links

TICKER WHITELIST (ONLY use these):
{ticker_context}

If article does NOT mention any ticker-relevant events, return empty array for related_tickers.

MARKDOWN FORMATTING:
- Use **bold** for: Company names, key people, critical locations
- Use [Article N] format for source citations (if multiple articles)
- Use ## headings for analysis_content sections
- Example: "**Taiwan Semiconductor (TSM)** reported record Q4 earnings [Article 3]..."

CONFIDENCE SCORE:
- 0.9-1.0: High confidence (verified facts, multiple sources, clear causality)
- 0.7-0.8: Medium confidence (single source, emerging story, some uncertainty)
- 0.5-0.6: Low confidence (rumors, conflicting info, speculative analysis)

EXAMPLE OUTPUT:
{{
  "title": "China Naval Exercises Near Taiwan Escalate Tensions",
  "category": "GEOPOLITICS",
  "impact": {{
    "score": 7,
    "reasoning": "Largest PLA Navy deployment since 2024, high risk of miscalculation in contested waters"
  }},
  "sentiment": {{
    "label": "NEGATIVE",
    "score": -0.65
  }},
  "key_entities": ["China", "Taiwan", "US 7th Fleet", "TSMC", "Xi Jinping"],
  "related_tickers": [
    {{
      "ticker": "TSM",
      "signal": "BEARISH",
      "timeframe": "SHORT_TERM",
      "rationale": "Geopolitical risk premium spike; potential supply chain disruption fears impacting semiconductor sector valuations"
    }},
    {{
      "ticker": "LMT",
      "signal": "BULLISH",
      "timeframe": "MEDIUM_TERM",
      "rationale": "Increased demand for Aegis defense systems and F-35 fighter jets from Taiwan and regional allies (Japan, Australia) likely"
    }}
  ],
  "executive_summary": "BLUF: **China's People's Liberation Army Navy** deployed 15 warships including 3 Type 055 destroyers 100km from **Taiwan Strait** on Dec 15, marking the largest show of force since August 2024. **Taiwan's Ministry of Defense** confirmed no territorial water incursions but reported increased surveillance flights. **US 7th Fleet** is monitoring closely. Investment implications: Short-term volatility expected for **Taiwan Semiconductor (TSM)** and Asia-Pacific equities due to heightened geopolitical risk premium. Defense contractors like **Lockheed Martin (LMT)** and **Raytheon (RTX)** positioned to benefit from increased regional procurement.",
  "analysis_content": "## Military Deployment Details\\n\\nChina's naval exercise represents a significant escalation...\\n\\n## Market Impact Analysis\\n\\nTaiwan Semiconductor faces immediate valuation pressure...",
  "confidence_score": 0.85
}}

Now analyze the article below and respond with JSON only:"""

        # User prompt with article content
        user_prompt = f"""Article to analyze:

{metadata_context}{article_text}

Respond with JSON analysis following the full schema above:"""

        try:
            # Call Gemini with JSON mode
            # CRITICAL: Use temperature 0.2 (NOT 0.3) for analytical consistency
            response = self.model.generate_content(
                contents=[system_instruction, user_prompt],
                generation_config={
                    "response_mime_type": "application/json",
                    "temperature": 0.2,  # Lower than MVP for trade signal precision
                    # NO max_output_tokens - let it generate full content
                }
                # Optional: Add timeout if 504 errors occur
                # request_options={"timeout": 600}
            )

            raw_output = response.text
            logger.debug(f"Raw LLM output: {raw_output[:200]}...")

            # Validate with Pydantic (IntelligenceReport full schema)
            try:
                validated_report = IntelligenceReport.model_validate_json(raw_output)
                logger.info("✅ Pydantic validation PASSED (Full Schema)")

                # Log extracted trade signals for visibility
                signals = validated_report.related_tickers
                if signals:
                    logger.info(f"  💰 Trade Signals: {len(signals)} extracted")
                    for sig in signals:
                        logger.info(f"     {sig.ticker}: {sig.signal} ({sig.timeframe})")
                else:
                    logger.info("  ℹ️  No trade signals (article not ticker-relevant)")

                return {
                    'success': True,
                    'structured': validated_report.model_dump(),
                    'raw_llm_output': raw_output,
                    'validation_errors': []
                }

            except ValidationError as e:
                logger.warning(f"⚠️ Pydantic validation FAILED: {e}")
                # Fallback: Return raw parsed JSON with errors flagged
                try:
                    raw_json = json.loads(raw_output)
                except json.JSONDecodeError as json_err:
                    raw_json = {"error": "Invalid JSON", "raw": raw_output[:500]}

                return {
                    'success': False,
                    'validation_errors': [str(err) for err in e.errors()],
                    'raw_llm_output': raw_output,
                    'parsed_attempt': raw_json
                }

        except Exception as e:
            logger.error(f"❌ LLM generation failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'validation_errors': [],
                'raw_llm_output': None
            }

    # ========================================================================
    # MACRO DASHBOARD GENERATION (Two-Step Pipeline)
    # ========================================================================

    def _generate_macro_analysis(
        self,
        macro_context_raw: str,
        target_date
    ) -> Dict[str, Any]:
        """
        Step 1: LLM interprets raw macro data to generate interpretive analysis.

        Analyzes all 16 OpenBB indicators and generates:
        - Dashboard items with interpretive labels (e.g., VIX 14.2 -> "Calm")
        - Risk regime classification (RISK_ON, RISK_OFF, MIXED, TRANSITION)
        - Narrative explaining the macro environment

        Args:
            macro_context_raw: Raw macro data text from OpenBB get_macro_context_text()
            target_date: Date of the data

        Returns:
            Dictionary with:
            - success: bool
            - result: MacroAnalysisResult dict if success
            - error: str if failure
        """
        logger.info("[STEP 0.5] Generating macro interpretation (two-step pipeline)...")

        date_str = target_date.strftime('%Y-%m-%d') if hasattr(target_date, 'strftime') else str(target_date)

        # Construct prompt for macro interpretation
        macro_analysis_prompt = f"""You are a macro strategist interpreting today's market indicators.

RAW DATA ({date_str}):
{macro_context_raw}

TASK: Analyze ALL indicators and generate an interpretive analysis.

=== INTERPRETATION RULES ===

**RATES:**
- 10Y_YIELD up >10bp: "Hawkish Shift" | down >10bp: "Dovish Signal"
- 2Y_YIELD up faster than 10Y: "Flattening/Inversion Risk"
- YIELD_CURVE (10Y-2Y) < 0: "Inverted (Recession Signal)" | > 0.5%: "Healthy Steepening"

**VOLATILITY:**
- VIX < 15: "Calm/Complacent" | 15-20: "Cautious" | 20-30: "Elevated Fear" | >30: "Panic"

**COMMODITIES:**
- Oil (Brent/WTI) up >2%: "Supply Concern / Geopolitical Risk" | down >2%: "Demand Worry"
- GOLD up >1%: "Safe Haven Bid" | down >1%: "Risk-On Rotation"
- COPPER up: "Growth Optimism (Dr. Copper)" | down: "Slowdown Signal"

**FX:**
- DXY up >0.5%: "Risk-Off / Dollar Strength" | down >0.5%: "Risk-On / Dollar Weakness"
- USD/JPY up: "Carry Trade Active" | down sharply: "Risk-Off / Yen Strength"
- EUR/USD: correlate with ECB vs Fed policy divergence

**INDICES:**
- SP500 up >1%: "Risk-On Rally" | down >1%: "Risk-Off Selling"

**CREDIT_RISK:**
- HY_SPREAD widening >20bp: "Credit Stress" | narrowing: "Credit Appetite"
- HY_SPREAD > 5%: "High Stress" | < 3.5%: "Complacent"

**INFLATION:**
- 5Y_INFLATION_EXPECTATION up >5bp: "Inflation Fears Rising"
- 5Y below 2%: "Deflation Concern" | above 2.5%: "Overheating Risk"

**SHIPPING:**
- CASS_FREIGHT falling: "Supply Chain Easing / Demand Slowdown"
- CASS_FREIGHT rising: "Logistics Bottleneck / Demand Recovery"

=== OUTPUT FORMAT (JSON) ===

{{
    "dashboard_items": [
        {{"indicator": "OIL", "value": "$78.50", "change": "-1.2%", "label": "Supply Easing", "emoji": "📉"}},
        {{"indicator": "VIX", "value": "14.2", "change": "+0.5", "label": "Calm", "emoji": "🟢"}},
        {{"indicator": "10Y YIELD", "value": "4.1%", "change": "+5bp", "label": "Hawkish Hold", "emoji": "📊"}},
        {{"indicator": "DXY", "value": "104.2", "change": "-0.3%", "label": "Mild Weakness", "emoji": "💵"}},
        {{"indicator": "HY SPREAD", "value": "3.2%", "change": "flat", "label": "No Stress", "emoji": "✅"}},
        {{"indicator": "COPPER", "value": "$4.15", "change": "+0.8%", "label": "Growth Signal", "emoji": "🏭"}}
    ],
    "risk_regime": "RISK_ON",
    "macro_narrative": "Markets are in a low-volatility, risk-on regime. VIX at 14.2 indicates investor complacency despite Middle East headlines. Oil weakness (-1.2%) suggests demand concerns outweigh supply risks. Copper strength points to intact global growth expectations. Monitor HY spreads for early stress signals.",
    "key_divergences": ["VIX complacent despite elevated geopolitical risk"],
    "watch_items": ["HY spreads", "Yield curve flattening"]
}}

Select 6-8 MOST RELEVANT indicators for today's dashboard.
Focus on what's MOVING and what has INTERPRETIVE significance.

Respond with JSON only:"""

        try:
            response = self.model.generate_content(
                macro_analysis_prompt,
                generation_config={
                    "response_mime_type": "application/json",
                    "temperature": 0.45,  # Higher for interpretive analysis
                }
            )

            raw_output = response.text
            logger.debug(f"Macro analysis raw output: {raw_output[:300]}...")

            # Validate with Pydantic
            try:
                validated_analysis = MacroAnalysisResult.model_validate_json(raw_output)
                logger.info("✅ Macro analysis validation PASSED")

                return {
                    'success': True,
                    'result': validated_analysis.model_dump(),
                    'raw_llm_output': raw_output
                }

            except ValidationError as e:
                logger.warning(f"⚠️ Macro analysis validation FAILED: {e}")
                # Try to parse JSON anyway for partial recovery
                try:
                    raw_json = json.loads(raw_output)
                    return {
                        'success': False,
                        'result': raw_json,  # Partial result
                        'validation_errors': [str(err) for err in e.errors()],
                        'raw_llm_output': raw_output
                    }
                except json.JSONDecodeError:
                    return {
                        'success': False,
                        'error': 'Invalid JSON output',
                        'validation_errors': [str(err) for err in e.errors()],
                        'raw_llm_output': raw_output
                    }

        except Exception as e:
            logger.error(f"❌ Macro analysis generation failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'raw_llm_output': None
            }

    def _format_macro_dashboard(
        self,
        macro_analysis: Dict[str, Any],
        target_date
    ) -> str:
        """
        Format macro analysis into inline dashboard for report header.

        Output format:
        **MACRO DASHBOARD**
        `OIL: $78.50 (📉 Supply Easing)` | `VIX: 14.2 (🟢 Calm)` | `10Y: 4.1%`

        *Risk Regime: RISK_ON*

        [3-4 sentence macro narrative]

        Args:
            macro_analysis: Result from _generate_macro_analysis()
            target_date: Date for header

        Returns:
            Formatted markdown string for report header
        """
        date_str = target_date.strftime('%d/%m/%Y') if hasattr(target_date, 'strftime') else str(target_date)

        items = macro_analysis.get('dashboard_items', [])
        regime = macro_analysis.get('risk_regime', 'MIXED')
        narrative = macro_analysis.get('macro_narrative', '')
        divergences = macro_analysis.get('key_divergences', [])
        watch = macro_analysis.get('watch_items', [])

        # Format each dashboard item as inline code block
        formatted_items = []
        for item in items[:8]:  # Max 8 items
            indicator = item.get('indicator', '')
            value = item.get('value', '')
            change = item.get('change', '')
            label = item.get('label', '')
            emoji = item.get('emoji', '')

            if label:
                formatted_items.append(f"`{indicator}: {value} ({emoji} {label})`")
            else:
                formatted_items.append(f"`{indicator}: {value} ({change})`")

        dashboard_line = " | ".join(formatted_items)

        # Build divergences section if present
        divergence_section = ""
        if divergences:
            divergence_section = f"\n\n**⚠️ Key Divergences:** {', '.join(divergences)}"

        # Build watch items section if present
        watch_section = ""
        if watch:
            watch_section = f"\n\n**👁️ Watch:** {', '.join(watch)}"

        return f"""🌍 **MACRO DASHBOARD** ({date_str})

{dashboard_line}

*Risk Regime: {regime}*

{narrative}{divergence_section}{watch_section}
"""

    # =====================================================================
    # Narrative Storyline Context
    # =====================================================================

    def _get_narrative_context(self, days: int = 1, top_n: int = 10) -> Dict[str, Any]:
        """
        Fetch top storylines, their graph edges, and recent linked articles.

        Returns:
            Dict with 'storylines' list, 'edges' list, or empty if unavailable.
        """
        try:
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    # Top N active storylines by momentum
                    cur.execute("""
                        SELECT id, title, summary, narrative_status,
                               momentum_score, article_count, key_entities,
                               start_date, last_update
                        FROM v_active_storylines
                        LIMIT %s
                    """, [top_n])
                    storyline_rows = cur.fetchall()

                    if not storyline_rows:
                        return {'storylines': [], 'edges': []}

                    storyline_ids = [r[0] for r in storyline_rows]

                    # Edges between these storylines
                    cur.execute("""
                        SELECT source_story_id, target_story_id,
                               source_title, target_title,
                               weight, relation_type
                        FROM v_storyline_graph
                        WHERE source_story_id = ANY(%s)
                          AND target_story_id = ANY(%s)
                    """, [storyline_ids, storyline_ids])
                    edge_rows = cur.fetchall()

                    # Recent articles per storyline (last N days)
                    cur.execute("""
                        SELECT als.storyline_id, a.title, a.source,
                               a.published_date
                        FROM article_storylines als
                        JOIN articles a ON als.article_id = a.id
                        WHERE als.storyline_id = ANY(%s)
                          AND a.published_date >= NOW() - make_interval(days => %s)
                        ORDER BY a.published_date DESC
                    """, [storyline_ids, days])
                    article_rows = cur.fetchall()

            # Group articles by storyline_id
            articles_by_story: Dict[int, list] = {}
            for sid, title, source, pub_date in article_rows:
                articles_by_story.setdefault(sid, []).append({
                    'title': title,
                    'source': source,
                    'date': pub_date.strftime('%Y-%m-%d') if pub_date else '',
                })

            storylines = []
            for i, r in enumerate(storyline_rows):
                entities = r[6] or []
                if isinstance(entities, str):
                    import json as _json
                    try:
                        entities = _json.loads(entities)
                    except Exception:
                        entities = []

                storylines.append({
                    'rank': i + 1,
                    'id': r[0],
                    'title': r[1] or '',
                    'summary': r[2] or '',
                    'status': r[3] or 'active',
                    'momentum': round(r[4] or 0.0, 2),
                    'article_count': r[5] or 0,
                    'entities': entities if isinstance(entities, list) else [],
                    'recent_articles': articles_by_story.get(r[0], [])[:5],
                })

            edges = [
                {
                    'source_id': r[0], 'target_id': r[1],
                    'source_title': r[2], 'target_title': r[3],
                    'weight': round(r[4] or 0.0, 2),
                    'relation_type': r[5] or 'relates_to',
                }
                for r in edge_rows
            ]

            return {'storylines': storylines, 'edges': edges}

        except Exception as e:
            logger.warning(f"Failed to fetch narrative context (non-blocking): {e}")
            return {'storylines': [], 'edges': []}

    def _format_narrative_xml(self, narrative_ctx: Dict[str, Any]) -> str:
        """Format narrative context as structured XML for LLM prompt."""
        storylines = narrative_ctx.get('storylines', [])
        edges = narrative_ctx.get('edges', [])

        if not storylines:
            return ""

        # Build edge lookup: storyline_id -> list of related titles
        edge_lookup: Dict[int, list] = {}
        for e in edges:
            edge_lookup.setdefault(e['source_id'], []).append(
                f'{e["target_title"]}')
            edge_lookup.setdefault(e['target_id'], []).append(
                f'{e["source_title"]}')

        lines = ['<strategic_storylines>']
        for s in storylines:
            lines.append(
                f'  <storyline rank="{s["rank"]}" momentum="{s["momentum"]}" '
                f'status="{s["status"]}" articles="{s["article_count"]}">'
            )
            lines.append(f'    <title>{s["title"]}</title>')
            if s['summary']:
                # Truncate long summaries
                summary = s['summary'][:500]
                lines.append(f'    <summary>{summary}</summary>')
            if s['entities']:
                lines.append(f'    <entities>{", ".join(s["entities"][:10])}</entities>')

            # Recent articles
            if s['recent_articles']:
                lines.append('    <recent_articles>')
                for a in s['recent_articles']:
                    lines.append(
                        f'      <article date="{a["date"]}" source="{a["source"]}">'
                        f'{a["title"]}</article>'
                    )
                lines.append('    </recent_articles>')

            # Related storylines
            related = edge_lookup.get(s['id'], [])
            if related:
                lines.append('    <related_storylines>')
                for rel in related[:5]:
                    lines.append(f'      {rel}')
                lines.append('    </related_storylines>')

            lines.append('  </storyline>')

        lines.append('</strategic_storylines>')
        return '\n'.join(lines)

    def generate_report(
        self,
        focus_areas: Optional[List[str]] = None,
        days: int = 1,
        from_time: Optional[datetime] = None,
        to_time: Optional[datetime] = None,
        rag_queries: Optional[List[str]] = None,
        rag_top_k: int = 5,
        top_articles: int = 60,
        min_similarity: float = 0.30,
        min_fallback: int = 10
    ) -> Dict[str, Any]:
        """
        Generate intelligence report with RAG context.

        Args:
            focus_areas: List of topics to focus on (e.g., ["cybersecurity", "geopolitics"])
            days: Number of days to look back for recent articles (if from_time/to_time not set)
            from_time: Optional start time for explicit time window (takes precedence over days)
            to_time: Optional end time for explicit time window (takes precedence over days)
            rag_queries: Custom RAG search queries. If None, auto-generates from focus areas
            rag_top_k: Number of historical chunks per RAG query
            top_articles: Maximum number of top relevant articles to include (default: 60)
            min_similarity: Minimum cosine similarity threshold for relevance (default: 0.30)
            min_fallback: Minimum articles to return even if below threshold (default: 10)

        Returns:
            Dictionary with report content and metadata
        """
        logger.info("=" * 80)
        logger.info("GENERATING INTELLIGENCE REPORT")
        logger.info("=" * 80)

        # [STEP 0] Fetch macro data from OpenBB (if available)
        macro_context_text = ""
        macro_dashboard_text = ""
        macro_analysis_result = None
        today = None

        OpenBBMarketService = get_openbb_service()
        if OpenBBMarketService:
            try:
                logger.info("\n[STEP 0] Fetching macro economic context...")
                from datetime import date as date_type
                openbb_service = OpenBBMarketService(self.db)
                today = date_type.today()

                # Ensure macro data is available
                openbb_service.ensure_daily_macro_data(today)

                # Get formatted macro context for LLM prompt (raw data)
                macro_context_text = openbb_service.get_macro_context_text(today)
                if macro_context_text:
                    logger.info(f"✓ Macro context loaded ({len(macro_context_text)} chars)")

                    # [STEP 0.5] Generate interpretive macro analysis (two-step pipeline)
                    macro_analysis_result = self._generate_macro_analysis(macro_context_text, today)

                    if macro_analysis_result.get('success') or macro_analysis_result.get('result'):
                        result_data = macro_analysis_result.get('result', {})
                        macro_dashboard_text = self._format_macro_dashboard(result_data, today)
                        logger.info(f"✓ Macro dashboard generated ({len(macro_dashboard_text)} chars)")
                    else:
                        logger.warning("  Macro analysis generation failed, using raw context")
                else:
                    logger.info("  No macro data available for today")
            except Exception as e:
                logger.warning(f"OpenBB macro fetch failed (non-blocking): {e}")
                macro_context_text = ""
                macro_dashboard_text = ""
        else:
            logger.debug("OpenBB service not available, skipping macro context")

        # Default focus areas - aligned with feed coverage
        if focus_areas is None:
            focus_areas = [
                "cybersecurity threats, data breaches, and critical infrastructure vulnerabilities",
                "geopolitical tensions and power dynamics in Indo-Pacific region (China, Taiwan, ASEAN)",
                "Middle East conflicts, security developments, and regional stability (Israel, Iran, Arab states)",
                "defense technology, military procurement, and strategic weapons systems",
                "global supply chain disruptions, semiconductor industry, and critical materials",
                "energy markets, OPEC dynamics, and transition to renewables",
                "European Union policy, Russia-NATO relations, and transatlantic security",
                "space industry developments, satellite technology, and dual-use applications",
                "Africa security challenges, conflicts, and great power competition",
                "Latin America political developments and China's influence in the region",
                "economic policy shifts, central bank decisions, and financial market trends"
            ]

        # Step 1: Get recent articles
        if from_time or to_time:
            logger.info(f"\n[STEP 1] Fetching articles (time window: {from_time or 'N/A'} → {to_time or 'N/A'})...")
        else:
            logger.info(f"\n[STEP 1] Fetching articles from last {days} day(s)...")
        all_recent_articles = self.db.get_recent_articles(days=days, from_time=from_time, to_time=to_time)
        logger.info(f"✓ Retrieved {len(all_recent_articles)} recent articles")

        if not all_recent_articles:
            logger.warning("No recent articles found. Cannot generate report.")
            return {
                'success': False,
                'error': 'No recent articles available',
                'timestamp': datetime.now().isoformat()
            }

        # Step 1b: Filter articles by relevance to focus areas
        logger.info(f"\n[STEP 1b] Filtering articles by relevance...")
        recent_articles = self.filter_relevant_articles(
            articles=all_recent_articles,
            focus_areas=focus_areas,
            top_n=top_articles,
            min_similarity=min_similarity,
            min_fallback=min_fallback
        )

        if not recent_articles:
            logger.warning("No relevant articles found after filtering. Cannot generate report.")
            return {
                'success': False,
                'error': 'No relevant articles found',
                'timestamp': datetime.now().isoformat()
            }

        # Step 2: Get RAG context
        logger.info(f"\n[STEP 2] Retrieving historical context via RAG...")

        # Auto-generate RAG queries from focus areas if not provided
        if rag_queries is None:
            rag_queries = focus_areas

        # Step 2a: Expand queries (if enabled)
        expanded_queries = self.expand_rag_queries(rag_queries)

        # Step 2b: Execute RAG searches
        all_rag_results = []
        # Increase top_k if reranking is enabled (cast wider net)
        search_top_k = rag_top_k * 2 if self.enable_reranking else rag_top_k

        for query in expanded_queries:
            results = self.get_rag_context(query, top_k=search_top_k)
            all_rag_results.extend(results)

        logger.info(f"✓ Retrieved {len(all_rag_results)} total chunks from RAG")

        # Step 2c: Advanced deduplication (ID + similarity)
        unique_rag_results = self.deduplicate_chunks_advanced(all_rag_results)

        # Step 2d: Reranking (if enabled)
        if self.enable_reranking and unique_rag_results and rag_queries:
            # Rerank using a composite query that balances coverage and coherence:
            # - Full first query for semantic anchor
            # - Top 5 keywords from each additional query for breadth
            primary = rag_queries[0]
            secondary_keywords = []
            for q in rag_queries[1:4]:  # Up to 3 additional queries
                words = q.split()[:5]  # Top 5 keywords each
                secondary_keywords.extend(words)
            
            composite_query = primary + " " + " ".join(secondary_keywords)
            
            unique_rag_results = self._rerank_chunks(
                query=composite_query,
                chunks=unique_rag_results,
                top_k=self.reranking_top_k * len(rag_queries)  # Scale by number of queries
            )

        logger.info(f"✓ Final RAG context: {len(unique_rag_results)} unique historical chunks")

        # Step 2.5: Fetch narrative storyline context
        logger.info(f"\n[STEP 2.5] Fetching narrative storyline context...")
        narrative_ctx = self._get_narrative_context(days=days, top_n=10)
        narrative_xml = self._format_narrative_xml(narrative_ctx)
        storyline_count = len(narrative_ctx.get('storylines', []))

        if narrative_xml:
            narrative_section = f"""---

**STRATEGIC STORYLINE CONTEXT:**
The following are the top {storyline_count} active intelligence storylines tracked by the narrative engine, ordered by momentum (highest = most active).
Use them to:
- Connect today's events to ongoing strategic narratives
- Identify which storylines are accelerating or decelerating based on today's news
- In the "Trend Analysis" section, reference storyline momentum shifts
- Generate section "5. Strategic Storyline Tracker" using this data

{narrative_xml}"""
            logger.info(f"✓ Narrative context: {storyline_count} storylines, {len(narrative_ctx.get('edges', []))} edges")
        else:
            narrative_section = ""
            logger.info("  No active storylines found, skipping narrative context")

        # Step 3: Format context for LLM
        logger.info(f"\n[STEP 3] Preparing prompt for LLM...")
        recent_articles_text = self.format_recent_articles(recent_articles)
        rag_context_text = self.format_rag_context(unique_rag_results)

        # Step 4: Construct prompt (with macro dashboard + raw data for reference)
        report_date = datetime.now().strftime('%Y-%m-%d')

        # Build header section with macro dashboard (if available)
        header_section = ""
        if macro_dashboard_text:
            header_section = f"""# 🌍 Daily Intelligence Briefing - {report_date}

{macro_dashboard_text}

---

"""
            logger.info("  Macro dashboard injected into prompt header")

            # Also include raw macro data for LLM reference (but after dashboard)
            if macro_context_text:
                header_section += f"""
=== RAW MACRO DATA (for LLM reference - DO NOT include in report) ===
{macro_context_text}

---

"""
        elif macro_context_text:
            # Fallback: use raw context if dashboard generation failed
            header_section = f"""
{macro_context_text}

---

"""
            logger.info("  Raw macro context injected (dashboard unavailable)")

        prompt = f"""{header_section}You are an intelligence analyst generating a daily intelligence briefing.

**YOUR TASK:**
Analyze today's news articles and provide a comprehensive intelligence report focused on strategic relevance and actionable investment implications. Prioritize events that represent breaking points in existing trends and competition between major powers, even in seemingly peripheral regions.
Transform raw news into a structured, high-precision intelligence briefing.
Your goal is to balance **Macro-Strategic Context** with **Micro-Tactical Details**.
NEVER provide a macro claim without the specific micro-event that supports it.

**FOCUS AREAS:**
{chr(10).join(f"- {area}" for area in focus_areas)}

**PRIORITIZATION FRAMEWORK:**
Before writing the report, score each event using this system and prioritize those with highest scores:

1. Immediate Impact (0-3 points): Does this event immediately affect national security, financial markets, or critical infrastructure? A cyberattack on a power grid scores 3, a generic diplomatic statement scores 0.

2. Escalation Potential (0-3 points): Can this event rapidly degenerate? A military incident in a contested zone scores 3, a peaceful local protest scores 0. Always ask: can this trigger a chain reaction?

3. Critical Actor Involvement (0-2 points): Are nuclear states, major economies, or actors controlling strategic resources involved? China, USA, Russia, EU score 2, peripheral countries without significant alliances score 0 (st martin island).

4. Break from Historical Pattern (0-2 points): Does this event represent a rupture with recent history? If it breaks a 5+ year pattern, it scores 2; if it confirms existing trends, it scores 0. Example: Russia and Ukraine negotiating after two years of refusal scores 2.

5. Long-Term Strategic Relevance (0-3 points + bonus): Does this event involve control of critical resources, trade routes, or positioning in great power competition even if it seems peripheral today? Think five to ten years ahead, not just six months.

**SPECIAL RULE - PERIPHERAL STRATEGIC EVENTS:**
Assign a bonus of 2 additional points to events involving great power competition in regions considered "peripheral" but strategically positioned: Myanmar, East Africa (Djibouti, Horn of Africa), Central Asia, Arctic, small Pacific island states. Even if immediate impact seems low, these events reveal long-term dynamics in global geopolitical repositioning. When you identify such events, explicitly explain why the geographic position or resources involved amplify importance beyond surface appearance.

Events scoring 8-10 points require priority analysis. Events scoring 4-7 go in standard report. Events below 4 can be briefly mentioned.

**REPORT STRUCTURE:**

1. Executive Summary (200-300 words)
Highlight the most critical developments with focus on strategic breaks and shifts in great power dynamics.

2. Key Developments by Category (150-200 words each):
   - Cybersecurity 
   - Technology
   - Geopolitical Events 
   - Economic events

For each development, always identify specific actors (individuals, organizations, governments, groups), explain their motivations and causal relationships. Avoid impersonal language: instead of "tensions are rising," say "Russia and NATO are escalating tensions because..." Provide relationship context: explain how actors relate to each other (allies, adversaries, dependencies).

3. Trend Analysis (250-300 words)
Connect current events with historical patterns from the context. Identify whether events confirm or break from existing trends. Reference active storylines and their momentum shifts where relevant.

4. Actionable Insights: Investment Implications

For each significant development, provide a three-level structured analysis that portfolio managers can use immediately:

**Level 1 - Direct Beneficiaries (immediate exposure):**
Identify companies with direct exposure seeing impact on balance sheets within 1-2 quarters. Specify:
- Company names with exact tickers
- Contract size or revenue impact
- Specific catalysts with concrete numbers
Example: "Long defense contractors Lockheed Martin (LMT), Raytheon (RTX), Northrop Grumman (NOC) based on $4.2B Pentagon contract for THAAD systems announced today [Article 12]. Delivery scheduled Q2-Q3 2025, with potential extensions if Taiwan increases orders (likely scenario given Chinese military exercises last week). Monitor: LMT earnings call January 15 for 2025 guidance."

**Level 2 - Supply Chain & Correlated Markets:**
Trace the complete causal chain. A geopolitical event rarely hits only one isolated sector. If China blocks rare earth exports, analyze not just alternative producers like Lynas (ASX:LYC) or MP Materials (MP), but also permanent magnet manufacturers, EV makers depending on those magnets, utilities that ordered wind turbines that won't arrive on schedule. Map: geopolitical event → input shortage → companies with alternative inventory → companies revising guidance → end markets facing delays.

**Level 3 - Macro Market Impacts:**
Consider impacts on currencies, government bonds, and commodities. If India raises fertilizer tariffs in retaliation against Canada, this affects not just fertilizer producers but also agricultural futures, US farm loans, and Canadian dollar weakening from reduced exports. If Myanmar becomes a proxy battlefield between US and China, this shifts capital flows toward safe-haven assets, strengthens Japanese yen, and increases credit default swap premiums on ASEAN countries perceived as next on the list.

For each insight, always include: specific tickers, concrete catalysts with exact figures, timing, causal connections with other events, and next catalysts to monitor.

5. Strategic Storyline Tracker (if storyline data is provided below)

For each of the top 5 storylines by momentum from the strategic context:
- **Status**: Current narrative status (emerging/active) and momentum trend (accelerating if today's articles advance it, stable if no new developments, decelerating if contradicted)
- **Today's Impact**: How today's news articles specifically affect this storyline
- **Cross-Domain Links**: Connected storylines and what their intersection means strategically
- **Watch Indicators**: Key next events, dates, or triggers to monitor

If no storyline data is provided, skip this section entirely.

**ADDITIONAL GUIDELINES:**
- Cite specific articles with [Article N] references
- Use professional, analytical tone
- Prioritize events that are strategic break points, not just high-volume news
- When information is unverified or conflicting, use confidence indicators (High/Medium/Low) and cite multiple sources
- Never use generic language like "the sector could benefit" - always specify which companies, why, with what catalyst, and in what timeframe

---
{recent_articles_text}

---

{rag_context_text}

---

{narrative_section}

**Now generate the intelligence report:**
"""

        # Step 5: Generate report with Gemini (temperature 0.35 for narrative quality)
        logger.info(f"\n[STEP 4] Generating report with Gemini (temperature: 0.35)...")
        try:
            response = self.model.generate_content(
                prompt,
                generation_config=genai.GenerationConfig(
                    temperature=0.35,  # Slightly higher for narrative flow
                    max_output_tokens=8192,
                ),
                request_options={"timeout": 120},
            )
            report_text = response.text
            logger.info(f"✓ Report generated successfully ({len(report_text)} characters)")
        except Exception as e:
            logger.error(f"Failed to generate report: {e}")
            return {
                'success': False,
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            }

        # Step 6: Compile results
        report = {
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'report_text': report_text,
            'metadata': {
                'focus_areas': focus_areas,
                'recent_articles_count': len(recent_articles),
                'historical_chunks_count': len(unique_rag_results),
                'days_covered': days,
                'model_used': self.model.model_name,
                'macro_analysis': {
                    'enabled': bool(macro_analysis_result),
                    'risk_regime': macro_analysis_result.get('result', {}).get('risk_regime') if macro_analysis_result else None,
                    'dashboard_items_count': len(macro_analysis_result.get('result', {}).get('dashboard_items', [])) if macro_analysis_result else 0,
                    'temperature_step1': 0.45,
                    'temperature_step2': 0.35
                } if macro_analysis_result else None,
                'narrative_context': {
                    'storylines_count': len(narrative_ctx.get('storylines', [])),
                    'edges_count': len(narrative_ctx.get('edges', [])),
                    'top_storylines': [
                        {'id': s['id'], 'title': s['title'], 'momentum': s['momentum']}
                        for s in narrative_ctx.get('storylines', [])[:5]
                    ],
                } if narrative_ctx.get('storylines') else None
            },
            'sources': {
                'recent_articles': [
                    {
                        'title': a['title'],
                        'link': a['link'],
                        'source': a['source'],
                        'published_date': a['published_date'].isoformat() if hasattr(a['published_date'], 'isoformat') else str(a['published_date'])
                    }
                    for a in recent_articles
                ],
                'historical_context': [
                    {
                        'title': r['title'],
                        'link': r['link'],
                        'similarity': r['similarity']
                    }
                    for r in unique_rag_results
                ]
            }
        }

        logger.info("\n✓ Report generation complete")
        return report

    def save_report(self, report: Dict[str, Any], output_dir: str = "reports") -> Path:
        """
        Save report to file.

        Args:
            report: Report dictionary from generate_report()
            output_dir: Directory to save reports

        Returns:
            Path to saved report file
        """
        output_path = Path(output_dir)
        output_path.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_file = output_path / f"intelligence_report_{timestamp}.json"

        with open(report_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        logger.info(f"✓ Report saved to: {report_file}")

        # Also save markdown version for easy reading
        md_file = output_path / f"intelligence_report_{timestamp}.md"
        with open(md_file, 'w', encoding='utf-8') as f:
            f.write(f"# Intelligence Report - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write(report['report_text'])
            f.write("\n\n---\n\n")
            f.write(f"**Generated by:** {report['metadata']['model_used']}\n")
            f.write(f"**Sources:** {report['metadata']['recent_articles_count']} recent articles, "
                   f"{report['metadata']['historical_chunks_count']} historical chunks\n")

        logger.info(f"✓ Markdown version saved to: {md_file}")

        return report_file

    def run_daily_report(
        self,
        focus_areas: Optional[List[str]] = None,
        save: bool = True,
        save_to_db: bool = True,
        output_dir: str = "reports",
        top_articles: int = 60,
        min_similarity: float = 0.30,
        min_fallback: int = 10
    ) -> Dict[str, Any]:
        """
        Run complete daily report generation pipeline.

        Args:
            focus_areas: Topics to focus on
            save: Whether to save report to file
            save_to_db: Whether to save report to database (for HITL review)
            output_dir: Directory for saved reports
            top_articles: Maximum number of top relevant articles to include (default: 60)
            min_similarity: Minimum cosine similarity threshold (default: 0.30)
            min_fallback: Minimum articles to return even if below threshold (default: 10)

        Returns:
            Report dictionary with added 'report_id' if saved to database
        """
        logger.info("Starting daily intelligence report generation...")

        # Generate report
        report = self.generate_report(
            focus_areas=focus_areas,
            days=1,  # Last 24 hours
            rag_top_k=5,  # Top 5 historical chunks per focus area
            top_articles=top_articles,
            min_similarity=min_similarity,
            min_fallback=min_fallback
        )

        if not report['success']:
            logger.error(f"Report generation failed: {report.get('error')}")
            return report

        # Save to database (for HITL review)
        if save_to_db:
            report_id = self.db.save_report(report)
            if report_id:
                report['report_id'] = report_id
                logger.info(f"✓ Report saved to database with ID: {report_id}")
            else:
                logger.warning("Failed to save report to database")

        # Save to file
        if save:
            self.save_report(report, output_dir=output_dir)

        # Print summary
        logger.info("\n" + "=" * 80)
        logger.info("REPORT SUMMARY")
        logger.info("=" * 80)
        logger.info(f"Timestamp: {report['timestamp']}")
        logger.info(f"Recent articles analyzed: {report['metadata']['recent_articles_count']}")
        logger.info(f"Historical context chunks: {report['metadata']['historical_chunks_count']}")
        logger.info(f"Report length: {len(report['report_text'])} characters")

        if 'report_id' in report:
            logger.info(f"Database ID: {report['report_id']}")
            logger.info(f"Review at: http://localhost:8501 (run ./scripts/run_dashboard.sh)")

        return report

    # =========================================================================
    # MACRO-FIRST PIPELINE METHODS
    # =========================================================================
    # These methods implement the serialized pipeline where:
    # 1. Macro report is generated first
    # 2. Context is condensed for token efficiency
    # 3. Trade signals are extracted with macro alignment check

    def filter_articles_with_tickers(
        self,
        articles: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Filter articles that mention tickers from the whitelist.

        Only articles containing ticker symbols or company aliases from
        config/top_50_tickers.yaml will be returned. This reduces API
        calls by 60-80% while maintaining signal relevance.

        Args:
            articles: List of articles (with full_text and entities)

        Returns:
            Filtered list of articles that mention whitelisted tickers
        """
        if not self.ticker_whitelist:
            logger.warning("No ticker whitelist loaded, returning all articles")
            return articles

        # Build flat list of all tickers and aliases (case-insensitive)
        all_matches = set()
        for category, companies in self.ticker_whitelist.items():
            for ticker in companies:
                all_matches.add(ticker.upper())

        # Also load aliases from the full config file
        import yaml
        config_path = Path(__file__).parent.parent.parent / 'config' / 'top_50_tickers.yaml'
        aliases_map = {}

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                ticker_config = yaml.safe_load(f)

            for category, companies in ticker_config.items():
                for company in companies:
                    ticker = company['ticker']
                    # Add ticker itself
                    all_matches.add(ticker.upper())
                    # Add all aliases
                    for alias in company.get('aliases', []):
                        all_matches.add(alias.upper())
                        aliases_map[alias.upper()] = ticker
        except Exception as e:
            logger.warning(f"Could not load aliases from config: {e}")

        # Filter articles
        filtered = []
        for article in articles:
            # Check full_text
            full_text = article.get('full_text', '') or ''
            full_text_upper = full_text.upper()

            # Check entities (ORG type especially)
            entities = article.get('entities', {})
            entity_names = []
            if isinstance(entities, dict):
                for entity_type, names in entities.items():
                    if isinstance(names, list):
                        for n in names:
                            if isinstance(n, str):
                                entity_names.append(n.upper())
                            elif isinstance(n, dict):
                                # Handle dict format like {'text': 'Microsoft', 'label': 'ORG'}
                                text = n.get('text') or n.get('name') or ''
                                if text:
                                    entity_names.append(text.upper())

            # Check title
            title = article.get('title', '') or ''
            title_upper = title.upper()

            # Search for matches
            found_ticker = False
            matched_tickers = []

            for match_term in all_matches:
                if (match_term in full_text_upper or
                    match_term in title_upper or
                    match_term in entity_names):
                    found_ticker = True
                    # Get the actual ticker (resolve alias if needed)
                    actual_ticker = aliases_map.get(match_term, match_term)
                    if actual_ticker not in matched_tickers:
                        matched_tickers.append(actual_ticker)

            if found_ticker:
                # Add matched tickers to article for later use
                article['matched_tickers'] = matched_tickers
                filtered.append(article)

        logger.info(f"✓ Ticker filter: {len(filtered)}/{len(articles)} articles contain whitelisted tickers")
        return filtered

    def condense_macro_context(
        self,
        report_text: str,
        report_metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Create a token-efficient condensation of the macro report.

        This condensed context (~500 tokens) will be passed to each article
        analysis instead of the full report (~5000+ tokens), reducing API
        costs by ~90% while preserving essential macro alignment context.

        Args:
            report_text: Full macro report text
            report_metadata: Optional metadata (focus_areas, article_count, etc.)

        Returns:
            Dictionary with:
            - success: bool
            - condensed: MacroCondensedContext as dict
            - raw_llm_output: str
            - token_estimate: int (approximate tokens in condensed context)
        """
        logger.info("Condensing macro report into structured context...")

        prompt = f"""You are condensing a macro intelligence report into a structured summary.

TASK: Extract the key strategic themes, sentiment, and tickers from this report.
Output must be JSON matching the schema exactly.

JSON SCHEMA:
{{
  "key_themes": ["string", ...] (5-7 major themes, e.g., "Taiwan escalation risk", "Defense spending surge"),
  "dominant_sentiment": "RISK_ON | RISK_OFF | MIXED",
  "priority_sectors": ["string", ...] (max 5 sectors, e.g., "Defense", "Semiconductors"),
  "tickers_mentioned": ["string", ...] (all tickers explicitly mentioned, max 20),
  "geopolitical_hotspots": ["string", ...] (active regions, max 5),
  "time_horizon_focus": "IMMEDIATE | SHORT_TERM | MEDIUM_TERM"
}}

RULES:
1. key_themes: Extract ACTIONABLE strategic themes, not generic observations
2. dominant_sentiment: RISK_OFF = defensive posture, RISK_ON = bullish/offensive, MIXED = unclear
3. tickers_mentioned: ONLY include tickers actually in the report text (e.g., LMT, TSM, not company names)
4. Keep each field concise (total output < 500 tokens)
5. priority_sectors: Use categories like Defense, Semiconductors, Energy, Cyber, Finance

MACRO REPORT TO CONDENSE:
---
{report_text[:15000]}
---

Respond with JSON only:"""

        try:
            response = self.model.generate_content(
                contents=[prompt],
                generation_config={
                    "response_mime_type": "application/json",
                    "temperature": 0.2,
                }
            )

            raw_output = response.text

            # Validate with Pydantic
            from .schemas import MacroCondensedContext
            validated = MacroCondensedContext.model_validate_json(raw_output)

            # Estimate tokens (rough: 1 token ~ 4 chars)
            token_estimate = len(raw_output) // 4

            logger.info(f"✓ Macro context condensed: {len(validated.key_themes)} themes, "
                       f"{len(validated.tickers_mentioned)} tickers, ~{token_estimate} tokens")

            return {
                'success': True,
                'condensed': validated.model_dump(),
                'raw_llm_output': raw_output,
                'token_estimate': token_estimate
            }

        except Exception as e:
            logger.error(f"Failed to condense macro context: {e}")
            return {
                'success': False,
                'error': str(e),
                'condensed': None
            }

    def extract_macro_signals(
        self,
        report_text: str,
        condensed_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Extract trade signals at the macro report level.

        These are HIGH-CONVICTION signals derived from the synthesis of
        multiple articles, not individual article events.

        Args:
            report_text: Full macro report text
            condensed_context: Output from condense_macro_context()

        Returns:
            Dictionary with:
            - success: bool
            - signals: list[ReportLevelSignal] as dicts
            - raw_llm_output: str
        """
        logger.info("Extracting report-level trade signals...")

        ticker_context = self._format_ticker_whitelist()

        # Format condensed themes for prompt
        themes_str = "\n".join(f"- {theme}" for theme in condensed_context.get('key_themes', []))
        sectors_str = ", ".join(condensed_context.get('priority_sectors', []))

        prompt = f"""You are extracting MACRO-LEVEL trade signals from an intelligence report.

CONTEXT (Condensed Macro Analysis):
- Dominant Sentiment: {condensed_context.get('dominant_sentiment', 'MIXED')}
- Priority Sectors: {sectors_str}
- Key Themes:
{themes_str}
- Geopolitical Hotspots: {', '.join(condensed_context.get('geopolitical_hotspots', []))}

TASK: Extract 3-8 HIGH-CONVICTION trade signals that represent the SYNTHESIS of the full report.
These are NOT per-article signals but strategic positioning based on macro themes.

TICKER WHITELIST (ONLY use these):
{ticker_context}

JSON SCHEMA (array of signals):
[
  {{
    "ticker": "string (from whitelist, e.g., LMT, TSM)",
    "signal": "BULLISH | BEARISH | NEUTRAL | WATCHLIST",
    "timeframe": "SHORT_TERM | MEDIUM_TERM | LONG_TERM",
    "rationale": "string (1-2 sentences, cite specific macro drivers)",
    "confidence": float (0.7-1.0 for macro signals),
    "supporting_themes": ["string", ...] (which key_themes support this)
  }}
]

TIMEFRAME DEFINITIONS:
- SHORT_TERM: <3 months (immediate tactical positioning)
- MEDIUM_TERM: 3-12 months (quarterly earnings impact)
- LONG_TERM: >1 year (structural shifts)

RULES:
1. HIGH BAR: Only signals with strong multi-source evidence
2. ACTIONABLE: Each signal must have clear timeframe and catalyst
3. DIVERSE: Avoid 5 defense stocks with same rationale - diversify across sectors
4. CONFIDENCE: Macro signals should be 0.7+ (synthesized from many sources)
5. Empty array is valid if no clear signals emerge

FULL REPORT:
---
{report_text[:20000]}
---

Respond with JSON array only:"""

        try:
            response = self.model.generate_content(
                contents=[prompt],
                generation_config={
                    "response_mime_type": "application/json",
                    "temperature": 0.3,
                }
            )

            raw_output = response.text

            # Parse and validate each signal
            from .schemas import ReportLevelSignal

            raw_signals = json.loads(raw_output)
            validated_signals = []

            # === FINANCIAL INTELLIGENCE v2: Sandwich Enrichment ===
            ValuationEngine = get_valuation_engine()
            enrich_signal = get_signal_enricher()
            valuation_engine = None
            if ValuationEngine and enrich_signal:
                try:
                    valuation_engine = ValuationEngine(self.db)
                    logger.info("Financial Intelligence v2 enabled for signal enrichment")
                except Exception as e:
                    logger.warning(f"Failed to initialize ValuationEngine: {e}")

            for sig in raw_signals:
                try:
                    ticker = sig.get('ticker')

                    # === SANDWICH: Enrich signal with market validation ===
                    if ticker and valuation_engine and enrich_signal:
                        try:
                            metrics = valuation_engine.build_ticker_metrics(ticker)
                            llm_confidence = sig.get('confidence', 0.8)
                            sig = enrich_signal(sig, metrics, llm_confidence)
                            logger.debug(
                                f"  {ticker}: intel_score={sig.get('intelligence_score')}, "
                                f"sma_dev={sig.get('sma_200_deviation', 'N/A')}, "
                                f"valuation={sig.get('valuation_rating', 'N/A')}"
                            )
                        except Exception as e:
                            logger.warning(f"Failed to enrich signal for {ticker}: {e}")

                    validated = ReportLevelSignal.model_validate(sig)
                    validated_signals.append(validated.model_dump())
                except Exception as e:
                    logger.warning(f"Invalid report signal skipped: {e}")

            logger.info(f"✓ Extracted {len(validated_signals)} macro-level signals")
            for sig in validated_signals:
                intel_score = sig.get('intelligence_score', 'N/A')
                logger.info(
                    f"  {sig['ticker']}: {sig['signal']} ({sig['timeframe']}) - "
                    f"confidence: {sig['confidence']:.0%}, intel_score: {intel_score}"
                )

            return {
                'success': True,
                'signals': validated_signals,
                'raw_llm_output': raw_output
            }

        except Exception as e:
            logger.error(f"Failed to extract macro signals: {e}")
            return {
                'success': False,
                'error': str(e),
                'signals': []
            }

    def extract_article_signals_with_context(
        self,
        article_text: str,
        article_metadata: Dict[str, Any],
        condensed_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Extract trade signals from an article WITH macro context alignment.

        This method adds an alignment_score to each signal indicating how
        well it aligns with the macro narrative. Signals that DIVERGE from
        macro themes may still be valid (contrarian) but are flagged.

        Args:
            article_text: Full article text
            article_metadata: Article metadata (title, source, id, etc.)
            condensed_context: Output from condense_macro_context()

        Returns:
            Dictionary with:
            - success: bool
            - signals: list[ArticleLevelSignal] as dicts
            - macro_alignment_summary: str
            - raw_llm_output: str
        """
        ticker_context = self._format_ticker_whitelist()

        # Format condensed context for prompt (~500 tokens)
        context_summary = f"""MACRO CONTEXT (Today's Intelligence Synthesis):
- Sentiment: {condensed_context.get('dominant_sentiment', 'MIXED')}
- Priority Sectors: {', '.join(condensed_context.get('priority_sectors', []))}
- Key Themes: {', '.join(condensed_context.get('key_themes', [])[:5])}
- Active Hotspots: {', '.join(condensed_context.get('geopolitical_hotspots', []))}
- Tickers in Focus: {', '.join(condensed_context.get('tickers_mentioned', [])[:10])}"""

        prompt = f"""You are analyzing an article for trade signals WITH MACRO CONTEXT ALIGNMENT.

{context_summary}

ARTICLE METADATA:
- Title: {article_metadata.get('title', 'Unknown')}
- Source: {article_metadata.get('source', 'Unknown')}

TASK: Extract trade signals AND assess how they align with today's macro narrative.

TICKER WHITELIST (ONLY use these):
{ticker_context}

JSON SCHEMA:
{{
  "signals": [
    {{
      "ticker": "string (from whitelist)",
      "signal": "BULLISH | BEARISH | NEUTRAL | WATCHLIST",
      "timeframe": "SHORT_TERM | MEDIUM_TERM | LONG_TERM",
      "rationale": "string (article-specific catalyst)",
      "confidence": float (0.0-1.0),
      "alignment_score": float (0.0-1.0, how well this aligns with macro themes),
      "alignment_reasoning": "string (explain alignment or divergence)"
    }}
  ],
  "macro_alignment_summary": "string (1-2 sentences: how this article fits the macro narrative)"
}}

ALIGNMENT SCORING:
- 1.0: Signal directly supports macro themes (e.g., defense bullish during escalation)
- 0.7-0.9: Consistent with macro but different sector/angle
- 0.4-0.6: Neutral/orthogonal to macro themes
- 0.1-0.3: Contrarian signal (valid but divergent from macro consensus)
- 0.0: Signal contradicts macro narrative without clear justification

RULES:
1. Only use tickers from whitelist
2. Empty signals array is valid if article has no ticker-relevant events
3. Be specific in rationale - cite article content
4. Alignment reasoning should explain the connection (or lack thereof) to macro themes

ARTICLE TEXT:
---
{article_text[:8000]}
---

Respond with JSON only:"""

        try:
            response = self.model.generate_content(
                contents=[prompt],
                generation_config={
                    "response_mime_type": "application/json",
                    "temperature": 0.3,
                }
            )

            raw_output = response.text

            from .schemas import ArticleLevelSignal

            parsed = json.loads(raw_output)
            validated_signals = []

            # === FINANCIAL INTELLIGENCE v2: Sandwich Enrichment ===
            ValuationEngine = get_valuation_engine()
            enrich_signal = get_signal_enricher()
            valuation_engine = None
            if ValuationEngine and enrich_signal:
                try:
                    valuation_engine = ValuationEngine(self.db)
                except Exception as e:
                    logger.debug(f"ValuationEngine not available: {e}")

            for sig in parsed.get('signals', []):
                try:
                    ticker = sig.get('ticker')

                    # === SANDWICH: Enrich signal with market validation ===
                    if ticker and valuation_engine and enrich_signal:
                        try:
                            metrics = valuation_engine.build_ticker_metrics(ticker)
                            llm_confidence = sig.get('confidence', 0.5)
                            sig = enrich_signal(sig, metrics, llm_confidence)
                        except Exception as e:
                            logger.debug(f"Failed to enrich article signal for {ticker}: {e}")

                    validated = ArticleLevelSignal.model_validate(sig)
                    validated_signals.append(validated.model_dump())
                except Exception as e:
                    logger.warning(f"Invalid article signal skipped: {e}")

            return {
                'success': True,
                'signals': validated_signals,
                'macro_alignment_summary': parsed.get('macro_alignment_summary', ''),
                'raw_llm_output': raw_output
            }

        except Exception as e:
            logger.error(f"Failed to extract article signals: {e}")
            return {
                'success': False,
                'error': str(e),
                'signals': [],
                'macro_alignment_summary': ''
            }

    def save_trade_signals(
        self,
        report_id: int,
        report_signals: List[Dict[str, Any]],
        article_signals: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        """
        Save trade signals to normalized trade_signals table.
        Also updates reports.metadata with denormalized JSONB for quick access.

        Args:
            report_id: FK to reports table
            report_signals: List of ReportLevelSignal dicts
            article_signals: List of dicts with article_id and signals

        Returns:
            Dictionary with counts: saved_report_signals, saved_article_signals, errors
        """
        from psycopg2.extras import Json

        stats = {'saved_report_signals': 0, 'saved_article_signals': 0, 'errors': 0}

        try:
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    # 1. Insert report-level signals (article_id = NULL)
                    for sig in report_signals:
                        try:
                            cur.execute("""
                                INSERT INTO trade_signals
                                (report_id, article_id, ticker, signal, timeframe,
                                 rationale, confidence, alignment_score, signal_source, category,
                                 intelligence_score, sma_200_deviation, pe_rel_valuation,
                                 valuation_rating, data_quality,
                                 price_source, sma_source, pe_source, sector_pe_source,
                                 fetched_at, days_of_history)
                                VALUES (%s, NULL, %s, %s, %s, %s, %s, 1.0, 'report', %s,
                                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                ON CONFLICT (report_id, ticker, signal, timeframe)
                                WHERE article_id IS NULL
                                DO UPDATE SET
                                    intelligence_score = EXCLUDED.intelligence_score,
                                    sma_200_deviation = EXCLUDED.sma_200_deviation,
                                    pe_rel_valuation = EXCLUDED.pe_rel_valuation,
                                    valuation_rating = EXCLUDED.valuation_rating,
                                    data_quality = EXCLUDED.data_quality,
                                    price_source = EXCLUDED.price_source,
                                    sma_source = EXCLUDED.sma_source,
                                    pe_source = EXCLUDED.pe_source,
                                    sector_pe_source = EXCLUDED.sector_pe_source,
                                    fetched_at = EXCLUDED.fetched_at,
                                    days_of_history = EXCLUDED.days_of_history
                            """, (
                                report_id,
                                sig['ticker'],
                                sig['signal'],
                                sig['timeframe'],
                                sig['rationale'],
                                sig.get('confidence', 0.8),
                                sig.get('category'),
                                sig.get('intelligence_score'),
                                sig.get('sma_200_deviation'),
                                sig.get('pe_rel_valuation'),
                                sig.get('valuation_rating'),
                                sig.get('data_quality', 'FULL'),
                                sig.get('price_source'),
                                sig.get('sma_source'),
                                sig.get('pe_source'),
                                sig.get('sector_pe_source'),
                                sig.get('fetched_at'),
                                sig.get('days_of_history')
                            ))
                            if cur.rowcount > 0:
                                stats['saved_report_signals'] += 1
                            else:
                                logger.debug(f"Duplicate report signal skipped: {sig['ticker']}")
                        except Exception as e:
                            logger.warning(f"Failed to save report signal {sig['ticker']}: {e}")
                            stats['errors'] += 1

                    # 2. Insert article-level signals
                    for article_data in article_signals:
                        article_id = article_data.get('article_id')
                        for sig in article_data.get('signals', []):
                            try:
                                cur.execute("""
                                    INSERT INTO trade_signals
                                    (report_id, article_id, ticker, signal, timeframe,
                                     rationale, confidence, alignment_score, signal_source, category,
                                     intelligence_score, sma_200_deviation, pe_rel_valuation,
                                     valuation_rating, data_quality,
                                     price_source, sma_source, pe_source, sector_pe_source,
                                     fetched_at, days_of_history)
                                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'article', %s,
                                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                    ON CONFLICT (report_id, article_id, ticker, signal, timeframe)
                                    WHERE article_id IS NOT NULL
                                    DO UPDATE SET
                                        intelligence_score = EXCLUDED.intelligence_score,
                                        sma_200_deviation = EXCLUDED.sma_200_deviation,
                                        pe_rel_valuation = EXCLUDED.pe_rel_valuation,
                                        valuation_rating = EXCLUDED.valuation_rating,
                                        data_quality = EXCLUDED.data_quality,
                                        price_source = EXCLUDED.price_source,
                                        sma_source = EXCLUDED.sma_source,
                                        pe_source = EXCLUDED.pe_source,
                                        sector_pe_source = EXCLUDED.sector_pe_source,
                                        fetched_at = EXCLUDED.fetched_at,
                                        days_of_history = EXCLUDED.days_of_history
                                """, (
                                    report_id,
                                    article_id,
                                    sig['ticker'],
                                    sig['signal'],
                                    sig['timeframe'],
                                    sig['rationale'],
                                    sig.get('confidence', 0.5),
                                    sig.get('alignment_score', 0.5),
                                    sig.get('category'),
                                    sig.get('intelligence_score'),
                                    sig.get('sma_200_deviation'),
                                    sig.get('pe_rel_valuation'),
                                    sig.get('valuation_rating'),
                                    sig.get('data_quality', 'FULL'),
                                    sig.get('price_source'),
                                    sig.get('sma_source'),
                                    sig.get('pe_source'),
                                    sig.get('sector_pe_source'),
                                    sig.get('fetched_at'),
                                    sig.get('days_of_history')
                                ))
                                if cur.rowcount > 0:
                                    stats['saved_article_signals'] += 1
                                else:
                                    logger.debug(f"Duplicate article signal skipped: {sig['ticker']}")
                            except Exception as e:
                                logger.warning(f"Failed to save article signal: {e}")
                                stats['errors'] += 1

                    # 3. Update reports.metadata with denormalized signals JSONB
                    all_signals_summary = {
                        'report_signals': report_signals,
                        'article_signals_count': stats['saved_article_signals'],
                        'total_signals': stats['saved_report_signals'] + stats['saved_article_signals'],
                        'extraction_timestamp': datetime.now().isoformat()
                    }

                    cur.execute("""
                        UPDATE reports
                        SET metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb,
                            updated_at = NOW()
                        WHERE id = %s
                    """, (Json({'trade_signals_summary': all_signals_summary}), report_id))

                    conn.commit()

            logger.info(f"✓ Saved {stats['saved_report_signals']} report signals, "
                       f"{stats['saved_article_signals']} article signals to database")
            return stats

        except Exception as e:
            logger.error(f"Failed to save trade signals: {e}")
            stats['errors'] += 1
            return stats

    def run_macro_first_pipeline(
        self,
        focus_areas: Optional[List[str]] = None,
        days: int = 1,
        from_time: Optional[datetime] = None,
        to_time: Optional[datetime] = None,
        save: bool = True,
        save_to_db: bool = True,
        output_dir: str = "reports",
        top_articles: int = 60,
        min_similarity: float = 0.30,
        min_fallback: int = 10,
        skip_article_signals: bool = False
    ) -> Dict[str, Any]:
        """
        Run the serialized Macro-First pipeline.

        Flow:
        1. Generate macro report (existing generate_report method)
        2. Condense macro context (~500 tokens)
        3. Extract report-level signals (high-conviction, synthesized)
        4. Filter articles with ticker mentions
        5. For each filtered article: extract signals with macro alignment check
        6. Save all signals to trade_signals table

        Args:
            focus_areas: Topics to focus on
            days: Number of days to look back (if from_time/to_time not set)
            from_time: Optional start time for explicit time window (takes precedence over days)
            to_time: Optional end time for explicit time window (takes precedence over days)
            save: Whether to save report to file
            save_to_db: Whether to save to database
            output_dir: Directory for saved reports
            top_articles: Maximum articles to analyze
            min_similarity: Relevance threshold
            skip_article_signals: If True, only extract report-level signals (faster)

        Returns:
            Extended report dictionary with trade_signals data
        """
        logger.info("=" * 80)
        logger.info("MACRO-FIRST PIPELINE (Serialized)")
        logger.info("=" * 80)

        # Step 1: Generate macro report (reuse existing method)
        logger.info("\n[STEP 1/6] Generating macro report...")
        report = self.generate_report(
            focus_areas=focus_areas,
            days=days,
            from_time=from_time,
            to_time=to_time,
            top_articles=top_articles,
            min_similarity=min_similarity,
            min_fallback=min_fallback
        )

        if not report['success']:
            return report

        report_text = report['report_text']

        # Step 2: Condense macro context
        logger.info("\n[STEP 2/6] Condensing macro context for efficiency...")
        condensed_result = self.condense_macro_context(report_text, report.get('metadata'))

        if not condensed_result['success']:
            logger.warning("Failed to condense context, using fallback")
            condensed_context = {
                'key_themes': [],
                'dominant_sentiment': 'MIXED',
                'priority_sectors': [],
                'tickers_mentioned': [],
                'geopolitical_hotspots': [],
                'time_horizon_focus': 'SHORT_TERM'
            }
        else:
            condensed_context = condensed_result['condensed']

        # Step 3: Extract report-level signals
        logger.info("\n[STEP 3/6] Extracting report-level trade signals...")
        macro_signals_result = self.extract_macro_signals(report_text, condensed_context)
        report_signals = macro_signals_result.get('signals', [])

        # Step 4 & 5: Article-level signals (optional)
        article_signals = []
        articles_with_tickers = []

        if not skip_article_signals:
            logger.info(f"\n[STEP 4/6] Filtering articles with ticker mentions...")

            # Get full articles from database
            articles_refs = report['sources']['recent_articles']
            full_articles = []

            for article_ref in articles_refs:
                article = self.db.get_article_by_link(article_ref['link'])
                if article:
                    full_articles.append(article)

            # Filter to only articles with ticker mentions
            articles_with_tickers = self.filter_articles_with_tickers(full_articles)

            logger.info(f"\n[STEP 5/6] Extracting article-level signals with macro alignment...")
            logger.info(f"Processing {len(articles_with_tickers)} articles with ticker mentions...")

            for i, article in enumerate(articles_with_tickers, 1):
                try:
                    logger.info(f"  [{i}/{len(articles_with_tickers)}] {article['title'][:60]}...")

                    result = self.extract_article_signals_with_context(
                        article_text=article.get('full_text', ''),
                        article_metadata={
                            'title': article['title'],
                            'source': article['source'],
                            'id': article['id'],
                            'matched_tickers': article.get('matched_tickers', [])
                        },
                        condensed_context=condensed_context
                    )

                    if result['success'] and result['signals']:
                        article_signals.append({
                            'article_id': article['id'],
                            'article_title': article['title'],
                            'signals': result['signals'],
                            'macro_alignment_summary': result['macro_alignment_summary']
                        })
                        logger.info(f"      → {len(result['signals'])} signals extracted")

                except Exception as e:
                    logger.warning(f"  Error processing article: {e}")
        else:
            logger.info("\n[STEP 4/6] Skipping article filtering (--skip-article-signals)")
            logger.info("[STEP 5/6] Skipping article-level signals (--skip-article-signals)")

        # Step 6: Save to database
        logger.info("\n[STEP 6/6] Saving to database...")
        report_id = None
        signal_stats = {'saved_report_signals': 0, 'saved_article_signals': 0, 'errors': 0}

        if save_to_db:
            # Save report first
            report_id = self.db.save_report(report)

            if report_id:
                # Save trade signals
                signal_stats = self.save_trade_signals(
                    report_id=report_id,
                    report_signals=report_signals,
                    article_signals=article_signals
                )

            report['report_id'] = report_id

        # Save to file
        if save:
            self.save_report(report, output_dir=output_dir)

        # Enrich report with trade signals data
        report['macro_first'] = True
        report['condensed_context'] = condensed_context
        report['report_signals'] = report_signals
        report['article_signals'] = article_signals
        report['article_signals_count'] = len(article_signals)
        report['articles_with_tickers_count'] = len(articles_with_tickers)
        report['trade_signals_stats'] = signal_stats
        report['token_savings_estimate'] = condensed_result.get('token_estimate', 0)

        # Summary
        total_article_signals = sum(len(a.get('signals', [])) for a in article_signals)

        logger.info("\n" + "=" * 80)
        logger.info("MACRO-FIRST PIPELINE COMPLETE")
        logger.info("=" * 80)
        logger.info(f"Report ID: {report_id}")
        logger.info(f"Report-level signals: {len(report_signals)}")
        logger.info(f"Articles with tickers: {len(articles_with_tickers)}")
        logger.info(f"Article-level signals: {total_article_signals}")
        logger.info(f"Token savings (condensed context): ~{5000 - condensed_result.get('token_estimate', 500)} tokens/article")

        return report

    # ──────────────────────────────────────────────────────────────────────
    # Sequential 5-call architecture (B, C, D, E, F2/F3, G)
    # ──────────────────────────────────────────────────────────────────────

    # ── B: Standardized LLM call helper ───────────────────────────────────

    def _call_llm(
        self,
        prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 8192,
        timeout: int = 120,
        label: str = "LLM",
        max_retries: int = 2,
    ) -> str:
        """
        Standardized LLM call with timeout, max_tokens ceiling, and exponential retry.
        Prevents 900s hangs, truncation, and rate-limit failures.
        """
        import time
        last_error: Exception = RuntimeError("No attempt made")

        for attempt in range(max_retries + 1):
            try:
                if attempt > 0:
                    wait = 2 ** (attempt - 1)  # 1s, 2s, 4s ...
                    logger.warning(
                        f"  [{label}] retry {attempt}/{max_retries} after {wait}s "
                        f"(prev error: {last_error})"
                    )
                    time.sleep(wait)

                logger.info(
                    f"  [{label}] prompt={len(prompt):,} chars | "
                    f"T={temperature} | max_tokens={max_tokens} | timeout={timeout}s"
                )
                response = self.model.generate_content(
                    prompt,
                    generation_config=genai.GenerationConfig(
                        temperature=temperature,
                        max_output_tokens=max_tokens,
                    ),
                    request_options={"timeout": timeout},
                )
                result = response.text
                logger.info(f"  [{label}] ✓ {len(result.split()):,} words")
                return result

            except Exception as e:
                last_error = e
                logger.warning(f"  [{label}] attempt {attempt + 1} failed: {e}")

        raise last_error

    # ── C: Domain section definitions ─────────────────────────────────────

    _DAILY_SECTION_DEFS: Dict[int, Dict] = {
        1: {
            "header": "**Cybersecurity**",
            "word_target": "200-250 words",
            "domain_rag_query": (
                "cybersecurity APT ransomware critical infrastructure attack zero-day "
                "malware attribution nation-state cyber espionage supply chain compromise"
            ),
            "focus": (
                "- Active threat actors: APT groups, ransomware gangs, state-sponsored operators — "
                "name them by group and attribution confidence\n"
                "- Attack vectors, targeted sectors (energy, finance, gov, defense, telecoms)\n"
                "- Attribution quality: TTPs overlap, infrastructure reuse, government indictments\n"
                "- Defensive actions: patches, takedowns, law enforcement — specific actors + timelines\n"
                "- New CVEs or zero-days exploited in the wild"
            ),
            "requirements": (
                "- ATTRIBUTION: always qualify with confidence level — 'attributed to [Group] "
                "[High/Medium/Low confidence] based on [TTP overlap/infrastructure/indictment]'\n"
                "- OPERATIONAL INTENT: distinguish espionage vs. pre-positioning for sabotage "
                "vs. financial gain — each has different strategic implications for the reader\n"
                "- CAUSAL CHAIN: connect each attack to its geopolitical context "
                "('linked to X-Y bilateral tensions') or economic motive\n"
                "- SPECIFICITY: malware family names, CVE numbers if confirmed, named sectors — "
                "never 'cyberattack on critical infrastructure' without specifics\n"
                "- SOURCE FIDELITY: 'attributed to' ≠ 'confirmed as'; 'intrusion detected' ≠ 'attack succeeded'\n"
                "- CHAIN-OF-THOUGHT: Before writing each paragraph, reason step by step — "
                "identify the threat actor, connect to geopolitical motive, then assess operational impact. "
                "Do not jump to conclusions.\n"
                "**KEY TAKEAWAYS** (append 3 bullets at the very end of this section):\n"
                "  • Top threat actor + attributed intent (confidence level)\n"
                "  • Most significant attack vector or CVE exploited in the wild\n"
                "  • Primary defensive/policy implication for the reader"
            ),
        },
        2: {
            "header": "**Technology**",
            "word_target": "200-250 words",
            "domain_rag_query": (
                "semiconductor chip AI artificial intelligence quantum computing dual-use "
                "export controls TSMC ASML strategic technology competition supply chain"
            ),
            "focus": (
                "- Semiconductor supply chain: fab capacity, TSMC/Samsung/Intel/ASML moves, "
                "rare earth dependencies, advanced packaging\n"
                "- AI strategic competition: frontier model deployments, military AI, autonomous systems, "
                "compute clusters, export control enforcement\n"
                "- Dual-use technology transfers, BIS enforcement, entity list updates\n"
                "- Quantum, space, hypersonics — verified milestones with strategic implications"
            ),
            "requirements": (
                "- STRATEGIC FRAME: for every tech development → who gains what strategic advantage "
                "and on what timeline? Never describe tech in isolation\n"
                "- CHOKEPOINTS: identify critical single-point dependencies "
                "('ASML controls 100% of EUV — without it, no 7nm+ chips outside ASML customer base')\n"
                "- TIMELINE: explicitly distinguish 'commercially deployed now', '2-5 years', "
                "'decade+' — no vague 'could soon'\n"
                "- ECONOMIC-SECURITY NEXUS: connect R&D milestones to defense procurement, "
                "export controls, or supply chain shifts\n"
                "- SOURCE FIDELITY: 'prototype tested' ≠ 'operational'; 'announced' ≠ 'deployed'\n"
                "- CHAIN-OF-THOUGHT: Before writing each paragraph, reason step by step — "
                "identify the technology actor, connect to strategic advantage or control mechanism, "
                "then assess timeline and economic-security implication. Do not jump to conclusions.\n"
                "**KEY TAKEAWAYS** (append 3 bullets at the very end of this section):\n"
                "  • Critical chokepoint or single-point dependency identified today\n"
                "  • Strategic actor gaining or losing advantage + timeline\n"
                "  • Investment/policy implication (export controls, procurement, supply chain)"
            ),
        },
        3: {
            "header": "**Geopolitical Events**",
            "word_target": "200-250 words",
            "domain_rag_query": (
                "geopolitical military conflict great power competition NATO China Russia "
                "Indo-Pacific Middle East escalation regional security proxy war territorial"
            ),
            "focus": (
                "- Military posture shifts: troop movements, exercises with strategic signaling, "
                "procurement decisions, doctrine changes\n"
                "- Great power competition dynamics: US-China, Russia-NATO, regional hegemony\n"
                "- Peripheral strategic events (Myanmar, Horn of Africa, Central Asia, Arctic, "
                "Pacific islands) — flag and explain geographic/resource amplifier\n"
                "- Escalation/de-escalation: what SPECIFICALLY changed vs. last week's trajectory?"
            ),
            "requirements": (
                "- PRIORITIZATION: use implicit scoring — Immediate Impact × Escalation Potential × "
                "Actor Weight. Surface the reasoning ('this matters because X actor controls Y chokepoint')\n"
                "- PERIPHERAL STRATEGIC BONUS: for events in Myanmar/Horn of Africa/Central Asia/Arctic — "
                "explain why seemingly minor location has outsized strategic significance\n"
                "- ACTOR MOTIVATIONS: never impersonal — 'Russia is doing X because Y', "
                "not 'tensions are rising in the region'\n"
                "- PATTERN BREAK: explicitly flag if event breaks a 5+ year pattern vs. confirms trend. "
                "Pattern breaks score highest in priority\n"
                "- SOURCE FIDELITY: 'military exercises near border' ≠ 'invasion preparation'; "
                "'diplomatic signal' ≠ 'policy decision'\n"
                "- CHAIN-OF-THOUGHT: Before writing each paragraph, reason step by step — "
                "identify the actor and their motivation, connect to regional power dynamics or alliance structure, "
                "then assess escalation potential. Do not jump to conclusions.\n"
                "**KEY TAKEAWAYS** (append 3 bullets at the very end of this section):\n"
                "  • Primary actor + stated vs. actual motivation\n"
                "  • Pattern break or trend confirmation (flag explicitly)\n"
                "  • Escalation/de-escalation signal + next concrete indicator to watch"
            ),
        },
        4: {
            "header": "**Economic Events**",
            "word_target": "200-250 words",
            "domain_rag_query": (
                "sanctions energy markets supply chain commodity prices trade policy "
                "central bank monetary policy inflation economic coercion OPEC"
            ),
            "focus": (
                "- Sanctions: new designations, evasion mechanisms (name intermediaries/vessels "
                "if publicly confirmed), enforcement actions and gaps\n"
                "- Energy: OPEC production decisions, LNG dynamics, oil price drivers, "
                "pipeline geopolitics, transition policy\n"
                "- Supply chain stress: critical materials, semiconductor shortage, "
                "shipping route disruptions, strategic stockpiles\n"
                "- Macro: central bank decisions with geopolitical drivers, sovereign debt risk "
                "in conflict zones, currency stress"
            ),
            "requirements": (
                "- CAUSAL CHAIN: always trace economic event → political consequence → security implication. "
                "Full chain required, not just the headline\n"
                "- DATA PRECISION: note reference period for any figure "
                "('inflation at X% as of [month/quarter from source]' — not presented as real-time)\n"
                "- SANCTIONS MECHANICS: explain HOW evasion works (which jurisdiction, which instrument) — "
                "not just that evasion is occurring\n"
                "- GEOPOLITICAL NEXUS: connect economic pressure to regime stability, proxy funding "
                "capacity, or military procurement constraints\n"
                "- SOURCE FIDELITY: 'proposed tariff' ≠ 'implemented'; 'announced' ≠ 'in force today'\n"
                "- CHAIN-OF-THOUGHT: Before writing each paragraph, reason step by step — "
                "identify the economic event, trace the causal chain to political consequence, "
                "then assess security implication. Do not jump to conclusions.\n"
                "**KEY TAKEAWAYS** (append 3 bullets at the very end of this section):\n"
                "  • Primary economic pressure point + mechanism (sanctions, energy, trade)\n"
                "  • Causal chain: economic event → political consequence → security implication\n"
                "  • Market signal: ticker/commodity/currency most directly impacted with causal justification"
            ),
        },
    }

    # ── D: Epistemic constants ─────────────────────────────────────────────

    _DAILY_GUARDRAIL = (
        "\n**GUARDRAIL**: If no material developments exist for this domain today, write:\n"
        "> 'No significant developments in this reporting period for [domain].'\n"
        "Add only the most recent relevant historical context from RAG sources.\n"
        "Do NOT pad with generic background. Stop when the section's content is exhausted.\n"
    )

    _DAILY_CITATION_RULES = (
        "**CITATION**: Every factual claim → [Article N]. "
        "Historical context → [RAG: Title, Date]. Analytical inference → [Assessment].\n"
        "**CONFIDENCE**: [High confidence] = ≥2 independent sources; "
        "[Medium confidence] = single credible source; [Unverified/Reported] = single source.\n"
        "**EPISTEMIC DISCIPLINE**:\n"
        "  • VERIFIED FACT (named source, directly stated): cite plainly → [Article N]\n"
        "  • SINGLE SOURCE / UNVERIFIED: → 'reportedly', 'according to [Source]', 'allegedly'\n"
        "  • ASSESSMENT (analytical inference): → 'appears to', 'suggests', 'likely', "
        "'may indicate' → [Assessment]\n"
        "  • FORECAST: ALWAYS conditional → 'could', 'may', 'if X then Y'. Never certain.\n"
        "**SOURCE FIDELITY** — FEW-SHOT:\n"
        "  ✗ 'China seized the disputed zone' → ✓ 'Chinese forces reportedly entered the zone' [Article N]\n"
        "  ✗ 'The group launched an attack' → ✓ 'The intrusion, attributed to [group] by [researcher], targeted...'\n"
        "  ✗ 'NATO decided to deploy' → ✓ 'NATO members are in discussions over deployment options'\n"
        "  Rule: use the source's exact verb. Exercises ≠ invasion. Discussed ≠ decided.\n"
        "**TEMPORAL**: Date events concretely. Calculate deadlines from event date, not today's date.\n"
        "**STYLE**: Analytical, neutral. ZERO moral/narrative language: "
        "'alarming', 'reckless', 'provocative', 'desperate', 'brutal'. "
        "Report events; do not dramatize.\n"
    )

    # ── E1: Domain prompt builder ──────────────────────────────────────────

    def _build_daily_domain_prompt(
        self,
        section_num: int,
        shared_context: str,
        previous_sections: str,
        report_date: str,
        start_date: str,
        macro_summary: str = "",
    ) -> str:
        defn = self._DAILY_SECTION_DEFS[section_num]
        prev_block = ""
        if previous_sections.strip():
            prev_block = (
                "\n**SECTIONS ALREADY WRITTEN** "
                "(read for cross-domain consistency — do NOT repeat their content):\n"
                f"{previous_sections.strip()}\n\n---\n"
            )
        macro_block = f"\n**MACRO SNAPSHOT**: {macro_summary}\n\n" if macro_summary else ""

        return (
            "You are a senior intelligence analyst (ISW/RAND/CSIS caliber). "
            "Write a single section of a daily intelligence brief.\n\n"
            f"**REPORT DATE**: {report_date} | **ANALYSIS PERIOD**: {start_date} – {report_date}\n"
            f"{shared_context}"
            f"{prev_block}"
            f"{macro_block}"
            f"**YOUR TASK**: Write ONLY the section below ({defn['word_target']}).\n\n"
            f"**Focus Areas**:\n{defn['focus']}\n\n"
            f"**Analytical Requirements**:\n{defn['requirements']}\n\n"
            f"{self._DAILY_GUARDRAIL}\n"
            f"{self._DAILY_CITATION_RULES}\n\n"
            f"Output ONLY this section. Begin directly with \"{defn['header']}\".\n"
            "Do not add section headers before or after — just the content of this section."
        )

    # ── E2: Synthesis prompt builder ───────────────────────────────────────

    def _build_daily_synthesis_prompt(
        self,
        d1: str,
        d2: str,
        d3: str,
        d4: str,
        macro_dashboard_text: str,
        report_date: str,
        narrative_xml: str,
    ) -> str:
        def extract_section_input(text: str, fallback_chars: int = 600) -> str:
            """Extract **KEY TAKEAWAYS** block if present, else return first fallback_chars chars."""
            import re
            match = re.search(r'\*\*KEY TAKEAWAYS\*\*.*?(?=\n\n|\Z)', text, re.DOTALL)
            if match:
                return match.group(0).strip()
            return text[:fallback_chars] + ("\n[...truncated...]" if len(text) > fallback_chars else "")

        return (
            "You are a senior intelligence analyst. Four domain sections have been written. "
            "Your task: produce the OPENING and CLOSING sections of today's daily intelligence brief.\n\n"
            "**DOMAIN SECTIONS** (Key Takeaways extracted from each — synthesize across all four, "
            "do not repeat their content verbatim):\n\n"
            f"[CYBERSECURITY]:\n{extract_section_input(d1)}\n\n"
            f"[TECHNOLOGY]:\n{extract_section_input(d2)}\n\n"
            f"[GEOPOLITICAL EVENTS]:\n{extract_section_input(d3)}\n\n"
            f"[ECONOMIC EVENTS]:\n{extract_section_input(d4)}\n\n"
            f"**MACRO CONTEXT**:\n{macro_dashboard_text or 'Unavailable.'}\n\n"
            f"**ACTIVE STORYLINES**:\n{narrative_xml or 'No active storylines.'}\n\n"
            "---\n\n"
            "Generate EXACTLY the following, using these output markers for assembly:\n\n"
            "<<ES>>\n"
            "**Executive Summary**\n"
            "[200-300 words BLUF. Bold key actors. [Article N] references. "
            "Cover top development from each domain. Active voice; hedge unverified with 'reportedly'. "
            "Connect cross-domain dynamics explicitly.]\n"
            "<<ES_END>>\n\n"
            "<<CLOSING>>\n"
            "**Trend Analysis**\n"
            "[250-300 words. Connect today's events to historical patterns from RAG context and storylines. "
            "Explicitly state: does each key event CONFIRM or BREAK existing trends? "
            "Reference momentum shifts in active storylines. "
            "Focus on second-order effects and 3-6 month trajectory.]\n\n"
            "---\n\n"
            "**Actionable Insights: Investment Implications**\n"
            "[CROSS-VALIDATION: Before writing this section, verify each ticker you plan to mention "
            "is directly supported by evidence in the DOMAIN SECTIONS KEY TAKEAWAYS above. "
            "If a ticker lacks a clear causal link to a specific development in D1-D4, omit it.]\n"
            "[Top 3 developments across all domains. For each:\n"
            "**Level 1 - Direct Beneficiaries**: specific companies, exact tickers, "
            "contract/revenue impact, quarter timeline\n"
            "**Level 2 - Supply Chain & Correlated Markets**: full causal chain "
            "(event → input shortage → company revision → end market effect)\n"
            "**Level 3 - Macro Market Impacts**: currencies, bonds, commodities, capital flows\n"
            "Each signal: tickers, concrete catalyst with figures, timing, next monitor date.]\n\n"
            "---\n\n"
            "**Strategic Storyline Tracker**\n"
            "[Top 5 storylines by momentum. For each:\n"
            "- **Status**: narrative_status + momentum direction (accelerating/stable/decelerating)\n"
            "- **Today's Impact**: how today's domain sections specifically advance or challenge this storyline\n"
            "- **Cross-Domain Links**: connected storylines + intersection meaning\n"
            "- **Watch Indicators**: next concrete event, metric, or date to monitor]\n"
            "<<CLOSING_END>>\n\n"
            f"**TODAY**: {report_date}\n"
            "Output ONLY the two blocks above (<<ES>>...<<ES_END>> and <<CLOSING>>...<<CLOSING_END>>). "
            "Active voice; hedge unverified claims. Do not dramatize."
        )

    # ── F2/F3: Per-domain RAG reranking ───────────────────────────────────

    def _rag_for_domain(
        self,
        global_chunks: List[Dict[str, Any]],
        domain_query: str,
        top_k: int = 8,
    ) -> List[Dict[str, Any]]:
        """
        Rerank the pre-fetched global RAG pool for a specific domain.
        CPU-only cross-encoder reranking — no additional DB or embedding calls.
        """
        if self.enable_reranking and global_chunks:
            try:
                return self._rerank_chunks(domain_query, global_chunks, top_k=top_k)
            except Exception as e:
                logger.warning(f"Domain reranking failed ({e}), using top-k fallback")
                return global_chunks[:top_k]
        return global_chunks[:top_k]

    # ── G: Sequential 5-call orchestration ────────────────────────────────

    def generate_report_sequential(
        self,
        focus_areas: Optional[List[str]] = None,
        days: int = 1,
        from_time: Optional[datetime] = None,
        to_time: Optional[datetime] = None,
        top_articles: int = 60,
        min_similarity: float = 0.30,
        min_fallback: int = 10,
        macro_dashboard_text: str = "",
        macro_context_text: str = "",
    ) -> Dict[str, Any]:
        """
        Sequential 5-call daily report (replaces single-call generate_report()).
        Output format: identical to current generate_report() — zero frontend changes required.

        Calls:
          D1 Cybersecurity    — focused context, no prior sections
          D2 Technology       — + D1 for cross-domain consistency
          D3 Geopolitical     — + D1+D2
          D4 Economic Events  — + D1+D2+D3
          Synthesis           — KEY TAKEAWAYS(D1-D4) + macro + storylines → ES + closing sections
        """
        from datetime import timedelta
        import re as _re

        now = datetime.now()
        report_date = now.strftime('%B %d, %Y')
        start_date = (now - timedelta(days=days)).strftime('%B %d, %Y')
        datetime_str = now.strftime('%Y-%m-%d %H:%M:%S')

        domain_rag_queries = [d["domain_rag_query"] for d in self._DAILY_SECTION_DEFS.values()]

        # ── STEP 1: Articles ──────────────────────────────────────────────
        logger.info("\n[SEQ-1] Fetching and filtering articles...")
        all_articles = self.db.get_recent_articles(
            days=days, from_time=from_time, to_time=to_time
        )
        recent_articles = self.filter_relevant_articles(
            articles=all_articles,
            focus_areas=focus_areas or domain_rag_queries,
            top_n=top_articles,
            min_similarity=min_similarity,
            min_fallback=min_fallback,
        )
        if not recent_articles:
            return {"success": False, "error": "No relevant articles found"}
        logger.info(f"  ✓ {len(recent_articles)} relevant articles")

        # ── STEP 2: Global RAG pool + per-domain reranking ────────────────
        logger.info("\n[SEQ-2] Fetching global RAG pool (4 domain queries)...")
        all_rag: List[Dict[str, Any]] = []
        for q in domain_rag_queries:
            all_rag.extend(self.get_rag_context(q, top_k=10))
        global_rag = self.deduplicate_chunks_advanced(all_rag)
        logger.info(f"  ✓ {len(global_rag)} unique RAG chunks")

        d1_rag = self._rag_for_domain(global_rag, domain_rag_queries[0], top_k=8)
        d2_rag = self._rag_for_domain(global_rag, domain_rag_queries[1], top_k=8)
        d3_rag = self._rag_for_domain(global_rag, domain_rag_queries[2], top_k=8)
        d4_rag = self._rag_for_domain(global_rag, domain_rag_queries[3], top_k=8)

        # ── STEP 2.5: Narrative storylines ────────────────────────────────
        logger.info("\n[SEQ-2.5] Fetching narrative storylines...")
        narrative_ctx = self._get_narrative_context(days=days, top_n=10)
        narrative_xml = self._format_narrative_xml(narrative_ctx)

        # ── STEP 3: Shared context builders ──────────────────────────────
        articles_text = self.format_recent_articles(recent_articles)
        macro_summary = macro_context_text[:800] if macro_context_text else ""

        def _section_context(rag_domain: List[Dict[str, Any]]) -> str:
            return (
                f"\n**INTELLIGENCE SOURCES** (last {days} day(s)):\n{articles_text}\n\n"
                f"**HISTORICAL CONTEXT (domain-specific):**\n"
                f"{self.format_rag_context(rag_domain)}\n\n"
                f"**ACTIVE STORYLINES:**\n{narrative_xml}\n"
            )

        # ── STEP 4: Sequential domain calls ──────────────────────────────
        logger.info("\n[SEQ-4] Sequential domain calls...")

        d1 = self._call_llm(
            self._build_daily_domain_prompt(1, _section_context(d1_rag), "", report_date, start_date, macro_summary),
            temperature=0.25, max_tokens=8192, timeout=120, label="D1-Cyber",
        )
        d2 = self._call_llm(
            self._build_daily_domain_prompt(2, _section_context(d2_rag), d1, report_date, start_date, macro_summary),
            temperature=0.25, max_tokens=8192, timeout=120, label="D2-Tech",
        )
        d3 = self._call_llm(
            self._build_daily_domain_prompt(3, _section_context(d3_rag), d1 + "\n\n" + d2, report_date, start_date, macro_summary),
            temperature=0.25, max_tokens=8192, timeout=120, label="D3-Geo",
        )
        d4 = self._call_llm(
            self._build_daily_domain_prompt(4, _section_context(d4_rag), d1 + "\n\n" + d2 + "\n\n" + d3, report_date, start_date, macro_summary),
            temperature=0.25, max_tokens=8192, timeout=120, label="D4-Econ",
        )

        # ── STEP 5: Synthesis ─────────────────────────────────────────────
        logger.info("\n[SEQ-5] Synthesis (ES + Trend + Signals + Storylines)...")
        synthesis_raw = self._call_llm(
            self._build_daily_synthesis_prompt(d1, d2, d3, d4, macro_dashboard_text, report_date, narrative_xml),
            temperature=0.2, max_tokens=8192, timeout=150, label="Synthesis",
        )

        # Split synthesis on markers (fallback: raw text if markers missing)
        es_match = _re.search(r'<<ES>>(.*?)<<ES_END>>', synthesis_raw, _re.DOTALL)
        cl_match = _re.search(r'<<CLOSING>>(.*?)<<CLOSING_END>>', synthesis_raw, _re.DOTALL)
        executive_summary = es_match.group(1).strip() if es_match else synthesis_raw[:1500].strip()
        closing_sections = cl_match.group(1).strip() if cl_match else synthesis_raw[1500:].strip()

        # ── STEP 6: Assemble — format preservato al 100% ─────────────────
        sep = "\n\n---\n\n"
        report_text = (
            f"# Intelligence Report - {datetime_str}\n\n"
            f"## Daily Intelligence Briefing\n\n"
            + (f"{macro_dashboard_text}\n\n---\n\n" if macro_dashboard_text else "")
            + executive_summary
            + sep
            + "**Key Developments by Category**\n\n"
            + d1.strip() + "\n\n"
            + d2.strip() + "\n\n"
            + d3.strip() + "\n\n"
            + d4.strip()
            + sep
            + closing_sections
        )

        logger.info(f"\n  ✓ Sequential report assembled: {len(report_text.split()):,} words")

        # Build sources in the same format as generate_report() so save_report() / frontend work identically
        recent_articles_sources = [
            {
                "article_id": a.get("id", 0),
                "title": a.get("title", ""),
                "link": a.get("link", ""),
                "source": a.get("source", ""),
                "published_date": str(a.get("published_date", "")),
                "relevance_score": a.get("relevance_score") or a.get("similarity"),
            }
            for a in recent_articles
        ]
        historical_sources = [
            {
                "article_id": c.get("article_id", 0),
                "title": c.get("title", ""),
                "link": c.get("link", ""),
                "source": c.get("source", ""),
                "published_date": str(c.get("published_date", "")),
                "relevance_score": c.get("similarity"),
            }
            for c in global_rag[:20]
        ]

        return {
            "success": True,
            "report_text": report_text,
            "timestamp": now.isoformat(),
            "metadata": {
                "architecture": "sequential_5call",
                "model_used": self.model.model_name,
                "recent_articles_count": len(recent_articles),
                "historical_chunks_count": len(global_rag),
                "narrative_storylines": len(narrative_ctx.get("storylines", [])),
                "days_covered": days,
            },
            "sources": {
                "recent_articles": recent_articles_sources,
                "historical_context": historical_sources,
            },
        }


if __name__ == "__main__":
    import sys
    import argparse

    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description="Generate intelligence report with optional query expansion"
    )

    parser.add_argument(
        '--no-query-expansion',
        action='store_true',
        help='Disable automatic query expansion for RAG (default: enabled)'
    )

    parser.add_argument(
        '--expansion-variants',
        type=int,
        default=2,
        help='Number of query variants to generate per focus area (default: 2)'
    )

    parser.add_argument(
        '--dedup-similarity',
        type=float,
        default=0.98,
        help='Similarity threshold for chunk deduplication, range 0-1 (default: 0.98)'
    )

    parser.add_argument(
        '--output-dir',
        type=str,
        default='reports',
        help='Directory to save reports (default: reports)'
    )

    parser.add_argument(
        '--no-save',
        action='store_true',
        help='Do not save report to file (default: saves to file)'
    )

    parser.add_argument(
        '--no-db',
        action='store_true',
        help='Do not save report to database (default: saves to DB)'
    )

    args = parser.parse_args()

    # Initialize generator with query expansion settings
    generator = ReportGenerator(
        enable_query_expansion=not args.no_query_expansion,
        expansion_variants=args.expansion_variants,
        dedup_similarity=args.dedup_similarity
    )

    # Custom focus areas (optional)
    focus_areas = [
        "cybersecurity threats and data breaches",
        "artificial intelligence developments",
        "geopolitical tensions in Asia and Middle East",
        "economic policy changes in Europe"
    ]

    # Generate and save report
    report = generator.run_daily_report(
        focus_areas=focus_areas,
        save=not args.no_save,
        save_to_db=not args.no_db,
        output_dir=args.output_dir
    )

    if report['success']:
        print("\n" + "=" * 80)
        print("GENERATED REPORT")
        print("=" * 80)
        print(report['report_text'])
        print("\n" + "=" * 80)
        sys.exit(0)
    else:
        print(f"\nError: {report.get('error')}")
        sys.exit(1)
