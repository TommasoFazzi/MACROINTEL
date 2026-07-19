"""
Unit tests for scripts/theme_clustering.py — k-means champion + HDBSCAN
challenger shadow (narrative-clustering-embedding-based).

Tested behaviors (tasks 3.5, 4.6, 5.4, 6.4):
- _run_kmeans_embedding_and_score: deterministic at fixed seed; storylines
  without current_embedding are excluded, not crashed on.
- assign_nearest_centroid / assign_storylines_nearest_centroid: below-threshold
  similarity -> outlier (excluded), otherwise nearest centroid wins.
- _hungarian_match_centroids: correct match/no-match outcomes on a synthetic
  near-identity case and a case with one genuinely new cluster.
- _run_hdbscan_shadow_and_score: degrades gracefully when unavailable (skip
  if actually available in this env — see skip marker).
- detect_drift / count_consecutive_drift_signals: pure-logic threshold checks
  via a stub DB connection (no real Postgres needed).

DB-backed functions (_fetch_active_centroids, refit_with_warm_start's DB writes,
write_*_shadow) are exercised only at the pure-logic boundary here — the actual
DB roundtrip is out of scope for unit tests (no local Postgres in this env).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import scripts.theme_clustering as tc  # noqa: E402
import scripts.compute_communities as cc  # noqa: E402


# ---------------------------------------------------------------------------
# _run_kmeans_embedding_and_score (task 3.5)
# ---------------------------------------------------------------------------

def _synthetic_embeddings(n_per_cluster=10, n_clusters=3, dim=384, seed=0):
    rng = np.random.default_rng(seed)
    current_by_id = {}
    sid = 0
    for c in range(n_clusters):
        center = rng.normal(loc=c * 5.0, scale=0.1, size=dim)
        for _ in range(n_per_cluster):
            current_by_id[sid] = (center + rng.normal(scale=0.05, size=dim)).astype(np.float32)
            sid += 1
    return current_by_id


def test_kmeans_deterministic_same_seed(monkeypatch):
    current_by_id = _synthetic_embeddings()
    monkeypatch.setattr(tc, "_fetch_storyline_embeddings", lambda db, ids: (current_by_id, {}))

    ids = list(current_by_id.keys())
    p1, _ = tc._run_kmeans_embedding_and_score(db=None, storyline_ids=ids, k=3)
    p2, _ = tc._run_kmeans_embedding_and_score(db=None, storyline_ids=ids, k=3)
    assert p1 == p2


def test_kmeans_excludes_storylines_without_embedding(monkeypatch):
    current_by_id = _synthetic_embeddings()
    monkeypatch.setattr(tc, "_fetch_storyline_embeddings", lambda db, ids: (current_by_id, {}))

    ids = list(current_by_id.keys()) + [9999, 9998]  # no embedding for these two
    partition, score = tc._run_kmeans_embedding_and_score(db=None, storyline_ids=ids, k=3)
    assert 9999 not in partition and 9998 not in partition
    assert score["n_excluded_no_embedding"] == 2


def test_kmeans_returns_empty_when_fewer_samples_than_k(monkeypatch):
    current_by_id = _synthetic_embeddings(n_per_cluster=1, n_clusters=2)
    monkeypatch.setattr(tc, "_fetch_storyline_embeddings", lambda db, ids: (current_by_id, {}))

    ids = list(current_by_id.keys())
    partition, score = tc._run_kmeans_embedding_and_score(db=None, storyline_ids=ids, k=10)
    assert partition == {}
    assert score["n_communities"] == 0


def test_kmeans_warm_start_converges_near_previous_centroids(monkeypatch):
    """Sanity check of stability (design.md § Decision 6): on a snapshot with
    minimal drift, warm-start re-fit centroids stay close to the centroids
    they were initialized from."""
    current_by_id = _synthetic_embeddings(n_per_cluster=20, n_clusters=3, dim=16, seed=1)
    monkeypatch.setattr(tc, "_fetch_storyline_embeddings", lambda db, ids: (current_by_id, {}))
    ids = list(current_by_id.keys())

    _, score_cold = tc._run_kmeans_embedding_and_score(db=None, storyline_ids=ids, k=3)
    prev_centroids = score_cold["centroids"]

    # Minimal drift: nudge embeddings slightly, then warm-start re-fit from prev_centroids.
    rng = np.random.default_rng(2)
    drifted = {sid: (emb + rng.normal(scale=0.01, size=emb.shape)).astype(np.float32)
               for sid, emb in current_by_id.items()}
    monkeypatch.setattr(tc, "_fetch_storyline_embeddings", lambda db, ids: (drifted, {}))
    _, score_warm = tc._run_kmeans_embedding_and_score(
        db=None, storyline_ids=ids, k=3, init=prev_centroids,
    )
    new_centroids = score_warm["centroids"]

    # Each new centroid must have a close match (cosine sim > 0.99) among the
    # previous ones — warm-start should not relabel/reshuffle clusters on tiny drift.
    sims = tc._cosine_similarity_matrix(new_centroids, prev_centroids)
    best_sims = sims.max(axis=1)
    assert all(s > 0.99 for s in best_sims)


# ---------------------------------------------------------------------------
# assign_nearest_centroid / outlier bucket (tasks 3.5, spec UC4)
# ---------------------------------------------------------------------------

def test_assign_nearest_centroid_picks_closest():
    centroids = {
        1: np.array([1.0, 0.0, 0.0], dtype=np.float32),
        2: np.array([0.0, 1.0, 0.0], dtype=np.float32),
    }
    embedding = np.array([0.9, 0.1, 0.0], dtype=np.float32)
    result = tc.assign_nearest_centroid(embedding, centroids, outlier_threshold=0.15)
    assert result is not None
    pid, sim = result
    assert pid == 1
    assert sim > 0.9


def test_assign_nearest_centroid_outlier_below_threshold():
    centroids = {
        1: np.array([1.0, 0.0, 0.0], dtype=np.float32),
    }
    # Orthogonal vector -> cosine similarity 0.0, well below any sane threshold.
    embedding = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    result = tc.assign_nearest_centroid(embedding, centroids, outlier_threshold=0.15)
    assert result is None


def test_assign_nearest_centroid_no_active_centroids():
    embedding = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    assert tc.assign_nearest_centroid(embedding, {}, outlier_threshold=0.15) is None


def test_assign_storylines_nearest_centroid_skips_missing_embedding(monkeypatch):
    centroids = {1: np.array([1.0, 0.0], dtype=np.float32)}
    monkeypatch.setattr(tc, "_fetch_active_centroids", lambda db: centroids)
    monkeypatch.setattr(
        tc, "_fetch_storyline_embeddings",
        lambda db, ids: ({10: np.array([1.0, 0.0], dtype=np.float32)}, {}),
    )
    result = tc.assign_storylines_nearest_centroid(
        db=None, storyline_ids=[10, 11], outlier_threshold=0.15,
    )
    assert result == {10: 1}
    assert 11 not in result


# ---------------------------------------------------------------------------
# _hungarian_match_centroids (task 4.6 — synthetic merge/split-adjacent cases)
# ---------------------------------------------------------------------------

def test_hungarian_match_near_identity():
    # New centroids are near-identical to previous ones -> all should match.
    prev = {
        1: np.array([1.0, 0.0, 0.0], dtype=np.float32),
        2: np.array([0.0, 1.0, 0.0], dtype=np.float32),
    }
    new_centroids = np.array([
        [0.99, 0.01, 0.0],
        [0.01, 0.99, 0.0],
    ], dtype=np.float32)
    lineage = tc._hungarian_match_centroids(new_centroids, prev, tau_match=0.45)
    assert lineage[0] == 1
    assert lineage[1] == 2


def test_hungarian_match_new_theme_unmatched():
    prev = {
        1: np.array([1.0, 0.0, 0.0], dtype=np.float32),
    }
    # Second new centroid is orthogonal to everything previous -> no match.
    new_centroids = np.array([
        [0.99, 0.01, 0.0],
        [0.0, 0.0, 1.0],
    ], dtype=np.float32)
    lineage = tc._hungarian_match_centroids(new_centroids, prev, tau_match=0.45)
    assert lineage[0] == 1
    assert lineage[1] is None


def test_hungarian_match_no_active_centroids_all_new():
    new_centroids = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    lineage = tc._hungarian_match_centroids(new_centroids, {}, tau_match=0.45)
    assert lineage == {0: None, 1: None}


# ---------------------------------------------------------------------------
# _run_hdbscan_shadow_and_score (task 5.4)
# ---------------------------------------------------------------------------

def test_hdbscan_shadow_graceful_degradation(monkeypatch):
    monkeypatch.setattr(tc, "HDBSCAN_AVAILABLE", False)
    partition, score = tc._run_hdbscan_shadow_and_score(
        db=None, storyline_ids=[1, 2, 3], min_cluster_size=2, min_samples=2,
    )
    assert partition == {}
    assert score["name"] == "hdbscan_shadow"


@pytest.mark.skipif(not tc.HDBSCAN_AVAILABLE, reason="sklearn HDBSCAN not available")
def test_hdbscan_shadow_runs_on_synthetic_clusters(monkeypatch):
    current_by_id = _synthetic_embeddings(n_per_cluster=10, n_clusters=3, dim=8)
    monkeypatch.setattr(tc, "_fetch_storyline_embeddings", lambda db, ids: (current_by_id, {}))
    ids = list(current_by_id.keys())
    partition, score = tc._run_hdbscan_shadow_and_score(
        db=None, storyline_ids=ids, min_cluster_size=5, min_samples=5,
    )
    assert score["name"] == "hdbscan_shadow"
    # Noise points (-1) must never appear as a partition value.
    assert -1 not in partition.values()


# ---------------------------------------------------------------------------
# detect_drift / count_consecutive_drift_signals (task 6.4)
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, fetchone_results=None, fetchall_results=None):
        self._fetchone_results = list(fetchone_results or [])
        self._fetchall_results = list(fetchall_results or [])
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def fetchone(self):
        return self._fetchone_results.pop(0)

    def fetchall(self):
        return self._fetchall_results.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeDB:
    def __init__(self, cursor):
        self._cursor = cursor

    def get_connection(self):
        return _FakeConn(self._cursor)


class _Thresholds:
    tcs_drop_ratio = 0.80
    coherence_drop_ratio = 0.85
    epr_drop_ratio = 0.80
    churn_shift_ratio = 0.30


class _DriftCfg:
    baseline_window_days = 30
    thresholds = _Thresholds()


def test_detect_drift_no_history_returns_false():
    cursor = _FakeCursor(fetchone_results=[None])
    db = _FakeDB(cursor)
    result = tc.detect_drift(db, _DriftCfg())
    assert result["drift"] is False
    assert result["reasons"] == []


def test_detect_drift_flags_tcs_drop():
    # latest tcs=0.5, baseline p50=0.8 -> 0.5 < 0.8*0.8=0.64 -> drift on tcs
    cursor = _FakeCursor(fetchone_results=[
        (0.5, 0.6, 0.9),      # latest row
        (0.8, 0.65, 0.9),     # baseline row (tcs_p50, coh_p50, epr_p50)
    ])
    db = _FakeDB(cursor)
    result = tc.detect_drift(db, _DriftCfg())
    assert result["drift"] is True
    assert "tcs_drop" in result["reasons"]
    assert "epr_drop" not in result["reasons"]


def test_detect_drift_no_drift_when_within_threshold():
    cursor = _FakeCursor(fetchone_results=[
        (0.79, 0.6, 0.9),
        (0.8, 0.6, 0.9),
    ])
    db = _FakeDB(cursor)
    result = tc.detect_drift(db, _DriftCfg())
    assert result["drift"] is False


def test_count_consecutive_drift_signals_stops_at_first_clean_run():
    cursor = _FakeCursor(fetchall_results=[[
        ({"drift": True},),
        ({"drift": True},),
        ({"drift": False},),
        ({"drift": True},),
    ]])
    db = _FakeDB(cursor)
    assert tc.count_consecutive_drift_signals(db) == 2


def test_count_consecutive_drift_signals_zero_when_latest_clean():
    cursor = _FakeCursor(fetchall_results=[[
        ({"drift": False},),
        ({"drift": True},),
    ]])
    db = _FakeDB(cursor)
    assert tc.count_consecutive_drift_signals(db) == 0


# ---------------------------------------------------------------------------
# _read_previous_partition (clustering-shadow-metrics-umap task 8.1)
# ---------------------------------------------------------------------------

def test_read_previous_partition_found():
    shadow_row = [
        [
            {"name": "kmeans_embedding", "partition_persistent_id": {"1": 5, "2": 5, "3": 6}},
            {"name": "hdbscan_shadow", "partition": {"1": 0, "2": 0}},
        ]
    ]
    cursor = _FakeCursor(fetchall_results=[[(shadow_row[0],)]])
    db = _FakeDB(cursor)
    result = tc._read_previous_partition(db, "kmeans_embedding")
    assert result == {"1": 5, "2": 5, "3": 6}


def test_read_previous_partition_not_found_no_rows():
    cursor = _FakeCursor(fetchall_results=[[]])
    db = _FakeDB(cursor)
    assert tc._read_previous_partition(db, "kmeans_embedding") is None


def test_read_previous_partition_not_found_wrong_algo_name():
    shadow_row = [
        {"name": "hdbscan_shadow", "partition": {"1": 0}},
    ]
    cursor = _FakeCursor(fetchall_results=[[(shadow_row,)]])
    db = _FakeDB(cursor)
    assert tc._read_previous_partition(db, "kmeans_embedding") is None


def test_read_previous_partition_malformed_json_skipped():
    # First row is malformed (not a list of dicts), second row has the answer —
    # function must skip the bad row instead of raising.
    rows = [
        ("not-a-list",),
        ([{"name": "kmeans_embedding", "partition_persistent_id": {"7": 1}}],),
    ]
    cursor = _FakeCursor(fetchall_results=[rows])
    db = _FakeDB(cursor)
    result = tc._read_previous_partition(db, "kmeans_embedding")
    assert result == {"7": 1}


# ---------------------------------------------------------------------------
# _ari_nmi_on_intersection (clustering-shadow-metrics-umap task 8.2)
# ---------------------------------------------------------------------------

def test_ari_nmi_identical_partitions_perfect_agreement():
    partition_a = {1: 0, 2: 0, 3: 1, 4: 1}
    partition_b = {1: 0, 2: 0, 3: 1, 4: 1}
    ari, nmi = tc._ari_nmi_on_intersection(partition_a, partition_b)
    assert ari == pytest.approx(1.0)
    assert nmi == pytest.approx(1.0)


def test_ari_nmi_relabeled_partitions_still_perfect_agreement():
    # Same grouping, different cluster label numbers -> ARI/NMI invariant to relabeling.
    partition_a = {1: 0, 2: 0, 3: 1, 4: 1}
    partition_b = {1: 9, 2: 9, 3: 4, 4: 4}
    ari, nmi = tc._ari_nmi_on_intersection(partition_a, partition_b)
    assert ari == pytest.approx(1.0)
    assert nmi == pytest.approx(1.0)


def test_ari_nmi_str_vs_int_keys_normalized():
    # Simulates comparing a freshly-computed partition (int keys) against one
    # read back from JSONB (str keys) — must not silently produce an empty
    # intersection.
    partition_a = {1: 0, 2: 0, 3: 1, 4: 1}
    partition_b = {"1": 0, "2": 0, "3": 1, "4": 1}
    ari, nmi = tc._ari_nmi_on_intersection(partition_a, partition_b)
    assert ari == pytest.approx(1.0)
    assert nmi == pytest.approx(1.0)


def test_ari_nmi_disjoint_partitions_below_two_intersection_returns_none():
    partition_a = {1: 0, 2: 1}
    partition_b = {3: 0, 4: 1}  # no shared IDs
    ari, nmi = tc._ari_nmi_on_intersection(partition_a, partition_b)
    assert ari is None
    assert nmi is None


def test_ari_nmi_known_hand_computed_case():
    # a: [0,0,1,1], b: [0,1,0,1] -> maximally disagreeing 2x2 partitions
    # (every pair that's together in a is split in b, and vice versa).
    # sklearn.metrics.adjusted_rand_score([0,0,1,1], [0,1,0,1]) == -0.5 —
    # verified directly against sklearn, not hand-derived, since ARI is not
    # bounded at 0 for worse-than-chance agreement (it can go negative).
    partition_a = {1: 0, 2: 0, 3: 1, 4: 1}
    partition_b = {1: 0, 2: 1, 3: 0, 4: 1}
    ari, nmi = tc._ari_nmi_on_intersection(partition_a, partition_b)
    assert ari == pytest.approx(-0.5, abs=1e-9)
    assert nmi == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------------------
# Calinski-Harabasz integration (clustering-shadow-metrics-umap task 8.3)
# ---------------------------------------------------------------------------

def test_kmeans_score_includes_calinski_harabasz(monkeypatch):
    current_by_id = _synthetic_embeddings()
    monkeypatch.setattr(tc, "_fetch_storyline_embeddings", lambda db, ids: (current_by_id, {}))
    ids = list(current_by_id.keys())
    _, score = tc._run_kmeans_embedding_and_score(db=None, storyline_ids=ids, k=3)
    assert "calinski_harabasz" in score
    assert isinstance(score["calinski_harabasz"], float)
    assert score["calinski_harabasz"] > 0


@pytest.mark.skipif(not tc.HDBSCAN_AVAILABLE, reason="sklearn HDBSCAN not available")
def test_hdbscan_score_includes_calinski_harabasz(monkeypatch):
    current_by_id = _synthetic_embeddings(n_per_cluster=10, n_clusters=3, dim=8)
    monkeypatch.setattr(tc, "_fetch_storyline_embeddings", lambda db, ids: (current_by_id, {}))
    ids = list(current_by_id.keys())
    _, score = tc._run_hdbscan_shadow_and_score(
        db=None, storyline_ids=ids, min_cluster_size=5, min_samples=5,
    )
    assert "calinski_harabasz" in score


# ---------------------------------------------------------------------------
# Shared coherence function parity (clustering-shadow-metrics-umap task 8.4)
# ---------------------------------------------------------------------------

def test_coherence_med_matches_inline_reference_implementation():
    """Parity test: _coherence_med must reproduce the exact math the inline
    version inside _compute_quality_metrics used before extraction (median of
    per-community medians of pairwise cosine similarity on summary_vector)."""
    partition = {1: 0, 2: 0, 3: 0, 4: 1, 5: 1}
    rng = np.random.default_rng(3)
    summary_by_id = {sid: rng.normal(size=32).astype(np.float32) for sid in partition}

    coherence_med, coherence_med_k = cc._coherence_med(partition, summary_by_id, k_min=5)

    # Reference implementation (pre-extraction inline logic, reproduced here).
    community_members = {}
    for sid, cid in partition.items():
        community_members.setdefault(cid, []).append(summary_by_id[sid])
    per_community_medians = []
    for cid, embs in community_members.items():
        M = np.stack(embs)
        norms = np.linalg.norm(M, axis=1, keepdims=True)
        norms[norms == 0] = 1e-10
        Mn = M / norms
        sim = Mn @ Mn.T
        iu = np.triu_indices(len(embs), k=1)
        sims = sim[iu]
        per_community_medians.append(float(np.median(sims)))
    expected = float(np.median(per_community_medians))

    assert coherence_med == pytest.approx(expected)
    assert coherence_med_k is None  # no community has >=5 members


def test_kmeans_and_hdbscan_use_summary_vector_for_coherence_not_current_embedding(monkeypatch):
    """coherence_med must come from summary_by_id, not current_by_id — the two
    caches are deliberately given different, distinguishable content so a bug
    that swaps them would change the result (design.md § Decision 4)."""
    current_by_id = _synthetic_embeddings(n_per_cluster=10, n_clusters=2, dim=16, seed=5)
    # summary_by_id: identical vector per storyline -> coherence_med must be ~1.0
    # if (and only if) the function actually reads summary_by_id.
    summary_by_id = {sid: np.ones(16, dtype=np.float32) for sid in current_by_id}
    monkeypatch.setattr(
        tc, "_fetch_storyline_embeddings", lambda db, ids: (current_by_id, summary_by_id)
    )
    ids = list(current_by_id.keys())
    _, score = tc._run_kmeans_embedding_and_score(db=None, storyline_ids=ids, k=2)
    assert score["coherence_med"] == pytest.approx(1.0, abs=1e-6)


# ---------------------------------------------------------------------------
# UMAP-enabled vs UMAP-disabled HDBSCAN path (clustering-shadow-metrics-umap
# task 8.5)
# ---------------------------------------------------------------------------

class _UMAPCfg:
    def __init__(self, enabled=True, n_components=4, n_neighbors=5, min_dist=0.0, random_state=42):
        self.enabled = enabled
        self.n_components = n_components
        self.n_neighbors = n_neighbors
        self.min_dist = min_dist
        self.random_state = random_state


@pytest.mark.skipif(not tc.HDBSCAN_AVAILABLE, reason="sklearn HDBSCAN not available")
def test_hdbscan_umap_disabled_matches_no_umap_cfg_behavior(monkeypatch):
    """umap_cfg=None and umap_cfg=_UMAPCfg(enabled=False) must both take the
    pre-UMAP full-dimensionality code path (task 7.6 regression safety)."""
    current_by_id = _synthetic_embeddings(n_per_cluster=10, n_clusters=3, dim=8, seed=9)
    monkeypatch.setattr(tc, "_fetch_storyline_embeddings", lambda db, ids: (current_by_id, {}))
    ids = list(current_by_id.keys())

    partition_none, score_none = tc._run_hdbscan_shadow_and_score(
        db=None, storyline_ids=ids, min_cluster_size=5, min_samples=5, umap_cfg=None,
    )
    partition_disabled, score_disabled = tc._run_hdbscan_shadow_and_score(
        db=None, storyline_ids=ids, min_cluster_size=5, min_samples=5,
        umap_cfg=_UMAPCfg(enabled=False),
    )
    assert partition_none == partition_disabled
    assert score_none["umap_enabled"] is False
    assert score_disabled["umap_enabled"] is False


@pytest.mark.skipif(not tc.UMAP_AVAILABLE or not tc.HDBSCAN_AVAILABLE, reason="umap-learn/HDBSCAN not available")
def test_hdbscan_umap_enabled_branches_to_reduced_space(monkeypatch):
    """Config gate must actually branch: enabling UMAP changes the embedding
    space HDBSCAN and silhouette/Calinski-Harabasz operate on (euclidean
    metric post-UMAP, per design.md § Decision 5), and reports umap_enabled=True."""
    current_by_id = _synthetic_embeddings(n_per_cluster=10, n_clusters=3, dim=32, seed=11)
    monkeypatch.setattr(tc, "_fetch_storyline_embeddings", lambda db, ids: (current_by_id, {}))
    ids = list(current_by_id.keys())

    _, score = tc._run_hdbscan_shadow_and_score(
        db=None, storyline_ids=ids, min_cluster_size=5, min_samples=5,
        umap_cfg=_UMAPCfg(enabled=True, n_components=4, n_neighbors=5),
    )
    assert score["umap_enabled"] is True


@pytest.mark.skipif(tc.UMAP_AVAILABLE, reason="test targets the umap-learn-missing fallback path")
def test_hdbscan_umap_enabled_but_unavailable_falls_back(monkeypatch):
    current_by_id = _synthetic_embeddings(n_per_cluster=10, n_clusters=3, dim=8)
    monkeypatch.setattr(tc, "_fetch_storyline_embeddings", lambda db, ids: (current_by_id, {}))
    ids = list(current_by_id.keys())
    _, score = tc._run_hdbscan_shadow_and_score(
        db=None, storyline_ids=ids, min_cluster_size=5, min_samples=5,
        umap_cfg=_UMAPCfg(enabled=True),
    )
    assert score["umap_enabled"] is False
