"""
Narrative Processor - Narrative Engine Core (v2)

Replaces the legacy StoryManager with a multi-stage pipeline:
1. Micro-clustering: group near-duplicate articles into "unique events"
2. Adaptive matching: assign events to existing storylines with temporal decay
3. Discovery: HDBSCAN on orphaned events to find new emerging storylines
4. LLM evolution: update storyline summaries and titles via Gemini
5. Graph building: compute entity-overlap edges between storylines
6. Decay: age out inactive storylines through narrative_status lifecycle
"""

import os
import re
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple, Any, Set
from collections import defaultdict, Counter
import numpy as np
from psycopg2.extras import Json

try:
    from sklearn.cluster import HDBSCAN
    HDBSCAN_AVAILABLE = True
except ImportError:
    HDBSCAN_AVAILABLE = False

try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

from ..storage.database import DatabaseManager
from ..utils.logger import get_logger

logger = get_logger(__name__)

# Model name constant
EMBEDDING_MODEL_NAME = 'paraphrase-multilingual-MiniLM-L12-v2'

# ---------------------------------------------------------------------------
# Post-clustering relevance validation
# Storylines whose title+summary match OFF-TOPIC patterns are archived.
# Patterns that MUST appear (at least one) to be considered on-scope.
# ---------------------------------------------------------------------------
_SCOPE_KEYWORDS: re.Pattern = re.compile(
    r'(?:geopoliti|politi|diplom|sanzi|embargo|govern|parlamento|elezioni|voto'
    r'|\bNATO\b|\bONU\b|\bUE\b|\bEU\b|\bG7\b|\bG20\b'
    r'|difesa|militar|esercit|guerra|conflict|armat|missile|nucle|drone'
    r'|cyber|hack|malware|ransomware|phishing|zero.day|\bAPT\b|\bCISA\b|\bNSA\b'
    r'|intelligen|spionag|sorveglianz|\bSIGINT\b|\bHUMINT\b|\bOSINT\b'
    r'|spazi[ao]|orbit|satellit|launch|\bNASA\b|\bESA\b|SpaceX'
    r'|energi[ae]|petrol|\bgas\b|\bOPEC\b|oleodott|gasdott|rinnovabil|nuclear'
    r'|econom|finanz|\bPIL\b|\bGDP\b|inflaz|banca.central|\bFMI\b|\bIMF\b|\bBCE\b|\bFED\b|Treasury|debito.pubblic'
    r'|supply.chain|semiconduttor|\bchip\b|Taiwan|\bTSMC\b|mineral[ie].critic|terre.rare'
    r'|terroris|jihad|estremis|radicalizz'
    r'|migra|rifugiat|frontier|Frontex'
    r'|\bCina\b|\bChina\b|\bRussia\b|\bIran\b|Corea.Nord|North.Korea|Ucraina|Ukraine|\bIsrael\b|\bGaza\b'
    r'|Pentagon|\bCIA\b|\bMI6\b|Mossad|\bFSB\b|\bGRU\b|\bDGSE\b|\bAISE\b|Creml)',
    re.IGNORECASE
)

_OFF_TOPIC_PATTERNS: List[re.Pattern] = [
    re.compile(p, re.IGNORECASE) for p in [
        r'\b(?:NBA|NFL|NHL|MLB|MLS|UFC|ATP|WTA|FIFA|UEFA|ICC|cricket|IPL|Serie A|Premier League|Champions League|Bundesliga|Ligue 1|La Liga)\b',
        r'\b(?:Grammy|Oscar|Emmy|Golden Globe|BAFTA|Netflix|Spotify|box.office|blockbuster|Marvel|K-pop|BTS|Taylor Swift)\b',
        r'\b(?:celebrity|gossip|fashion|Kardashian|reality TV|Love Island|Met Gala|red carpet)\b',
        r'\b(?:Real Madrid|Barcelona|Juventus|Liverpool|Arsenal|Chelsea|Ronaldo|Messi|Guardiola)\b',
        r'\b(?:recipe|ricetta|cooking|chef|restaurant|ristorante|food blog)\b',
        r'\b(?:tourist|turismo|travel guide|hotel review|vacation|vacanz)\b',
    ]
]


