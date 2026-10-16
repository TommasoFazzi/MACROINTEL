"""
Pure decision functions of the level-1 narrative engine (articles → storylines).

Extracted from `narrative_processor.py` by `storyline-formation-causal-review`
(design.md D2) so that prod and the offline chronological replay run the same
decision code. Nothing here touches the database or reads the clock: `now` is
always passed in. `NarrativeProcessor` keeps the DB orchestration and calls these
functions; the replay calls them on in-memory state.

Behaviour is identical to the pre-extraction code, including its quirks (greedy,
order-dependent micro-clusters; `merge_entities` truncating a set-ordered list),
which `tests/test_nlp/test_storyline_decisions_refactor.py` pins against verbatim
copies of the old methods. Changing a decision is a lever (design.md D6), not an
edit here.

Decay and orphan expiry are not here: they stay SQL in prod (design.md D2).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Set

import numpy as np

try:
    from sklearn.cluster import HDBSCAN
    HDBSCAN_AVAILABLE = True
except ImportError:
    HDBSCAN_AVAILABLE = False


# ---------------------------------------------------------------------------
# Stage 1: micro-clustering (near-duplicate articles → events)
# ---------------------------------------------------------------------------

def micro_cluster_indices(embeddings: np.ndarray, threshold: float) -> List[List[int]]:
    """Greedy grouping of near-duplicates: each unassigned row i opens a cluster and
    takes every later unassigned row j with cosine(i, j) >= threshold.

    Not transitive and order-dependent (hypothesis H5): the result depends on the
    row order the caller passes.
    """
    n = len(embeddings)
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    embeddings_norm = embeddings / np.maximum(norms, 1e-10)
    sim_matrix = embeddings_norm @ embeddings_norm.T

    assigned = [False] * n
    clusters = []
    for i in range(n):
        if assigned[i]:
            continue
        cluster_indices = [i]
        assigned[i] = True
        for j in range(i + 1, n):
            if assigned[j]:
                continue
            if sim_matrix[i, j] >= threshold:
                cluster_indices.append(j)
                assigned[j] = True
        clusters.append(cluster_indices)
    return clusters


def article_to_event(article: Dict, extract_entities: Callable[[Any], List[str]]) -> Dict:
    """One article as an event. The embedding is kept as is (not normalised)."""
    entities = extract_entities(article.get('entities', {}))
    return {
        'embedding': np.array(article['embedding']),
        'entities': entities,
        'article_ids': [article['id']],
        'representative_title': article['title'],
        'category': article.get('category'),
    }


def articles_to_event(articles: Sequence[Dict], extract_entities: Callable[[Any], List[str]]) -> Dict:
    """Several articles as one event: normalised centroid, union of entities, title of
    the article with most entities, most common category."""
    embeddings = np.array([a['embedding'] for a in articles])
    centroid = embeddings.mean(axis=0)
    norm = np.linalg.norm(centroid)
    if norm > 0:
        centroid = centroid / norm

    entity_counts = Counter()
    for a in articles:
        entity_counts.update(extract_entities(a.get('entities', {})))

    best_article = max(articles, key=lambda a: len(extract_entities(a.get('entities', {}))))

    categories = [a.get('category') for a in articles if a.get('category')]
    category = Counter(categories).most_common(1)[0][0] if categories else None

    return {
        'embedding': centroid,
        'entities': list(entity_counts.keys()),
        'article_ids': [a['id'] for a in articles],
        'representative_title': best_article['title'],
        'category': category,
    }


def create_micro_clusters(
    articles: Sequence[Dict], threshold: float, extract_entities: Callable[[Any], List[str]],
) -> List[Dict]:
    """Articles → events. A single article goes through `article_to_event`, every
    group (including singletons of a larger batch) through `articles_to_event`."""
    if not articles:
        return []
    if len(articles) == 1:
        return [article_to_event(articles[0], extract_entities)]
    embeddings = np.array([a['embedding'] for a in articles])
    return [
        articles_to_event([articles[i] for i in indices], extract_entities)
        for indices in micro_cluster_indices(embeddings, threshold)
    ]


# ---------------------------------------------------------------------------
# Stage 2: matching events to storylines
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MatchParams:
    threshold: float
    time_decay_factor: float
    entity_boost: float
    entity_jaccard_threshold: float


@dataclass(frozen=True)
class CandidateScore:
    """One storyline scored against one event: score = cosine − time_penalty + entity_boost."""
    storyline_id: int
    title: str
    score: float
    cosine_sim: float
    time_penalty: float
    entity_boost: float
    days_inactive: float
    jaccard: Optional[float]


def score_candidates(
    event: Dict, storylines: Sequence[Dict], now: datetime, params: MatchParams,
) -> List[CandidateScore]:
    """Score every storyline that has an embedding, in the given order.

    `storylines` are dicts as built by `NarrativeProcessor._load_active_storylines`
    (`storyline_id`, `title`, `current_embedding`, lower-cased `key_entities`,
    `last_update`). `now` must have the same tz-awareness as `last_update`.
    """
    event_embedding = event['embedding']
    event_entities = set(e.lower() for e in event['entities'])

    scores = []
    for storyline in storylines:
        if storyline['current_embedding'] is None:
            continue

        s_emb = storyline['current_embedding']
        dot = np.dot(event_embedding, s_emb)
        norm_product = np.linalg.norm(event_embedding) * np.linalg.norm(s_emb)
        if norm_product == 0:
            continue
        cosine_sim = dot / norm_product

        days_inactive = 0
        if storyline['last_update']:
            delta = now - storyline['last_update']
            days_inactive = max(0, delta.total_seconds() / 86400)
        time_penalty = params.time_decay_factor * days_inactive

        s_entities = storyline['key_entities']
        entity_boost = 0.0
        jaccard = None
        if event_entities and s_entities:
            intersection = len(event_entities & s_entities)
            union = len(event_entities | s_entities)
            jaccard = intersection / union if union > 0 else 0
            if jaccard >= params.entity_jaccard_threshold:
                entity_boost = params.entity_boost

        scores.append(CandidateScore(
            storyline_id=storyline['storyline_id'],
            title=storyline['title'],
            score=cosine_sim - time_penalty + entity_boost,
            cosine_sim=cosine_sim,
            time_penalty=time_penalty,
            entity_boost=entity_boost,
            days_inactive=days_inactive,
            jaccard=jaccard,
        ))
    return scores


def select_match(scores: Sequence[CandidateScore], threshold: float) -> Optional[CandidateScore]:
    """Highest score at or above the threshold; the first one wins a tie (strict >)."""
    best = None
    best_score = -1.0
    for c in scores:
        if c.score > best_score and c.score >= threshold:
            best_score = c.score
            best = c
    return best


# ---------------------------------------------------------------------------
# Stage 3: residual clustering of unmatched events
# ---------------------------------------------------------------------------

def residual_labels(event_embeddings: np.ndarray, min_cluster_size: int, min_samples: int) -> np.ndarray:
    """HDBSCAN labels for unmatched events (euclidean on unit vectors ≈ cosine).
    Label -1 is noise, which goes to the orphan pool. The caller handles the
    too-few-events case before calling."""
    norms = np.linalg.norm(event_embeddings, axis=1, keepdims=True)
    embeddings_norm = event_embeddings / np.maximum(norms, 1e-10)
    hdb = HDBSCAN(min_cluster_size=min_cluster_size, min_samples=min_samples, metric='euclidean')
    return hdb.fit_predict(embeddings_norm)


def storyline_seed(events: Sequence[Dict]) -> Dict:
    """Initial state of a storyline created from a residual cluster: normalised
    centroid of the event embeddings, 15 most frequent entities, most common
    category, title of the largest event, and every article id (with repeats if two
    events share an article; `article_count` is set from this list's length)."""
    all_embeddings = np.array([e['embedding'] for e in events])
    centroid = all_embeddings.mean(axis=0)
    norm = np.linalg.norm(centroid)
    if norm > 0:
        centroid = centroid / norm

    entity_counter = Counter()
    for event in events:
        entity_counter.update(event['entities'])
    top_entities = [e for e, _ in entity_counter.most_common(15)]

    categories = [e['category'] for e in events if e.get('category')]
    category = Counter(categories).most_common(1)[0][0] if categories else None

    largest_event = max(events, key=lambda e: len(e['article_ids']))
    title = largest_event['representative_title'][:100]

    all_article_ids = []
    for event in events:
        all_article_ids.extend(event['article_ids'])

    return {
        'centroid': centroid,
        'top_entities': top_entities,
        'category': category,
        'title': title,
        'article_ids': all_article_ids,
    }


def orphan_embedding(event: Dict) -> np.ndarray:
    """Unit-normalised event embedding, as stored in the orphan pool."""
    embedding = event['embedding']
    norm = np.linalg.norm(embedding)
    if norm > 0:
        embedding = embedding / norm
    return embedding


# ---------------------------------------------------------------------------
# Assignment: state update of a storyline that receives an event
# ---------------------------------------------------------------------------

def drift_embedding(current: np.ndarray, new: np.ndarray, w_old: float, w_new: float) -> np.ndarray:
    drifted = w_old * current + w_new * new
    norm = np.linalg.norm(drifted)
    if norm > 0:
        drifted = drifted / norm
    return drifted


def merge_entities(current: Set[str], new_clean: Sequence[str], cap: int = 20) -> List[str]:
    """Union of the storyline's entities and the event's (already cleaned) entities,
    case-insensitively de-duplicated, truncated to `cap`.

    The union is a set, so which entities survive the cap depends on set iteration
    order (string hashing). This is prod behaviour; it bears on hypothesis H4.
    """
    all_entities = list(current | set(new_clean))
    seen_lower = set()
    merged = []
    for e in all_entities:
        key = e.lower()
        if key not in seen_lower:
            seen_lower.add(key)
            merged.append(e)
    return merged[:cap]


def momentum_bump(n_articles: int) -> float:
    return min(1.0, 0.1 * n_articles)


def promote_status(current_status: str, new_count: int) -> str:
    """`emerging` becomes `active` when the storyline reaches 3 articles."""
    if current_status == 'emerging' and new_count >= 3:
        return 'active'
    return current_status


# ---------------------------------------------------------------------------
# Post-clustering relevance validation
# Storylines whose title+summary match OFF-TOPIC patterns are archived.
# Patterns that MUST appear (at least one) to be considered on-scope.
# ---------------------------------------------------------------------------
SCOPE_KEYWORDS: re.Pattern = re.compile(
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

OFF_TOPIC_PATTERNS: List[re.Pattern] = [
    re.compile(p, re.IGNORECASE) for p in [
        r'\b(?:NBA|NFL|NHL|MLB|MLS|UFC|ATP|WTA|FIFA|UEFA|ICC|cricket|IPL|Serie A|Premier League|Champions League|Bundesliga|Ligue 1|La Liga)\b',
        r'\b(?:Grammy|Oscar|Emmy|Golden Globe|BAFTA|Netflix|Spotify|box.office|blockbuster|Marvel|K-pop|BTS|Taylor Swift)\b',
        r'\b(?:celebrity|gossip|fashion|Kardashian|reality TV|Love Island|Met Gala|red carpet)\b',
        r'\b(?:Real Madrid|Barcelona|Juventus|Liverpool|Arsenal|Chelsea|Ronaldo|Messi|Guardiola)\b',
        r'\b(?:recipe|ricetta|cooking|chef|restaurant|ristorante|food blog)\b',
        r'\b(?:tourist|turismo|travel guide|hotel review|vacation|vacanz)\b',
    ]
]


def is_off_topic(title: Optional[str], summary: Optional[str]) -> bool:
    """True when the storyline has no scope keyword and matches an off-topic pattern.
    Such storylines are archived right after their summary is evolved."""
    text = f"{title or ''} {summary or ''}"
    has_scope = bool(SCOPE_KEYWORDS.search(text))
    return not has_scope and any(p.search(text) for p in OFF_TOPIC_PATTERNS)
