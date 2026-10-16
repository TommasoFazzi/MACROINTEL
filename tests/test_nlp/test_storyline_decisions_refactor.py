"""Equivalence tests for the extraction of `src/nlp/storyline_decisions.py`
(`storyline-formation-causal-review` task 2.1–2.3, design.md D2).

Each `_old_*` function below is a verbatim copy of the pre-extraction code in
`NarrativeProcessor` (git 7bde7af), with `self.X` replaced by explicit arguments
and DB I/O cut away. The new pure functions must give identical results on the
same inputs, in the same process (so set iteration order is shared). The clock
is frozen by replacing this module's `datetime` for the old copy and passing the
same instant to the new code.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime as _real_datetime, timedelta

import numpy as np
import pytest

from src.nlp import storyline_decisions as sd
from src.nlp.narrative_processor import NarrativeProcessor

FROZEN_NOW = _real_datetime(2026, 10, 16, 6, 30, 0)


class _FrozenDatetime(_real_datetime):
    @classmethod
    def now(cls, tz=None):
        return FROZEN_NOW


datetime = _FrozenDatetime  # used by the old copies below

extract = NarrativeProcessor._extract_entity_list
MICRO = 0.90
PARAMS = sd.MatchParams(threshold=0.75, time_decay_factor=0.05, entity_boost=0.10,
                        entity_jaccard_threshold=0.05)


# ---------------------------------------------------------------------------
# Verbatim copies of the pre-extraction code
# ---------------------------------------------------------------------------

def _old_article_to_event(article):
    entities = extract(article.get('entities', {}))
    return {
        'embedding': np.array(article['embedding']),
        'entities': entities,
        'article_ids': [article['id']],
        'representative_title': article['title'],
        'category': article.get('category'),
    }


def _old_articles_to_event(articles):
    embeddings = np.array([a['embedding'] for a in articles])
    centroid = embeddings.mean(axis=0)
    norm = np.linalg.norm(centroid)
    if norm > 0:
        centroid = centroid / norm
    all_entities = []
    entity_counts = Counter()
    for a in articles:
        ents = extract(a.get('entities', {}))
        all_entities.extend(ents)
        entity_counts.update(ents)
    best_article = max(articles, key=lambda a: len(extract(a.get('entities', {}))))
    categories = [a.get('category') for a in articles if a.get('category')]
    category = Counter(categories).most_common(1)[0][0] if categories else None
    return {
        'embedding': centroid,
        'entities': list(entity_counts.keys()),
        'article_ids': [a['id'] for a in articles],
        'representative_title': best_article['title'],
        'category': category,
    }


def _old_create_micro_clusters(articles, MICRO_CLUSTER_THRESHOLD):
    if not articles:
        return []
    n = len(articles)
    if n == 1:
        return [_old_article_to_event(articles[0])]
    embeddings = np.array([a['embedding'] for a in articles])
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
            if sim_matrix[i, j] >= MICRO_CLUSTER_THRESHOLD:
                cluster_indices.append(j)
                assigned[j] = True
        clusters.append(cluster_indices)
    events = []
    for indices in clusters:
        cluster_articles = [articles[i] for i in indices]
        events.append(_old_articles_to_event(cluster_articles))
    return events


def _old_find_best_match(event, active_storylines, TIME_DECAY_FACTOR, ENTITY_JACCARD_THRESHOLD,
                         ENTITY_BOOST, MATCH_THRESHOLD):
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
        time_penalty = TIME_DECAY_FACTOR * days_inactive
        s_entities = storyline['key_entities']
        entity_boost = 0.0
        if event_entities and s_entities:
            intersection = len(event_entities & s_entities)
            union = len(event_entities | s_entities)
            jaccard = intersection / union if union > 0 else 0
            if jaccard >= ENTITY_JACCARD_THRESHOLD:
                entity_boost = ENTITY_BOOST
        score = cosine_sim - time_penalty + entity_boost
        if score > best_score and score >= MATCH_THRESHOLD:
            best_score = score
            best_match = {
                'storyline_id': storyline['storyline_id'],
                'title': storyline['title'],
                'score': score,
                'cosine_sim': cosine_sim,
                'time_penalty': time_penalty,
                'entity_boost': entity_boost,
            }
    return best_match


def _old_residual_groups(orphaned_events, HDBSCAN_MIN_CLUSTER_SIZE, HDBSCAN_MIN_SAMPLES):
    from sklearn.cluster import HDBSCAN
    embeddings = np.array([e['embedding'] for e in orphaned_events])
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    embeddings_norm = embeddings / np.maximum(norms, 1e-10)
    hdb = HDBSCAN(min_cluster_size=HDBSCAN_MIN_CLUSTER_SIZE, min_samples=HDBSCAN_MIN_SAMPLES,
                  metric='euclidean')
    labels = hdb.fit_predict(embeddings_norm)
    cluster_map = defaultdict(list)
    noise_events = []
    for idx, label in enumerate(labels):
        if label == -1:
            noise_events.append(orphaned_events[idx])
        else:
            cluster_map[label].append(orphaned_events[idx])
    return labels, cluster_map, noise_events


def _old_assignment_update(event, row, DRIFT_WEIGHT_OLD, DRIFT_WEIGHT_NEW):
    """Steps 3–5 of the old `_assign_event_to_storyline`, after the SELECT."""
    current_emb = np.array(row[0]) if row[0] is not None else event['embedding']
    current_entities = set(row[1] or [])
    current_count = row[2] or 0
    current_status = row[3]
    new_emb = event['embedding']
    drifted = DRIFT_WEIGHT_OLD * current_emb + DRIFT_WEIGHT_NEW * new_emb
    norm = np.linalg.norm(drifted)
    if norm > 0:
        drifted = drifted / norm
    new_event_entities = [
        NarrativeProcessor._clean_entity(e) for e in event['entities']
        if not NarrativeProcessor._is_garbage_entity(NarrativeProcessor._clean_entity(e))
    ]
    all_entities = list(current_entities | set(new_event_entities))
    seen_lower = set()
    merged_entities = []
    for e in all_entities:
        key = e.lower()
        if key not in seen_lower:
            seen_lower.add(key)
            merged_entities.append(e)
    merged_entities = merged_entities[:20]
    new_count = current_count + len(event['article_ids'])
    momentum_bump = min(1.0, 0.1 * len(event['article_ids']))
    new_status = current_status
    if current_status == 'emerging' and new_count >= 3:
        new_status = 'active'
    return drifted, merged_entities, new_count, momentum_bump, new_status


def _old_storyline_seed(events):
    """Pre-extraction body of `_create_storyline_from_events`, up to the INSERT."""
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
    return centroid, top_entities, category, title, all_article_ids


def _old_orphan_embedding(event):
    embedding = event['embedding']
    norm = np.linalg.norm(embedding)
    if norm > 0:
        embedding = embedding / norm
    return embedding


def _old_is_archived_off_topic(title, summary, _SCOPE_KEYWORDS, _OFF_TOPIC_PATTERNS):
    text = f"{title or ''} {summary or ''}"
    has_scope = bool(_SCOPE_KEYWORDS.search(text))
    is_off_topic = any(p.search(text) for p in _OFF_TOPIC_PATTERNS)
    return not has_scope and is_off_topic


# ---------------------------------------------------------------------------
# Fixtures: synthetic articles and storylines with near-duplicates and ties
# ---------------------------------------------------------------------------

ENTITY_POOL = ["Iran", "United States", "Hormuz", "Israel", "Gaza", "Russia", "Ukraine",
               "NATO", "European Union", "China", "Taiwan", "Romania", "Bucharest", "IRGC",
               "Pentagon", "Kremlin", "4Trump", "The Hague", "Reuters", "US-"]


def _unit(v):
    return v / np.linalg.norm(v)


def _articles(seed: int, n: int = 40, dim: int = 16):
    rng = np.random.default_rng(seed)
    bases = [_unit(rng.normal(size=dim)) for _ in range(6)]
    out = []
    for i in range(n):
        base = bases[rng.integers(len(bases))]
        noise = rng.choice([0.02, 0.1, 0.6])  # near-duplicates, same story, unrelated
        emb = _unit(base + noise * rng.normal(size=dim)) * rng.uniform(0.5, 2.0)
        ents = list(rng.choice(ENTITY_POOL, size=rng.integers(0, 6), replace=False))
        fmt = {'clean': {'all': ents}} if i % 3 else {'by_type': {'GPE': ents[:2], 'ORG': ents[2:]}}
        out.append({'id': 1000 + i, 'title': f"Article {i}", 'embedding': emb.tolist(),
                    'entities': fmt, 'category': rng.choice([None, 'geo', 'econ'])})
    return out


def _storylines(seed: int, n: int = 25, dim: int = 16):
    rng = np.random.default_rng(seed + 100)
    out = []
    for i in range(n):
        emb = None if i == 3 else _unit(rng.normal(size=dim))
        if i == 4:
            emb = np.zeros(dim)  # norm 0 → skipped
        last = None if i == 5 else FROZEN_NOW - timedelta(hours=float(rng.uniform(-2, 200)))
        ents = set(e.lower() for e in rng.choice(ENTITY_POOL, size=rng.integers(0, 8), replace=False))
        out.append({'storyline_id': 500 + i, 'title': f"Storyline {i}", 'current_embedding': emb,
                    'key_entities': ents, 'last_update': last})
    # an exact duplicate of storyline 0 → tie, the first must win
    dup = dict(out[0])
    dup['storyline_id'] = 999
    out.append(dup)
    return out


def _assert_event_equal(a, b):
    assert a['article_ids'] == b['article_ids']
    assert a['entities'] == b['entities']
    assert a['representative_title'] == b['representative_title']
    assert a['category'] == b['category']
    assert np.array_equal(a['embedding'], b['embedding'])


SEEDS = range(8)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("seed", SEEDS)
def test_micro_clusters_identical(seed):
    arts = _articles(seed)
    old = _old_create_micro_clusters(arts, MICRO)
    new = sd.create_micro_clusters(arts, MICRO, extract)
    assert len(old) == len(new)
    assert any(len(e['article_ids']) > 1 for e in new)  # fixture exercises merging
    for a, b in zip(old, new):
        _assert_event_equal(a, b)


@pytest.mark.unit
def test_micro_clusters_edge_cases():
    assert sd.create_micro_clusters([], MICRO, extract) == []
    one = _articles(0, n=1)
    _assert_event_equal(_old_create_micro_clusters(one, MICRO)[0],
                        sd.create_micro_clusters(one, MICRO, extract)[0])


@pytest.mark.unit
@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("threshold", [0.0, 0.45, 0.75, 2.0])
def test_best_match_identical(seed, threshold):
    params = sd.MatchParams(threshold, PARAMS.time_decay_factor, PARAMS.entity_boost,
                            PARAMS.entity_jaccard_threshold)
    storylines = _storylines(seed)
    for event in sd.create_micro_clusters(_articles(seed), MICRO, extract):
        old = _old_find_best_match(event, storylines, params.time_decay_factor,
                                   params.entity_jaccard_threshold, params.entity_boost, threshold)
        new = sd.select_match(sd.score_candidates(event, storylines, FROZEN_NOW, params), threshold)
        if old is None:
            assert new is None
            continue
        assert new is not None
        assert new.storyline_id == old['storyline_id']
        assert new.title == old['title']
        assert new.score == old['score']
        assert new.cosine_sim == old['cosine_sim']
        assert new.time_penalty == old['time_penalty']
        assert new.entity_boost == old['entity_boost']


@pytest.mark.unit
def test_best_match_tie_goes_to_first_and_empty_list():
    storylines = _storylines(0)
    event = {'embedding': storylines[0]['current_embedding'], 'entities': []}
    match = sd.select_match(sd.score_candidates(event, storylines, FROZEN_NOW, PARAMS), 0.0)
    assert match.storyline_id == 500  # not its duplicate 999
    assert sd.select_match(sd.score_candidates(event, [], FROZEN_NOW, PARAMS), 0.0) is None


@pytest.mark.unit
@pytest.mark.parametrize("seed", SEEDS)
def test_residual_labels_identical(seed):
    events = sd.create_micro_clusters(_articles(seed, n=60), 0.999, extract)
    old_labels, _, _ = _old_residual_groups(events, 3, 2)
    emb = np.array([e['embedding'] for e in events])
    assert np.array_equal(old_labels, sd.residual_labels(emb, 3, 2))


@pytest.mark.unit
@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("status,count", [('emerging', 0), ('emerging', 1), ('emerging', 5),
                                          ('active', 2), ('stabilized', None)])
def test_assignment_update_identical(seed, status, count):
    rng = np.random.default_rng(seed)
    events = sd.create_micro_clusters(_articles(seed), MICRO, extract)
    for event in events:
        cur_emb = None if seed == 0 else rng.normal(size=16).tolist()
        cur_ents = list(rng.choice(ENTITY_POOL, size=rng.integers(0, 25 if seed % 2 else 5)))
        row = (cur_emb, cur_ents, count, status)
        drifted, merged, new_count, bump, new_status = _old_assignment_update(event, row, 0.85, 0.15)

        current_emb = np.array(row[0]) if row[0] is not None else event['embedding']
        new_clean = [NarrativeProcessor._clean_entity(e) for e in event['entities']
                     if not NarrativeProcessor._is_garbage_entity(NarrativeProcessor._clean_entity(e))]
        n = len(event['article_ids'])
        assert np.array_equal(drifted, sd.drift_embedding(current_emb, event['embedding'], 0.85, 0.15))
        assert merged == sd.merge_entities(set(row[1] or []), new_clean)
        assert new_count == (row[2] or 0) + n
        assert bump == sd.momentum_bump(n)
        assert new_status == sd.promote_status(row[3], new_count)


@pytest.mark.unit
@pytest.mark.parametrize("seed", SEEDS)
def test_storyline_seed_and_orphan_embedding_identical(seed):
    events = sd.create_micro_clusters(_articles(seed, n=60), MICRO, extract)
    events[1]['embedding'] = np.zeros_like(events[1]['embedding'])  # norm 0 path
    for size in (1, 2, 5, len(events)):
        group = events[:size]
        centroid, ents, cat, title, ids = _old_storyline_seed(group)
        new = sd.storyline_seed(group)
        assert np.array_equal(centroid, new['centroid'])
        assert (ents, cat, title, ids) == (new['top_entities'], new['category'], new['title'],
                                           new['article_ids'])
    for event in events:
        assert np.array_equal(_old_orphan_embedding(event), sd.orphan_embedding(event))


@pytest.mark.unit
@pytest.mark.parametrize("title,summary", [
    ("Champions League final", None),
    ("Champions League final", "Russia sanctions on club owners"),
    ("Netflix hit series", "box office record"),
    ("Iran talks", "Netflix documentary on Iran"),
    (None, None),
    ("Local bakery opens", ""),
    ("Taylor Swift tour", "Tourist boom in Lisbon"),
])
def test_relevance_predicate_identical(title, summary):
    old = _old_is_archived_off_topic(title, summary, sd.SCOPE_KEYWORDS, sd.OFF_TOPIC_PATTERNS)
    assert old == sd.is_off_topic(title, summary)


@pytest.mark.unit
def test_processor_uses_extracted_functions():
    """The processor's stage methods delegate to the pure functions, so prod and the
    replay share one implementation."""
    import inspect
    src = inspect.getsource(NarrativeProcessor)
    for name in ("create_micro_clusters", "score_candidates", "select_match", "residual_labels",
                 "drift_embedding", "merge_entities", "momentum_bump", "promote_status", "is_off_topic",
                 "storyline_seed", "orphan_embedding"):
        assert f"sd.{name}(" in src, name
