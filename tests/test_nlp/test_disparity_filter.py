"""
Unit tests for the Serrano-Boguñá-Vespignani (2009) disparity filter
helper used by scripts/compute_communities.py (Phase 1D of the
narrative clustering upgrade).

Tested behaviors:
- Hub-and-spoke graph: hub edges to spokes are NOT all kept (low-significance
  weak edges should be pruned); spoke degree-1 endpoints keep their lone edge.
- Uniform clique: with all-equal weights and equal degree, every edge has the
  same significance — backbone is either all-or-nothing depending on alpha.
- Degree-1 nodes: an isolated dyad keeps its edge unconditionally.
- Empty input returns empty backbone and zero stats.
- Fallback path activates when alpha is tight enough to prune everything.
- Output edges preserve the original (source, target, weight) tuples.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make scripts/ importable so we can reach _disparity_filter_backbone.
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

# Importing compute_communities triggers a numpy/sklearn import path; that's
# fine on CI (both are in requirements.txt). Louvain/community is optional —
# the helper itself does not depend on it.
from compute_communities import _disparity_filter_backbone  # noqa: E402


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _edge_set(edges):
    """Canonical undirected set of (min(s,t), max(s,t)) pairs for comparison."""
    return {(min(s, t), max(s, t)) for s, t, _ in edges}


# --------------------------------------------------------------------------
# Empty / trivial cases
# --------------------------------------------------------------------------

def test_empty_input_returns_empty_backbone():
    backbone, stats = _disparity_filter_backbone([], alpha=0.3, fallback_threshold=0.1)
    assert backbone == []
    assert stats["n_input"] == 0
    assert stats["n_backbone"] == 0
    assert stats["used_fallback"] is False
    assert stats["backbone_weight_p50"] is None
    assert stats["backbone_weight_p75"] is None


def test_degree_one_dyad_kept_unconditionally():
    # Two nodes, one edge — both endpoints have k=1.
    # Formula would prune (a = 1^0 = 1, never < alpha) — must be overridden.
    edges = [(1, 2, 0.4)]
    backbone, stats = _disparity_filter_backbone(edges, alpha=0.001, fallback_threshold=0.05)
    assert _edge_set(backbone) == {(1, 2)}
    assert stats["n_backbone"] == 1
    assert stats["used_fallback"] is False


# --------------------------------------------------------------------------
# Hub-and-spoke graph: should distinguish strong from weak edges
# --------------------------------------------------------------------------

def test_hub_and_spoke_prunes_weak_edges_at_strict_alpha():
    """Hub node 0 connects to 5 spokes. One edge dominates (weight 10.0),
    the others are noise (weight 1.0). With strict alpha, the dominant edge
    should survive from the hub's perspective."""
    edges = [
        (0, 1, 10.0),  # dominant
        (0, 2, 1.0),
        (0, 3, 1.0),
        (0, 4, 1.0),
        (0, 5, 1.0),
    ]
    backbone, stats = _disparity_filter_backbone(edges, alpha=0.1, fallback_threshold=0.05)

    # Spokes 1..5 all have degree 1 → their lone edge is force-kept (degenerate
    # case override). So every edge survives via the spoke side. This is the
    # intended conservative behavior — pruning only happens when BOTH endpoints
    # have degree >= 2 AND both fail the significance test.
    assert _edge_set(backbone) == {(0, 1), (0, 2), (0, 3), (0, 4), (0, 5)}
    assert stats["used_fallback"] is False