class NarrativeProcessor:
    """
    Narrative engine that manages storyline lifecycle:
    micro-clustering → matching → discovery → LLM evolution → graph → decay

    Key concepts:
    - Event: a cluster of near-duplicate articles about the same fact
    - Storyline: an ongoing narrative thread grouping related events over time
    - narrative_status lifecycle: emerging → active → stabilized → archived
    - Graph edges: entity overlap connections between storylines
    """

    # --- Thresholds (tunable) ---
    MICRO_CLUSTER_THRESHOLD = 0.90   # Cosine similarity for near-duplicate grouping
    MATCH_THRESHOLD = 0.75           # Min hybrid score to match a storyline
    TIME_DECAY_FACTOR = 0.05         # Score penalty per day of storyline inactivity
    ENTITY_BOOST = 0.10              # Bonus when entity Jaccard > 0.3
    ENTITY_JACCARD_THRESHOLD = 0.30  # Min Jaccard for entity boost / graph edges
    HDBSCAN_MIN_CLUSTER_SIZE = 2     # Min events to form a new storyline
    HDBSCAN_MIN_SAMPLES = 2
    DRIFT_WEIGHT_OLD = 0.85          # Weight for existing storyline embedding
    DRIFT_WEIGHT_NEW = 0.15          # Weight for new event embedding
    MOMENTUM_DECAY_FACTOR = 0.7      # Weekly decay multiplier
    LLM_RATE_LIMIT_SECONDS = 0.5     # Pause between Gemini calls

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        gemini_api_key: Optional[str] = None,
        skip_llm: bool = False
    ):
        self.db = db_manager or DatabaseManager()
        self.skip_llm = skip_llm

        # Lazy-loaded embedding model
        self._embedding_model = None

        # Initialize Gemini
        self.gemini_available = False
        if not skip_llm and GEMINI_AVAILABLE:
            api_key = (gemini_api_key or os.getenv('GEMINI_API_KEY', '')).strip()
            if api_key:
                genai.configure(api_key=api_key, transport='rest')
                self.model = genai.GenerativeModel('gemini-2.5-flash')
                self.gemini_available = True
                logger.info("NarrativeProcessor: Gemini initialized for summary evolution")
            else:
                logger.warning("NarrativeProcessor: No GEMINI_API_KEY found, LLM features disabled")
        elif skip_llm:
            logger.info("NarrativeProcessor: LLM features explicitly disabled (--skip-llm)")

        logger.info("NarrativeProcessor initialized")

    @property
    def embedding_model(self):
        """Lazy-load SentenceTransformer for summary_vector encoding."""
        if self._embedding_model is None:
            from sentence_transformers import SentenceTransformer
            self._embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
            logger.info(f"Loaded embedding model: {EMBEDDING_MODEL_NAME}")
        return self._embedding_model

    # =========================================================================
    # PUBLIC INTERFACE
    # =========================================================================

    def process_daily_batch(self, days: int = 1, dry_run: bool = False) -> Dict[str, Any]:
        """
        Main orchestrator. Called by the daily pipeline after load_to_database.

        Args:
            days: Time window for fetching unassigned articles
            dry_run: If True, analyze without writing to DB

        Returns:
            Stats dict with counters for each stage
        """
        logger.info(f"=== Narrative Processing (days={days}, dry_run={dry_run}) ===")
        stats = {
            'articles_loaded': 0,
            'micro_clusters': 0,
            'events_matched': 0,
            'events_orphaned': 0,
            'new_storylines': 0,
            'summaries_evolved': 0,
            'validated_on_scope': 0,
            'archived_off_topic': 0,
            'graph_edges_updated': 0,
            'decay_stats': {},
        }

        # 1. Load unassigned articles with embeddings
        articles = self.db.get_all_article_embeddings(
            days=days,
            exclude_assigned=True
        )
        stats['articles_loaded'] = len(articles)

        if not articles:
            logger.info("No unassigned articles found. Nothing to process.")
            return stats

        logger.info(f"Loaded {len(articles)} unassigned articles")

        # 2. Micro-clustering → unique events
        events = self._create_micro_clusters(articles)
        stats['micro_clusters'] = len(events)
        logger.info(f"Micro-clustering: {len(articles)} articles → {len(events)} unique events")

        # 3. Load active storylines for matching
        active_storylines = self._load_active_storylines()
        logger.info(f"Active storylines for matching: {len(active_storylines)}")

        # 4. Match events to existing storylines
        matched_events = []     # (event, storyline_id) tuples
        orphaned_events = []

        for event in events:
            match = self._find_best_match(event, active_storylines)
            if match:
                matched_events.append((event, match['storyline_id']))
            else:
                orphaned_events.append(event)

        stats['events_matched'] = len(matched_events)
        stats['events_orphaned'] = len(orphaned_events)
        logger.info(f"Matching: {len(matched_events)} matched, {len(orphaned_events)} orphaned")

        if dry_run:
            # In dry-run, estimate new storylines from HDBSCAN without writing
            if len(orphaned_events) >= self.HDBSCAN_MIN_CLUSTER_SIZE:
                embeddings = np.array([e['embedding'] for e in orphaned_events])
                norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
                embeddings_norm = embeddings / np.maximum(norms, 1e-10)
                hdb = HDBSCAN(
                    min_cluster_size=self.HDBSCAN_MIN_CLUSTER_SIZE,
                    min_samples=self.HDBSCAN_MIN_SAMPLES,
                    metric='euclidean'
                )
                labels = hdb.fit_predict(embeddings_norm)
                n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
                stats['new_storylines'] = n_clusters
            logger.info(f"[DRY RUN] Would create ~{stats['new_storylines']} new storylines")
            return stats

        # 5. Assign matched events to their storylines
        updated_storyline_ids = set()
        for event, storyline_id in matched_events:
            self._assign_event_to_storyline(event, storyline_id)
            updated_storyline_ids.add(storyline_id)

        # 6. Cluster orphaned events with HDBSCAN → new storylines
        new_storyline_ids = self._cluster_residuals(orphaned_events)
        stats['new_storylines'] = len(new_storyline_ids)
        updated_storyline_ids.update(new_storyline_ids)

        # 7. Evolve narrative summaries for all updated storylines
        if not self.skip_llm and self.gemini_available:
            for sid in updated_storyline_ids:
                try:
                    self._evolve_narrative_summary(sid)
                    stats['summaries_evolved'] += 1
                except Exception as e:
                    logger.error(f"Failed to evolve summary for storyline #{sid}: {e}")

        # 7.5. Post-clustering relevance validation (after LLM so we have titles+summaries)
        validation_stats = self._validate_storyline_relevance(updated_storyline_ids)
        stats['validated_on_scope'] = validation_stats['validated']
        stats['archived_off_topic'] = validation_stats['archived_off_topic']

        # Remove archived storylines from further processing (graph updates)
        if validation_stats['archived_off_topic'] > 0:
            # Reload which ones are still active
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT id FROM storylines
                        WHERE id = ANY(%s)
                        AND narrative_status <> 'archived'
                    """, (list(updated_storyline_ids),))
                    still_active = {row[0] for row in cur.fetchall()}
            updated_storyline_ids = still_active

        # 8. Update graph connections for all updated storylines
        for sid in updated_storyline_ids:
            try:
                edges = self._update_graph_connections(sid)
                stats['graph_edges_updated'] += edges
            except Exception as e:
                logger.error(f"Failed to update graph for storyline #{sid}: {e}")

        # 9. Apply decay to inactive storylines
        stats['decay_stats'] = self._apply_decay()

        logger.info(f"=== Narrative Processing Complete ===")
        logger.info(f"  Events: {stats['micro_clusters']} ({stats['events_matched']} matched, {stats['events_orphaned']} orphaned)")
        logger.info(f"  New storylines: {stats['new_storylines']}")
        logger.info(f"  Summaries evolved: {stats['summaries_evolved']}")
        logger.info(f"  Relevance validation: {stats['validated_on_scope']} on-scope, {stats['archived_off_topic']} archived off-topic")
        logger.info(f"  Graph edges updated: {stats['graph_edges_updated']}")
        logger.info(f"  Decay: {stats['decay_stats']}")

        return stats

    # =========================================================================
    # STAGE 1: MICRO-CLUSTERING
    # =========================================================================

    def _create_micro_clusters(self, articles: List[Dict]) -> List[Dict]:
        """
        Group near-duplicate articles (cosine sim > 0.90) into unique events.

        Returns list of event dicts:
        - embedding: normalized centroid of the cluster
        - entities: union of entities from all articles
        - article_ids: list of original article IDs
        - representative_title: title of article with most entities
        - category: most common category
        """
        if not articles:
            return []

        n = len(articles)
        if n == 1:
            return [self._article_to_event(articles[0])]

        # Build cosine similarity matrix
        embeddings = np.array([a['embedding'] for a in articles])
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        embeddings_norm = embeddings / np.maximum(norms, 1e-10)
        sim_matrix = embeddings_norm @ embeddings_norm.T

        # Greedy clustering: assign each article to a cluster
        assigned = [False] * n
        clusters = []

        for i in range(n):
            if assigned[i]:
                continue

            # Start new cluster with article i
            cluster_indices = [i]
            assigned[i] = True

            for j in range(i + 1, n):
                if assigned[j]:
                    continue
                if sim_matrix[i, j] >= self.MICRO_CLUSTER_THRESHOLD:
                    cluster_indices.append(j)
                    assigned[j] = True

            clusters.append(cluster_indices)

        # Convert clusters to events
        events = []
        for indices in clusters:
            cluster_articles = [articles[i] for i in indices]
            events.append(self._articles_to_event(cluster_articles))

        return events

    def _article_to_event(self, article: Dict) -> Dict:
        """Convert a single article to an event dict."""
        entities = self._extract_entity_list(article.get('entities', {}))
        return {
            'embedding': np.array(article['embedding']),
            'entities': entities,
            'article_ids': [article['id']],
            'representative_title': article['title'],
            'category': article.get('category'),
        }

    def _articles_to_event(self, articles: List[Dict]) -> Dict:
        """Merge multiple articles into a single event."""
        # Centroid embedding (normalized)
        embeddings = np.array([a['embedding'] for a in articles])
        centroid = embeddings.mean(axis=0)
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroid = centroid / norm

        # Union of entities
        all_entities = []
        entity_counts = Counter()
        for a in articles:
            ents = self._extract_entity_list(a.get('entities', {}))
            all_entities.extend(ents)
            entity_counts.update(ents)

        # Representative title: article with most entities
        best_article = max(
            articles,
            key=lambda a: len(self._extract_entity_list(a.get('entities', {})))
        )

        # Most common category
        categories = [a.get('category') for a in articles if a.get('category')]
        category = Counter(categories).most_common(1)[0][0] if categories else None

        return {
            'embedding': centroid,
            'entities': list(entity_counts.keys()),
            'article_ids': [a['id'] for a in articles],
            'representative_title': best_article['title'],
            'category': category,
        }

    # =========================================================================
    # STAGE 2: ADAPTIVE MATCHING
    # =========================================================================

    def _load_active_storylines(self) -> List[Dict]:
        """Load all storylines with narrative_status in ('emerging', 'active') for matching."""
        with self.db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        id, title, current_embedding, key_entities,
                        last_update, narrative_status, article_count, momentum_score
                    FROM storylines
                    WHERE narrative_status IN ('emerging', 'active')
                    ORDER BY momentum_score DESC
                """)
                rows = cur.fetchall()

        storylines = []
        for row in rows:
            embedding = row[2]
            if embedding is not None:
                if hasattr(embedding, 'tolist'):
                    embedding = embedding.tolist()
                elif not isinstance(embedding, list):
                    embedding = list(embedding)

            storylines.append({
                'storyline_id': row[0],
                'title': row[1],
                'current_embedding': np.array(embedding) if embedding else None,
                'key_entities': set(e.lower() for e in (row[3] or [])),
                'last_update': row[4],
                'narrative_status': row[5],
                'article_count': row[6],
                'momentum_score': row[7],
            })

        return storylines

    def _find_best_match(
        self,
        event: Dict,
        active_storylines: List[Dict]
    ) -> Optional[Dict]:
        """
        Find the best matching storyline for an event using hybrid scoring:
        score = cosine_sim - time_decay_penalty + entity_boost

        Returns the best match dict or None if below threshold.
        """
        if not active_storylines:
            return None

        event_embedding = event['embedding']
        event_entities = set(e.lower() for e in event['entities'])
        now = datetime.now()

        best_match = None
        best_score = -1.0

        for storyline in active_storylines:
            if storyline['current_embedding'] is None:
                continue

            # Cosine similarity
            s_emb = storyline['current_embedding']
            dot = np.dot(event_embedding, s_emb)
            norm_product = np.linalg.norm(event_embedding) * np.linalg.norm(s_emb)
            if norm_product == 0:
                continue
            cosine_sim = dot / norm_product

            # Time decay penalty
            days_inactive = 0
            if storyline['last_update']:
                delta = now - storyline['last_update']
                days_inactive = max(0, delta.total_seconds() / 86400)
            time_penalty = self.TIME_DECAY_FACTOR * days_inactive

            # Entity Jaccard boost
            s_entities = storyline['key_entities']
            entity_boost = 0.0
            if event_entities and s_entities:
                intersection = len(event_entities & s_entities)
                union = len(event_entities | s_entities)
                jaccard = intersection / union if union > 0 else 0
                if jaccard >= self.ENTITY_JACCARD_THRESHOLD:
                    entity_boost = self.ENTITY_BOOST

            # Hybrid score
            score = cosine_sim - time_penalty + entity_boost

            if score > best_score and score >= self.MATCH_THRESHOLD:
                best_score = score
                best_match = {
                    'storyline_id': storyline['storyline_id'],
                    'title': storyline['title'],
                    'score': score,
                    'cosine_sim': cosine_sim,
                    'time_penalty': time_penalty,
                    'entity_boost': entity_boost,
                }

        if best_match:
            logger.debug(
                f"Event '{event['representative_title'][:50]}' → "
                f"storyline '{best_match['title'][:40]}' (score={best_match['score']:.3f})"
            )

        return best_match

    def _assign_event_to_storyline(self, event: Dict, storyline_id: int) -> None:
        """
        Assign an event (group of articles) to an existing storyline.

        Updates: article_storylines, current_embedding (drift), key_entities,
        article_count, momentum_score, narrative_status.
        """
        with self.db.get_connection() as conn:
            with conn.cursor() as cur:
                # 1. Insert article-storyline links
                for article_id in event['article_ids']:
                    cur.execute("""
                        INSERT INTO article_storylines (article_id, storyline_id, relevance_score, is_origin)
                        VALUES (%s, %s, %s, FALSE)
                        ON CONFLICT (article_id, storyline_id) DO NOTHING
                    """, (article_id, storyline_id, 1.0))

                # 2. Get current storyline state
                cur.execute("""
                    SELECT current_embedding, key_entities, article_count, narrative_status
                    FROM storylines WHERE id = %s
                """, (storyline_id,))
                row = cur.fetchone()
                if not row:
                    return

                current_emb = np.array(row[0]) if row[0] is not None else event['embedding']
                current_entities = set(row[1] or [])
                current_count = row[2] or 0
                current_status = row[3]

                # 3. Vector drift
                new_emb = event['embedding']
                drifted = self.DRIFT_WEIGHT_OLD * current_emb + self.DRIFT_WEIGHT_NEW * new_emb
                norm = np.linalg.norm(drifted)
                if norm > 0:
                    drifted = drifted / norm

                # 4. Merge entities (cap at 20, by frequency)
                merged_entities = list(current_entities | set(event['entities']))[:20]

                # 5. Update counts and status
                new_count = current_count + len(event['article_ids'])
                momentum_bump = min(1.0, 0.1 * len(event['article_ids']))

                # Promote emerging → active when article_count >= 3
                new_status = current_status
                if current_status == 'emerging' and new_count >= 3:
                    new_status = 'active'

                cur.execute("""
                    UPDATE storylines SET
                        current_embedding = %s::vector,
                        key_entities = %s,
                        article_count = %s,
                        momentum_score = LEAST(1.0, momentum_score + %s),
                        narrative_status = %s,
                        last_update = NOW()
                    WHERE id = %s
                """, (
                    drifted.tolist(),
                    Json(merged_entities),
                    new_count,
                    momentum_bump,
                    new_status,
                    storyline_id,
                ))

            conn.commit()

    # =========================================================================
    # STAGE 3: HDBSCAN DISCOVERY
    # =========================================================================

    def _cluster_residuals(self, orphaned_events: List[Dict]) -> List[int]:
        """
        Apply HDBSCAN to orphaned events to discover new storylines.

        Returns list of created storyline IDs.
        """
        if len(orphaned_events) < self.HDBSCAN_MIN_CLUSTER_SIZE:
            # Too few events — create individual storylines for each
            created_ids = []
            for event in orphaned_events:
                sid = self._create_storyline_from_events([event])
                if sid:
                    created_ids.append(sid)
            return created_ids

        if not HDBSCAN_AVAILABLE:
            logger.warning("HDBSCAN not available (sklearn < 1.3). Creating individual storylines.")
            created_ids = []
            for event in orphaned_events:
                sid = self._create_storyline_from_events([event])
                if sid:
                    created_ids.append(sid)
            return created_ids

        # Build normalized embedding matrix (euclidean on unit vectors ≈ cosine)
        embeddings = np.array([e['embedding'] for e in orphaned_events])
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        embeddings_norm = embeddings / np.maximum(norms, 1e-10)

        # Run HDBSCAN
        hdb = HDBSCAN(
            min_cluster_size=self.HDBSCAN_MIN_CLUSTER_SIZE,
            min_samples=self.HDBSCAN_MIN_SAMPLES,
            metric='euclidean',
        )
        labels = hdb.fit_predict(embeddings_norm)

        # Group events by cluster label
        cluster_map = defaultdict(list)
        noise_events = []

        for idx, label in enumerate(labels):
            if label == -1:
                noise_events.append(orphaned_events[idx])
            else:
                cluster_map[label].append(orphaned_events[idx])

        n_clusters = len(cluster_map)
        n_noise = len(noise_events)
        logger.info(f"HDBSCAN: {n_clusters} clusters, {n_noise} noise events")

        # Create storylines from clusters
        created_ids = []
        for cluster_label, cluster_events in sorted(cluster_map.items()):
            sid = self._create_storyline_from_events(cluster_events)
            if sid:
                created_ids.append(sid)
                logger.info(
                    f"  Cluster {cluster_label}: {len(cluster_events)} events → storyline #{sid}"
                )

        # Noise events become individual storylines
        for event in noise_events:
            sid = self._create_storyline_from_events([event])
            if sid:
                created_ids.append(sid)

        return created_ids

    def _create_storyline_from_events(self, events: List[Dict]) -> Optional[int]:
        """Create a new storyline from one or more events."""
        if not events:
            return None

        # Centroid embedding
        all_embeddings = np.array([e['embedding'] for e in events])
        centroid = all_embeddings.mean(axis=0)
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroid = centroid / norm

        # Aggregate entities by frequency
        entity_counter = Counter()
        for event in events:
            entity_counter.update(event['entities'])
        top_entities = [e for e, _ in entity_counter.most_common(15)]

        # Category: most common
        categories = [e['category'] for e in events if e.get('category')]
        category = Counter(categories).most_common(1)[0][0] if categories else None

        # Title: representative from largest event
        largest_event = max(events, key=lambda e: len(e['article_ids']))
        title = largest_event['representative_title'][:100]

        # Total articles across all events
        all_article_ids = []
        for event in events:
            all_article_ids.extend(event['article_ids'])
        total_articles = len(all_article_ids)

        try:
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    # Create storyline
                    cur.execute("""
                        INSERT INTO storylines (
                            title, summary, original_embedding, current_embedding,
                            key_entities, category, narrative_status, status,
                            start_date, last_update, article_count, momentum_score
                        ) VALUES (
                            %s, NULL, %s::vector, %s::vector,
                            %s, %s, 'emerging', 'ACTIVE',
                            CURRENT_DATE, NOW(), %s, 0.5
                        )
                        RETURNING id
                    """, (
                        title,
                        centroid.tolist(), centroid.tolist(),
                        Json(top_entities), category,
                        total_articles,
                    ))
                    storyline_id = cur.fetchone()[0]

                    # Link articles
                    for i, article_id in enumerate(all_article_ids):
                        cur.execute("""
                            INSERT INTO article_storylines (
                                article_id, storyline_id, relevance_score, is_origin
                            ) VALUES (%s, %s, 1.0, %s)
                            ON CONFLICT (article_id, storyline_id) DO NOTHING
                        """, (article_id, storyline_id, i == 0))

                conn.commit()

            logger.debug(f"Created storyline #{storyline_id}: '{title[:60]}' ({total_articles} articles)")
            return storyline_id

        except Exception as e:
            logger.error(f"Error creating storyline: {e}")
            return None

    # =========================================================================
    # STAGE 4: LLM SUMMARY EVOLUTION
    # =========================================================================

    def _evolve_narrative_summary(self, storyline_id: int) -> None:
        """
        Update a storyline's title, summary, and summary_vector using Gemini LLM.

        - New storyline (no summary): generates title + summary from scratch
        - Existing summary: integrates new facts while preserving historical context
        """
        if not self.gemini_available:
            return

        # Fetch current storyline state
        with self.db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT title, summary, key_entities
                    FROM storylines WHERE id = %s
                """, (storyline_id,))
                row = cur.fetchone()
                if not row:
                    return

                current_title, current_summary, key_entities = row

                # Fetch recent articles (last 5)
                cur.execute("""
                    SELECT a.title, LEFT(a.full_text, 200) AS snippet
                    FROM articles a
                    JOIN article_storylines als ON a.id = als.article_id
                    WHERE als.storyline_id = %s
                    ORDER BY a.published_date DESC
                    LIMIT 5
                """, (storyline_id,))
                recent_articles = cur.fetchall()

        if not recent_articles:
            return

        # Build article context
        articles_text = "\n".join(
            f"- {a[0]}" + (f": {a[1]}..." if a[1] else "")
            for a in recent_articles
        )
        entities_text = ", ".join(key_entities[:10]) if key_entities else "N/A"

        # Build prompt
        if current_summary:
            prompt = f"""Sei un analista geopolitico esperto. Aggiorna il riassunto di questa storyline integrando i nuovi fatti.

