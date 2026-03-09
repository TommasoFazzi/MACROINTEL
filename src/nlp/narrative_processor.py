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
from psycopg2.extras import Json, execute_values

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
    ENTITY_JACCARD_THRESHOLD = 0.05  # Min TF-IDF weighted Jaccard for graph edges
    HDBSCAN_MIN_CLUSTER_SIZE = 2     # Min events to form a new storyline
    HDBSCAN_MIN_SAMPLES = 2
    DRIFT_WEIGHT_OLD = 0.85          # Weight for existing storyline embedding
    DRIFT_WEIGHT_NEW = 0.15          # Weight for new event embedding
    MOMENTUM_DECAY_FACTOR = 0.7      # Weekly decay multiplier
    LLM_RATE_LIMIT_SECONDS = 0.1     # gemini-2.0-flash: high quota, 0.1s sufficient

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
                self.model = genai.GenerativeModel('gemini-2.0-flash')
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
            'orphans_recovered': 0,
            'orphans_buffered': 0,
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

        # 3.5. Retry orphan pool against active storylines
        if not dry_run:
            orphan_recovery = self._retry_orphan_pool(active_storylines)
            stats['orphans_recovered'] = orphan_recovery['recovered']
            if orphan_recovery['recovered'] > 0:
                # Reload storylines so events/embeddings reflect absorbed orphans
                active_storylines = self._load_active_storylines()

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

        # 6. Cluster orphaned events with HDBSCAN → new storylines (noise → orphan buffer)
        cluster_result = self._cluster_residuals(orphaned_events)
        new_storyline_ids = cluster_result['created_ids']
        stats['new_storylines'] = len(new_storyline_ids)
        stats['orphans_buffered'] = cluster_result['buffered_count']
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
        # Load IDF weights once for the entire batch (entity_idf materialized view).
        # If the view does not exist yet (migration 015 not applied), idf_weights
        # stays None and _update_graph_connections falls back to plain Jaccard at
        # the original 0.30 threshold to avoid edge explosion with low TF-IDF threshold.
        idf_weights: Optional[Dict[str, float]] = None
        try:
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    loaded = self._load_entity_idf(cur)
            if loaded:
                idf_weights = loaded
                logger.info(f"Loaded IDF weights for {len(idf_weights)} entities")
            else:
                logger.warning("entity_idf view empty/missing — falling back to plain Jaccard (threshold 0.30)")
        except Exception as e:
            logger.warning(f"Could not load entity IDF weights (falling back to plain Jaccard): {e}")

        for sid in updated_storyline_ids:
            try:
                edges = self._update_graph_connections(sid, idf_weights)
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

                # 4. Merge entities (cap at 20, sanitized)
                new_event_entities = [
                    self._clean_entity(e) for e in event['entities']
                    if not self._is_garbage_entity(self._clean_entity(e))
                ]
                all_entities = list(current_entities | set(new_event_entities))
                # Deduplicate case-insensitive, keep first occurrence
                seen_lower = set()
                merged_entities = []
                for e in all_entities:
                    key = e.lower()
                    if key not in seen_lower:
                        seen_lower.add(key)
                        merged_entities.append(e)
                merged_entities = merged_entities[:20]

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

    def _cluster_residuals(self, orphaned_events: List[Dict]) -> Dict[str, Any]:
        """
        Apply HDBSCAN to orphaned events to discover new storylines.
        Noise events (label == -1) are sent to the orphan buffer pool
        instead of creating singleton storylines.

        Returns dict with 'created_ids' and 'buffered_count'.
        """
        result = {'created_ids': [], 'buffered_count': 0}

        if len(orphaned_events) < self.HDBSCAN_MIN_CLUSTER_SIZE:
            # Too few events to cluster — send to orphan buffer
            self._store_orphan_events(orphaned_events)
            result['buffered_count'] = len(orphaned_events)
            logger.info(f"HDBSCAN: too few events ({len(orphaned_events)}), buffered as orphans")
            return result

        if not HDBSCAN_AVAILABLE:
            logger.warning("HDBSCAN not available (sklearn < 1.3). Buffering as orphans.")
            self._store_orphan_events(orphaned_events)
            result['buffered_count'] = len(orphaned_events)
            return result

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

        # Noise events → orphan buffer pool (NOT singleton storylines)
        if noise_events:
            self._store_orphan_events(noise_events)
            logger.info(f"  {n_noise} noise events → orphan buffer pool")

        result['created_ids'] = created_ids
        result['buffered_count'] = n_noise
        return result

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
    # STAGE 3b: ORPHAN BUFFER POOL
    # =========================================================================

    def _store_orphan_events(self, events: List[Dict]) -> int:
        """
        Store unmatched events in the orphan buffer pool for future retry.

        Args:
            events: List of event dicts (article_ids, embedding, entities, etc.)

        Returns:
            Number of events stored.
        """
        if not events:
            return 0

        stored = 0
        try:
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    for event in events:
                        # Compute centroid for single-event (already a centroid for micro-cluster)
                        embedding = event['embedding']
                        norm = np.linalg.norm(embedding)
                        if norm > 0:
                            embedding = embedding / norm

                        entities = [
                            self._clean_entity(e) for e in event.get('entities', [])
                            if not self._is_garbage_entity(self._clean_entity(e))
                        ]

                        cur.execute("""
                            INSERT INTO orphan_events (
                                article_ids, representative_title,
                                centroid_embedding, key_entities, category
                            ) VALUES (%s, %s, %s::vector, %s, %s)
                        """, (
                            event['article_ids'],
                            event.get('representative_title', '')[:200],
                            embedding.tolist(),
                            Json(entities[:15]),
                            event.get('category'),
                        ))
                        stored += 1

                conn.commit()
        except Exception as e:
            logger.error(f"Failed to store orphan events: {e}")

        if stored:
            logger.info(f"Stored {stored} events in orphan buffer pool")
        return stored

    def _retry_orphan_pool(self, active_storylines: List[Dict]) -> Dict[str, int]:
        """
        Try matching buffered orphan events against active storylines.
        Orphans that match are assigned to the storyline and removed from the pool.
        Orphans older than 14 days are discarded.

        Args:
            active_storylines: Current active storylines (from _load_active_storylines)

        Returns:
            Dict with 'recovered', 'expired', 'remaining' counts.
        """
        result = {'recovered': 0, 'expired': 0, 'remaining': 0}

        if not active_storylines:
            return result

        try:
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    # 1. Expire old orphans (14 days)
                    cur.execute("""
                        DELETE FROM orphan_events
                        WHERE created_at < NOW() - INTERVAL '14 days'
                        RETURNING id
                    """)
                    result['expired'] = cur.rowcount
                    if result['expired']:
                        logger.info(f"Orphan pool: expired {result['expired']} stale orphans (>14 days)")

                    # 2. Load remaining orphans
                    cur.execute("""
                        SELECT id, article_ids, representative_title,
                               centroid_embedding, key_entities, category, retry_count
                        FROM orphan_events
                        ORDER BY created_at ASC
                    """)
                    orphan_rows = cur.fetchall()

                conn.commit()

            if not orphan_rows:
                return result

            logger.info(f"Orphan pool: retrying {len(orphan_rows)} buffered events")

            recovered_ids = []  # orphan_event IDs to delete
            recovered_storyline_ids = set()  # storylines that absorbed orphans

            for row in orphan_rows:
                orphan_id, article_ids, title, embedding, entities, category, retry_count = row
                embedding_array = np.array(embedding)

                # Build a pseudo-event dict for _find_best_match
                pseudo_event = {
                    'article_ids': article_ids,
                    'representative_title': title,
                    'embedding': embedding_array,
                    'entities': entities if entities else [],
                    'category': category,
                }

                match = self._find_best_match(pseudo_event, active_storylines)
                if match:
                    # Assign to storyline
                    self._assign_event_to_storyline(pseudo_event, match['storyline_id'])
                    recovered_ids.append(orphan_id)
                    recovered_storyline_ids.add(match['storyline_id'])
                    logger.debug(
                        f"Orphan #{orphan_id} '{title[:40]}' → storyline #{match['storyline_id']} "
                        f"(score={match['score']:.3f}, retries={retry_count})"
                    )

            # Delete recovered orphans and bump retry_count on remaining
            if recovered_ids or orphan_rows:
                with self.db.get_connection() as conn:
                    with conn.cursor() as cur:
                        if recovered_ids:
                            cur.execute(
                                "DELETE FROM orphan_events WHERE id = ANY(%s)",
                                (recovered_ids,)
                            )

                        # Bump retry_count for remaining orphans
                        remaining_ids = [
                            row[0] for row in orphan_rows
                            if row[0] not in set(recovered_ids)
                        ]
                        if remaining_ids:
                            cur.execute("""
                                UPDATE orphan_events
                                SET retry_count = retry_count + 1,
                                    last_retry = NOW()
                                WHERE id = ANY(%s)
                            """, (remaining_ids,))

                    conn.commit()

            result['recovered'] = len(recovered_ids)
            result['remaining'] = len(orphan_rows) - len(recovered_ids)

            if result['recovered']:
                logger.info(
                    f"Orphan pool: recovered {result['recovered']}, "
                    f"remaining {result['remaining']}"
                )

        except Exception as e:
            logger.error(f"Orphan pool retry failed (non-blocking): {e}")

        return result

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

        # Fetch current storyline state + recent articles
        # Two-phase: try with full_text snippet first, fall back to title-only
        # if any article has invalid UTF-8 bytes (legacy ingestion issue).
        current_title = current_summary = key_entities = None
        recent_articles = []

        try:
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

                    cur.execute("""
                        SELECT a.title, LEFT(a.full_text, 200) AS snippet
                        FROM articles a
                        JOIN article_storylines als ON a.id = als.article_id
                        WHERE als.storyline_id = %s
                        ORDER BY a.published_date DESC
                        LIMIT 5
                    """, (storyline_id,))
                    recent_articles = cur.fetchall()

        except Exception as fetch_err:
            if 'UTF8' not in str(fetch_err) and 'encoding' not in str(fetch_err).lower():
                raise
            # Some article for this storyline has corrupt bytes in full_text.
            # Retry fetching titles only (no snippet) so evolution can proceed.
            logger.warning(
                f"UTF-8 encoding error fetching snippets for storyline #{storyline_id}, "
                f"retrying with titles only: {fetch_err}"
            )
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

                    cur.execute("""
                        SELECT a.title, NULL AS snippet
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
RIASSUNTO: [riassunto aggiornato, 3-5 frasi, integra i nuovi fatti mantenendo il contesto storico]
ENTITÀ: [lista di 5-10 entità chiave separate da virgola — solo nomi propri: paesi, leader, organizzazioni, città, trattati. NO titoli di articoli, NO frammenti HTML, NO parole generiche, NO numeri isolati, NO acronimi di 2 lettere]"""
        else:
            prompt = f"""Sei un analista geopolitico esperto. Genera un titolo e un riassunto per questa nuova storyline.

ARTICOLI:
{articles_text}

ENTITÀ CHIAVE ATTUALI (da spaCy, possono contenere errori): {entities_text}

Rispondi in questo formato esatto:
TITOLO: [titolo descrittivo, max 8 parole, in italiano, specifico e informativo]
RIASSUNTO: [riassunto di 3-5 frasi che descrive la narrativa principale]
ENTITÀ: [lista di 5-10 entità chiave separate da virgola — solo nomi propri: paesi, leader, organizzazioni, città, trattati. NO titoli di articoli, NO frammenti HTML, NO parole generiche, NO numeri isolati, NO acronimi di 2 lettere]"""

        try:
            response = self.model.generate_content(
                prompt,
                generation_config={
                    "max_output_tokens": 400,  # titolo(~15t) + riassunto(~200t IT) + margine
                    "temperature": 0.3,        # format-following: bassa varianza, output coerente
                },
                request_options={"timeout": 30}  # 2.0-flash: <4s normale, 30s = 7× safety margin
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
                riassunto_parts = text.split('RIASSUNTO:', 1)
                if len(riassunto_parts) == 2:
                    summary_block = riassunto_parts[1]
                    # Stop at ENTITÀ: if present
                    if 'ENTIT' in summary_block.upper():
                        summary_block = re.split(r'ENTIT[ÀA]:', summary_block, flags=re.IGNORECASE)[0]
                    new_summary = summary_block.strip()

            # Parse ENTITÀ: line from Gemini response
            new_entities = None
            for line in text.split('\n'):
                line_stripped = line.strip()
                if re.match(r'^ENTIT[ÀA]:', line_stripped, re.IGNORECASE):
                    entities_raw = re.sub(r'^ENTIT[ÀA]:', '', line_stripped, flags=re.IGNORECASE).strip()
                    # Split by comma, clean each entity
                    parsed = [e.strip().strip('"\'-[]') for e in entities_raw.split(',')]
                    parsed = [e for e in parsed if e and len(e) >= 2]
                    # Apply sanitization
                    parsed = [e for e in parsed if not self._is_garbage_entity(e)]
                    if parsed:
                        new_entities = parsed[:15]
                    break

            # Encode summary → summary_vector
            summary_vector = self.embedding_model.encode(new_summary).tolist()

            # Update DB
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    if new_entities:
                        cur.execute("""
                            UPDATE storylines SET
                                title = %s,
                                summary = %s,
                                summary_vector = %s::vector,
                                key_entities = %s
                            WHERE id = %s
                        """, (new_title, new_summary, summary_vector, Json(new_entities), storyline_id))
                    else:
                        cur.execute("""
                            UPDATE storylines SET
                                title = %s,
                                summary = %s,
                                summary_vector = %s::vector
                            WHERE id = %s
                        """, (new_title, new_summary, summary_vector, storyline_id))
                conn.commit()

            entities_log = f", entities={len(new_entities)}" if new_entities else ""
            logger.debug(f"Evolved storyline #{storyline_id}: '{new_title[:60]}'{entities_log}")
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

    def _load_entity_idf(self, cur) -> Dict[str, float]:
        """
        Load TF-IDF weights from entity_idf materialized view.
        Returns {entity_lowercase: idf_score}.
        Falls back gracefully if the view does not exist yet.
        """
        try:
            cur.execute("SELECT entity, idf FROM entity_idf")
            return {row[0].lower(): float(row[1]) for row in cur.fetchall()}
        except Exception:
            return {}

    def _update_graph_connections(self, storyline_id: int, idf_weights: Optional[Dict[str, float]] = None) -> int:
        """
        Create/update edges between storyline_id and other active storylines
        using TF-IDF weighted Jaccard similarity.

        Rare entities (high IDF) contribute more to edge weight than common
        entities (low IDF, e.g. "USA", "Trump"). This eliminates hairball
        connections formed by high-frequency generic entities.

        When idf_weights is None (entity_idf view not yet available), falls back
        to plain Jaccard with the safe legacy threshold of 0.30.

        Returns number of edges created/updated.
        """
        use_tfidf = idf_weights is not None
        threshold = self.ENTITY_JACCARD_THRESHOLD if use_tfidf else 0.30
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

                # Pre-filter at DB level: only fetch storylines that share at least
                # one entity with this storyline. EXISTS short-circuits at first match,
                # reducing candidates from ~3000+ to ~10-50.
                source_entities_list = list(source_entities)
                cur.execute("""
                    SELECT id, key_entities
                    FROM storylines
                    WHERE narrative_status IN ('emerging', 'active', 'stabilized')
                    AND id != %s
                    AND key_entities IS NOT NULL
                    AND EXISTS (
                        SELECT 1 FROM jsonb_array_elements_text(key_entities) AS e
                        WHERE LOWER(e) = ANY(%s)
                    )
                """, (storyline_id, source_entities_list))
                candidates = cur.fetchall()

                # Compute similarity for each candidate.
                # TF-IDF weighted Jaccard when IDF weights available (post-migration 015),
                # plain Jaccard with legacy threshold 0.30 as safe fallback.
                new_edges = []
                for other_id, other_entities_raw in candidates:
                    if not other_entities_raw:
                        continue
                    target_entities = set(e.lower() for e in other_entities_raw)
                    shared = source_entities & target_entities
                    union = source_entities | target_entities
                    if use_tfidf:
                        intersection_w = sum(idf_weights.get(e, 1.0) for e in shared)  # type: ignore[union-attr]
                        union_w = sum(idf_weights.get(e, 1.0) for e in union)          # type: ignore[union-attr]
                        score = intersection_w / union_w if union_w > 0 else 0
                    else:
                        score = len(shared) / len(union) if union else 0
                    if score >= threshold:
                        new_edges.append((storyline_id, other_id, score))

                # Delete all outgoing edges for this storyline
                cur.execute("""
                    DELETE FROM storyline_edges WHERE source_story_id = %s
                """, (storyline_id,))

                if new_edges:
                    # Check which reverse edges already exist (B→A where we want A→B).
                    # Keep the higher-weight direction to avoid bidirectional duplicates.
                    target_ids = [t for _, t, _ in new_edges]
                    cur.execute("""
                        SELECT source_story_id, weight
                        FROM storyline_edges
                        WHERE source_story_id = ANY(%s) AND target_story_id = %s
                    """, (target_ids, storyline_id))
                    reverse_edges = {row[0]: row[1] for row in cur.fetchall()}

                    filtered_edges = []
                    for s, t, w in new_edges:
                        reverse_weight = reverse_edges.get(t)
                        if reverse_weight is not None:
                            # Reverse edge B→A exists. Keep whichever has higher weight.
                            if w > reverse_weight:
                                # Our edge is stronger: remove reverse, insert ours
                                cur.execute(
                                    "DELETE FROM storyline_edges "
                                    "WHERE source_story_id = %s AND target_story_id = %s",
                                    (t, storyline_id)
                                )
                                filtered_edges.append((s, t, w))
                            # else: reverse is stronger or equal, skip this direction
                        else:
                            filtered_edges.append((s, t, w))

                    if filtered_edges:
                        execute_values(cur, """
                            INSERT INTO storyline_edges
                                (source_story_id, target_story_id, weight, relation_type)
                            VALUES %s
                        """, [(s, t, w, 'relates_to') for s, t, w in filtered_edges])
                    edges_modified = len(filtered_edges)

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

                # 4. Emerging for 5 days without reaching 3 articles → archived
                cur.execute("""
                    UPDATE storylines
                    SET narrative_status = 'archived'
                    WHERE narrative_status = 'emerging'
                    AND article_count < 3
                    AND created_at < NOW() - INTERVAL '5 days'
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

    # Regex patterns for garbage entity detection (compiled once)
    _GARBAGE_PATTERNS = re.compile(
        r'^\d+[a-zA-Z]'           # numeric prefix: "4Trump", "3Hamas"
        r'|^\d+$'                 # pure number: "2024", "100"
        r'|\b(?:http|www\.|\.[a-z]{2,4})\b'  # URLs
        r'|(?:Features|Podcasts|Pictures|Investigations|Interactives|Newsletter|Subscribe)'
        r'|(?:Science & Technology|Human Rights|Climate Crisis)'
        r'|^[A-Z]{1,2}$'         # 1-2 letter acronyms: "EU", "PM", "DC"
        r'|[\|\[\]{}()]'        # brackets, pipes (HTML artifacts)
        r'|^\W+$'                # only punctuation/symbols
        r'|\s{3,}',              # excessive whitespace (navigation fragments)
        re.IGNORECASE
    )
    # Known valid short acronyms that bypass the 1-2 char filter
    _VALID_SHORT = {'EU', 'UN', 'US', 'UK', 'G7', 'G20', 'AI', 'ONU', 'UE'}
    # Common false positives from spaCy NER
    _FALSE_POSITIVES = {
        'not', 'feb', 'jan', 'mar', 'apr', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec',
        'est', 'gmt', 'cet', 'bst', 'pst', 'edt', 'cdt', 'utc',
        'et', 'pm', 'am', 'dc', 'vs', 'op', 'no', 'ok', 'ad',
        'the', 'this', 'that', 'its', 'his', 'her',
    }

    @staticmethod
    def _is_garbage_entity(entity: str) -> bool:
        """
        Check if an entity is garbage that should be filtered out.

        Catches: numeric prefixes (4Trump), HTML fragments, article titles,
        too-short/too-long strings, trailing punctuation, navigation text.
        """
        if not entity or not isinstance(entity, str):
            return True
        e = entity.strip()
        if len(e) < 2 or len(e) > 60:
            return True
        # Reject entities with too many words (likely article titles)
        if len(e.split()) > 6:
            return True
        # Known false positives
        if e.lower() in NarrativeProcessor._FALSE_POSITIVES:
            return True
        # Valid short acronyms bypass pattern check
        if e.upper() in NarrativeProcessor._VALID_SHORT:
            return False
        if NarrativeProcessor._GARBAGE_PATTERNS.search(e):
            return True
        # Trailing/leading punctuation ("US-", "West Bank -")
        stripped = e.strip(' -–—.,;:!?/')
        if len(stripped) < 2:
            return True
        return False

    @staticmethod
    def _clean_entity(entity: str) -> str:
        """Normalize an entity string: strip numeric prefixes, punctuation edges, leading articles."""
        e = entity.strip()
        # Strip leading digit prefix ("4Trump" → "Trump", "3Hamas" → "Hamas")
        e = re.sub(r'^\d+', '', e).strip()
        # Strip trailing punctuation
        e = e.strip(' -–—.,;:!?/')
        # Strip leading "The " / "Il " / "La " etc.
        e = re.sub(r'^(?:The|Il|La|Lo|Le|Gli|L[\u2019\'])\s+', '', e, flags=re.IGNORECASE).strip()
        # Collapse whitespace
        e = re.sub(r'\s+', ' ', e).strip()
        return e

    @staticmethod
    def _extract_entity_list(entities_json: Any) -> List[str]:
        """
        Extract flat entity list from article's entities JSON.

        Handles both formats:
        - New: {'clean': {'all': [...]}}
        - Old: {'by_type': {'GPE': [...], 'ORG': [...], 'PERSON': [...]}}

        Applies rule-based sanitization to filter garbage entities.
        """
        if not entities_json or not isinstance(entities_json, dict):
            return []

        raw = []
        # New format (clean entities)
        clean = entities_json.get('clean', {})
        if clean and clean.get('all'):
            raw = clean['all'][:20]
        else:
            # Old format (by_type)
            by_type = entities_json.get('by_type', {})
            for etype in ['GPE', 'ORG', 'PERSON']:
                raw.extend(by_type.get(etype, []))
            raw = raw[:20]

        # Sanitize: clean + filter garbage + deduplicate
        seen = set()
        result = []
        for entity in raw:
            cleaned = NarrativeProcessor._clean_entity(entity)
            if NarrativeProcessor._is_garbage_entity(cleaned):
                continue
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(cleaned)

        return result[:15]
