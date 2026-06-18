"""
Unit tests for the Phase 1E 4-way shadow comparison framework helpers in
scripts/compute_communities.py (tasks 1.18-1.21, design.md § Decision 1 + 22).

Tested behaviors:
- _run_leiden_cpm: deterministic with seed=42 on the karate-club graph (two runs
  give the same partition); IDs renumbered by descending community size.
- _build_igraph: preserves storyline IDs as vertex names; isolated nodes survive.
- _run_leiden_cpm_adaptive_sweep: γ-range derived from the edge-weight median and
  clamped into the config bounding box; gate_failed flag behaves; the adaptive
  range differs between a dense and a sparse weight distribution.
- _run_louvain_and_score / _run_leiden_cpm_and_score: emit the unified, JSON-
  serializable schema with identical keys.
- compute_shadow_partitions: returns 4 partitions (Louvain×2 + Leiden×2) with the
  expected names, JSON-serializable.

_compute_quality_metrics hits the DB (embeddings), so it is monkeypatched to a
deterministic stub — these tests exercise graph/partition logic, not embeddings.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import compute_communities as cc  # noqa: E402
from src.nlp.config import load_clustering_config  # noqa: E402

pytestmark = pytest.mark.skipif(
    not cc.LEIDEN_AVAILABLE, reason="leidenalg/python-igraph not installed"
)


# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------

@pytest.fixture
def stub_quality(monkeypatch):
    """Replace _compute_quality_metrics (DB-backed) with a deterministic stub:
    silhouette = 0.1, coherence = 0.50 (passes the 0.45 gate). Accepts the
    optional `embedding_cache` kwarg added by Fix A (Phase 1E perf)."""
    monkeypatch.setattr(
        cc, "_compute_quality_metrics",
        lambda db, partition, embedding_cache=None: (0.1, 0.50),
    )
    # Also stub the embedding fetch so compute_shadow_partitions can run with db=None.
    monkeypatch.setattr(cc, "_fetch_storyline_embeddings", lambda db, ids: ({}, {}))


def _karate_edges():
    import igraph as ig
    g = ig.Graph.Famous("Zachary")
    all_ids = list(range(g.vcount()))
    edges = [(e.source, e.target, 1.0) for e in g.es]
    return all_ids, edges


# --------------------------------------------------------------------------
# Task 1.18 — _run_leiden_cpm determinism
# --------------------------------------------------------------------------

def test_leiden_cpm_deterministic():
    all_ids, edges = _karate_edges()
    p1 = cc._run_leiden_cpm(all_ids, edges, resolution_param=0.1)
    p2 = cc._run_leiden_cpm(all_ids, edges, resolution_param=0.1)
    assert p1 == p2, "seed=42 must make Leiden+CPM deterministic across runs"
    assert set(p1.keys()) == set(all_ids)


def test_leiden_cpm_renumbered_by_size():
    all_ids, edges = _karate_edges()
    part = cc._run_leiden_cpm(all_ids, edges, resolution_param=0.1)
    from collections import Counter
    freq = Counter(part.values())
    sizes = [freq[c] for c in sorted(freq)]
    # Community 0 must be the largest (descending-size renumber, like Louvain path).
    assert sizes[0] == max(freq.values())


def test_build_igraph_preserves_ids_and_isolated():
    all_ids = [101, 202, 303]  # 303 is isolated
    edges = [(101, 202, 0.5)]
    g = cc._build_igraph(all_ids, edges)
    assert sorted(g.vs["name"]) == [101, 202, 303]
    assert g.vcount() == 3 and g.ecount() == 1
    # Isolated node 303 still receives a community in the partition.
    part = cc._run_leiden_cpm(all_ids, edges, resolution_param=0.1)
    assert 303 in part


# --------------------------------------------------------------------------
# Task 1.19 — adaptive γ-sweep
# --------------------------------------------------------------------------

def test_gamma_sweep_range_clamped_and_eight(stub_quality):
    cfg = load_clustering_config()
    all_ids, edges = _karate_edges()  # all weights = 1.0 → median 1.0
    _, stats, gamma_used, gamma_range = cc._run_leiden_cpm_adaptive_sweep(
        cfg=cfg, db=None, all_ids=all_ids, edges=edges
    )
    assert len(gamma_range) == 8
    lo_box = min(cfg.community.resolution_sweep)
    hi_box = max(cfg.community.resolution_sweep)
    for g in gamma_range:
        assert lo_box <= g <= hi_box, "γ must stay inside the config bounding box"
    assert gamma_used in gamma_range
    assert "gate_failed" in stats


def test_gamma_range_differs_dense_vs_sparse(stub_quality):
    cfg = load_clustering_config()
    nodes = list(range(20))
    # Dense weights (median ~0.9) vs sparse weights (median ~0.02): the adaptive
    # range, derived from median_w, must differ between the two.
    dense = [(i, i + 1, 0.9) for i in range(19)]
    sparse = [(i, i + 1, 0.02) for i in range(19)]
    _, _, _, range_dense = cc._run_leiden_cpm_adaptive_sweep(cfg=cfg, db=None, all_ids=nodes, edges=dense)
    _, _, _, range_sparse = cc._run_leiden_cpm_adaptive_sweep(cfg=cfg, db=None, all_ids=nodes, edges=sparse)
    assert range_dense != range_sparse


def test_gate_failed_when_coherence_low(monkeypatch):
    cfg = load_clustering_config()
    # Force coherence below the 0.45 gate for every γ → gate_failed must be True.
    monkeypatch.setattr(
        cc, "_compute_quality_metrics",
        lambda db, p, embedding_cache=None: (0.0, 0.10),
    )
    all_ids, edges = _karate_edges()
    _, stats, _, _ = cc._run_leiden_cpm_adaptive_sweep(cfg=cfg, db=None, all_ids=all_ids, edges=edges)
    assert stats["gate_failed"] is True


# --------------------------------------------------------------------------
# Task 1.20 — unified score schema
# --------------------------------------------------------------------------

_UNIFIED_KEYS = {
    "name", "n_edges", "n_communities", "n_singletons", "max_community_size",
    "avg_community_size", "modularity", "silhouette", "coherence_med",
    "runtime_ms", "gamma_used", "gamma_sweep_range",
}


def test_louvain_and_leiden_share_schema(stub_quality):
    cfg = load_clustering_config()
    all_ids, edges = _karate_edges()
    louvain = cc._run_louvain_and_score(None, all_ids, edges, resolution=0.8, name="louvain_full")
    leiden = cc._run_leiden_cpm_and_score(None, all_ids, edges, cfg, name="leiden_full")
    assert _UNIFIED_KEYS <= set(louvain.keys())
    assert _UNIFIED_KEYS <= set(leiden.keys())
    # Louvain has no γ; Leiden populates both γ fields.
    assert louvain["gamma_used"] is None and louvain["gamma_sweep_range"] is None
    assert leiden["gamma_used"] is not None and leiden["gamma_sweep_range"] is not None
    # Both must be JSON-serializable (they land in a JSONB column).
    json.dumps(louvain)
    json.dumps(leiden)


# --------------------------------------------------------------------------
# Task 1.21 — compute_shadow_partitions (4-way)
# --------------------------------------------------------------------------

def test_compute_shadow_partitions_four_way(stub_quality):
    cfg = load_clustering_config()
    all_ids, active = _karate_edges()
    # Use a subset as the "backbone" to mimic the sparsified graph.
    backbone = active[: len(active) // 2]
    parts = cc.compute_shadow_partitions(None, all_ids, active, backbone, cfg)
    names = [p["name"] for p in parts]
    assert names == ["louvain_full", "louvain_backbone", "leiden_full", "leiden_backbone"]
    json.dumps(parts)  # whole array must be JSONB-serializable