STORYLINE ATTUALE:
Titolo: {current_title}
Riassunto: {current_summary}
Entità chiave: {entities_text}

NUOVI ARTICOLI:
{articles_text}

Rispondi in questo formato esatto:
TITOLO: [titolo aggiornato, max 8 parole, in italiano]
RIASSUNTO: [riassunto aggiornato, 3-5 frasi, integra i nuovi fatti mantenendo il contesto storico]"""
        else:
            prompt = f"""Sei un analista geopolitico esperto. Genera un titolo e un riassunto per questa nuova storyline.

ARTICOLI:
{articles_text}

ENTITÀ CHIAVE: {entities_text}

Rispondi in questo formato esatto:
TITOLO: [titolo descrittivo, max 8 parole, in italiano, specifico e informativo]
RIASSUNTO: [riassunto di 3-5 frasi che descrive la narrativa principale]"""

        try:
            response = self.model.generate_content(
                prompt,
                request_options={"timeout": 60}
            )
            text = response.text.strip()

            # Parse response
            new_title = current_title
            new_summary = current_summary or ""

            for line in text.split('\n'):
                line = line.strip()
                if line.upper().startswith('TITOLO:'):
                    new_title = line[7:].strip().strip('"\'')[:100]
                elif line.upper().startswith('RIASSUNTO:'):
                    new_summary = line[10:].strip()

            # If summary continues on next lines after RIASSUNTO:
            if 'RIASSUNTO:' in text:
                parts = text.split('RIASSUNTO:', 1)
                if len(parts) == 2:
                    new_summary = parts[1].strip()

            # Encode summary → summary_vector
            summary_vector = self.embedding_model.encode(new_summary).tolist()

            # Update DB
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE storylines SET
                            title = %s,
                            summary = %s,
                            summary_vector = %s::vector
                        WHERE id = %s
                    """, (new_title, new_summary, summary_vector, storyline_id))
                conn.commit()

            logger.debug(f"Evolved storyline #{storyline_id}: '{new_title[:60]}'")
            time.sleep(self.LLM_RATE_LIMIT_SECONDS)

        except Exception as e:
            logger.error(f"LLM summary evolution failed for storyline #{storyline_id}: {e}")

    # =========================================================================
    # STAGE 4b: POST-CLUSTERING RELEVANCE VALIDATION
    # =========================================================================

    def _validate_storyline_relevance(self, storyline_ids: Set[int]) -> Dict[str, int]:
        """
        Validate that newly created/updated storylines are actually on-scope.

        Checks title + summary against:
        1. Positive scope keywords (must contain at least one)
        2. Negative off-topic patterns (must not match, unless scope keywords present)

        Off-topic storylines are immediately archived with narrative_status='archived'.

        Returns:
            {'validated': N, 'archived_off_topic': N}
        """
        if not storyline_ids:
            return {'validated': 0, 'archived_off_topic': 0}

        stats = {'validated': 0, 'archived_off_topic': 0}

        with self.db.get_connection() as conn:
            with conn.cursor() as cur:
                # Fetch titles and summaries for the storylines
                cur.execute("""
                    SELECT id, title, summary
                    FROM storylines
                    WHERE id = ANY(%s)
                    AND narrative_status <> 'archived'
                """, (list(storyline_ids),))
                rows = cur.fetchall()

                archive_ids = []
                for sid, title, summary in rows:
                    text = f"{title or ''} {summary or ''}"

                    # Check if text contains any scope keyword
                    has_scope = bool(_SCOPE_KEYWORDS.search(text))

                    # Check if text matches off-topic patterns
                    is_off_topic = any(p.search(text) for p in _OFF_TOPIC_PATTERNS)

                    if not has_scope and is_off_topic:
                        # Clearly off-topic: no scope keywords AND matches off-topic pattern
                        archive_ids.append(sid)
                        logger.info(
                            f"Post-clustering validation: archiving off-topic storyline "
                            f"#{sid}: '{(title or '')[:60]}'"
                        )
                    elif not has_scope and not summary:
                        # No summary yet and no scope keywords in title — skip for now,
                        # will be validated again after LLM generates summary
                        stats['validated'] += 1
                    else:
                        stats['validated'] += 1

                # Archive off-topic storylines
                if archive_ids:
                    cur.execute("""
                        UPDATE storylines
                        SET narrative_status = 'archived',
                            status = 'ARCHIVED'
                        WHERE id = ANY(%s)
                    """, (archive_ids,))
                    stats['archived_off_topic'] = len(archive_ids)

            conn.commit()

        if stats['archived_off_topic'] > 0:
            logger.info(
                f"Post-clustering validation: {stats['validated']} on-scope, "
                f"{stats['archived_off_topic']} archived as off-topic"
            )

        return stats

    # =========================================================================
    # STAGE 5: GRAPH BUILDER
    # =========================================================================

    def _update_graph_connections(self, storyline_id: int) -> int:
        """
        Create/update edges between storyline_id and other active storylines
        based on entity Jaccard overlap.

        Returns number of edges created/updated.
        """
        edges_modified = 0

        with self.db.get_connection() as conn:
            with conn.cursor() as cur:
                # Get this storyline's entities
                cur.execute("""
                    SELECT key_entities FROM storylines WHERE id = %s
                """, (storyline_id,))
                row = cur.fetchone()
                if not row or not row[0]:
                    return 0

                source_entities = set(e.lower() for e in row[0])

                # Get all other active storylines' entities
                cur.execute("""
                    SELECT id, key_entities
                    FROM storylines
                    WHERE narrative_status IN ('emerging', 'active')
                    AND id != %s
                    AND key_entities IS NOT NULL
                """, (storyline_id,))
                others = cur.fetchall()

                for other_id, other_entities_raw in others:
                    if not other_entities_raw:
                        continue

                    target_entities = set(e.lower() for e in other_entities_raw)

                    # Jaccard index
                    intersection = len(source_entities & target_entities)
                    union = len(source_entities | target_entities)
                    jaccard = intersection / union if union > 0 else 0

                    if jaccard >= self.ENTITY_JACCARD_THRESHOLD:
                        # UPSERT edge
                        cur.execute("""
                            INSERT INTO storyline_edges (source_story_id, target_story_id, weight, relation_type)
                            VALUES (%s, %s, %s, 'relates_to')
                            ON CONFLICT (source_story_id, target_story_id)
                            DO UPDATE SET weight = %s, updated_at = NOW()
                        """, (storyline_id, other_id, jaccard, jaccard))
                        edges_modified += 1
                    else:
                        # Remove edge if it exists and score dropped below threshold
                        cur.execute("""
                            DELETE FROM storyline_edges
                            WHERE source_story_id = %s AND target_story_id = %s
                        """, (storyline_id, other_id))

                # Update last_graph_update
                cur.execute("""
                    UPDATE storylines SET last_graph_update = NOW() WHERE id = %s
                """, (storyline_id,))

            conn.commit()

        return edges_modified

    # =========================================================================
    # STAGE 6: DECAY
    # =========================================================================

    def _apply_decay(self) -> Dict[str, int]:
        """
        Apply lifecycle transitions to inactive storylines.

        Rules:
        - No articles in 7 days: momentum *= 0.7
        - momentum < 0.3 + narrative_status='active': → 'stabilized'
        - 'stabilized' for 30 days without update: → 'archived'
        - 'emerging' for 14 days without reaching 3 articles: → 'archived'
        """
        stats = {'decayed': 0, 'stabilized': 0, 'archived': 0}

        with self.db.get_connection() as conn:
            with conn.cursor() as cur:
                # 1. Decay momentum for stale active storylines
                cur.execute("""
                    UPDATE storylines
                    SET momentum_score = momentum_score * %s
                    WHERE narrative_status IN ('emerging', 'active')
                    AND last_update < NOW() - INTERVAL '7 days'
                    RETURNING id
                """, (self.MOMENTUM_DECAY_FACTOR,))
                stats['decayed'] = cur.rowcount

                # 2. Active with low momentum → stabilized
                cur.execute("""
                    UPDATE storylines
                    SET narrative_status = 'stabilized'
                    WHERE narrative_status = 'active'
                    AND momentum_score < 0.3
                    RETURNING id
                """)
                stats['stabilized'] = cur.rowcount

                # 3. Stabilized for 30 days → archived
                cur.execute("""
                    UPDATE storylines
                    SET narrative_status = 'archived'
                    WHERE narrative_status = 'stabilized'
                    AND last_update < NOW() - INTERVAL '30 days'
                    RETURNING id
                """)
                stats['archived'] = cur.rowcount

                # 4. Emerging for 14 days without reaching 3 articles → archived
                cur.execute("""
                    UPDATE storylines
                    SET narrative_status = 'archived'
                    WHERE narrative_status = 'emerging'
                    AND article_count < 3
                    AND created_at < NOW() - INTERVAL '14 days'
                    RETURNING id
                """)
                stats['archived'] += cur.rowcount

            conn.commit()

        if any(stats.values()):
            logger.info(
                f"Decay: {stats['decayed']} decayed, "
                f"{stats['stabilized']} → stabilized, {stats['archived']} → archived"
            )

        return stats

    # =========================================================================
    # HELPERS
    # =========================================================================

    @staticmethod
    def _extract_entity_list(entities_json: Any) -> List[str]:
        """
        Extract flat entity list from article's entities JSON.

        Handles both formats:
        - New: {'clean': {'all': [...]}}
        - Old: {'by_type': {'GPE': [...], 'ORG': [...], 'PERSON': [...]}}
        """
        if not entities_json or not isinstance(entities_json, dict):
            return []

        # New format (clean entities)
        clean = entities_json.get('clean', {})
        if clean and clean.get('all'):
            return clean['all'][:15]

        # Old format (by_type)
        by_type = entities_json.get('by_type', {})
        result = []
        for etype in ['GPE', 'ORG', 'PERSON']:
            result.extend(by_type.get(etype, []))
        return result[:15]