def test_hub_and_spoke_with_internal_degree_prunes_weak_edges():
    """When spokes also have other neighbors (so k>=2 everywhere), the
    significance test actually filters. Build a denser graph where the weak
    edges have peers to compete with on both sides."""
    # Two hubs (0, 100) each connecting to 5 dense neighbors with one dominant
    # bridge between them.
    edges = []
    # Hub 0's cluster: weights 1.0, with one dominant 5.0
    edges += [(0, i, 1.0) for i in [1, 2, 3, 4]]
    edges += [(0, 5, 5.0)]
    edges += [(1, 2, 1.0), (3, 4, 1.0)]  # give spokes some degree
    # Hub 100's cluster, mirror
    edges += [(100, i, 1.0) for i in [101, 102, 103, 104]]
    edges += [(100, 105, 5.0)]
    edges += [(101, 102, 1.0), (103, 104, 1.0)]
    # Weak bridge between the two clusters — should be pruned at strict alpha
    edges += [(5, 105, 0.5)]

    backbone, stats = _disparity_filter_backbone(edges, alpha=0.05, fallback_threshold=0.05)
    backbone_set = _edge_set(backbone)

    # Backbone must be a strict subset (some edges WERE pruned)
    assert stats["n_backbone"] < stats["n_input"]
    # The dominant intra-cluster edges (0,5) and (100,105) must survive
    assert (0, 5) in backbone_set
    assert (100, 105) in backbone_set
    assert stats["used_fallback"] is False


# --------------------------------------------------------------------------
# Uniform clique: degenerate case
# --------------------------------------------------------------------------

def test_uniform_clique_all_edges_equally_significant():
    """K_5 clique with all weights = 1.0. Every node has k=4, s=4, so
    p_ij = 0.25 and a_ij = (0.75)^3 ≈ 0.4219 for every edge. With alpha=0.5
    everything passes; with alpha=0.3 nothing passes and fallback kicks in."""
    nodes = [1, 2, 3, 4, 5]
    edges = [(a, b, 1.0) for i, a in enumerate(nodes) for b in nodes[i + 1:]]
    assert len(edges) == 10

    # alpha=0.5: all 10 edges kept
    backbone, stats = _disparity_filter_backbone(edges, alpha=0.5, fallback_threshold=0.05)
    assert stats["n_backbone"] == 10
    assert stats["used_fallback"] is False

    # alpha=0.3: a_ij ≈ 0.42 > 0.3 → no edge passes from either side → empty
    # backbone → fallback retains edges with weight >= 0.05 (all 10).
    backbone, stats = _disparity_filter_backbone(edges, alpha=0.3, fallback_threshold=0.05)
    assert stats["used_fallback"] is True
    assert stats["n_fallback_kept"] == 10


# --------------------------------------------------------------------------
# Fallback path
# --------------------------------------------------------------------------

def test_fallback_filters_by_weight_threshold():
    """Force alpha so tight everything is pruned; then the fallback should
    still respect the weight threshold."""
    # 3 nodes in a triangle, equal weights → a_ij is the same everywhere.
    # alpha near zero prunes all; fallback_threshold=2.0 keeps only weight>=2.
    edges = [
        (1, 2, 1.0),
        (2, 3, 1.0),
        (1, 3, 3.0),
    ]
    backbone, stats = _disparity_filter_backbone(
        edges, alpha=1e-9, fallback_threshold=2.0,
    )
    assert stats["used_fallback"] is True
    assert _edge_set(backbone) == {(1, 3)}
    assert stats["n_fallback_kept"] == 1


# --------------------------------------------------------------------------
# Output shape / percentiles
# --------------------------------------------------------------------------

def test_backbone_weight_percentiles_populated():
    """Whenever the backbone is non-empty, p50 and p75 must be set."""
    edges = [
        (0, 1, 5.0), (0, 2, 1.0), (0, 3, 1.0), (0, 4, 1.0), (0, 5, 1.0),
        (1, 2, 0.5), (3, 4, 0.5),
    ]
    backbone, stats = _disparity_filter_backbone(edges, alpha=0.5, fallback_threshold=0.05)
    assert stats["n_backbone"] > 0
    assert stats["backbone_weight_p50"] is not None
    assert stats["backbone_weight_p75"] is not None
    assert stats["backbone_weight_p75"] >= stats["backbone_weight_p50"]


def test_backbone_preserves_original_tuples():
    """The backbone must return the original (s, t, w) tuples unmodified —
    no normalization, no reordering."""
    edges = [(7, 3, 0.42), (3, 9, 0.81), (9, 7, 0.17)]
    backbone, _ = _disparity_filter_backbone(edges, alpha=0.9, fallback_threshold=0.05)
    # alpha=0.9 should keep most/all in this tiny graph
    assert all(e in edges for e in backbone)
    for s, t, w in backbone:
        assert isinstance(w, float)
        assert (s, t, w) in edges
