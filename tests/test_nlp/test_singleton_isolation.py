"""
Unit tests for the degree-0 singleton isolation helper used by
scripts/compute_communities.py (Phase 1F, task 1.23 / design.md § Decision 12).

Storylines that no edge survived the weight/backbone filter for (degree=0) must
be pulled out of the community partition and assigned community_id = NULL, rather
than receiving a one-off Louvain singleton cluster.

Tested behaviors:
- All connected nodes → no isolated IDs.
- A node absent from every edge → reported as isolated.
- Multiple isolated nodes → all reported, original order preserved.
- Empty edge set → every node is isolated.
- Node touched by even one edge (any weight) → NOT isolated.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make scripts/ importable so we can reach _isolated_node_ids.
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from compute_communities import _isolated_node_ids  # noqa: E402


def test_all_connected_no_isolated():
    all_ids = [1, 2, 3]
    edges = [(1, 2, 0.5), (2, 3, 0.4)]
    assert _isolated_node_ids(all_ids, edges) == []


def test_single_isolated_node():
    # Node 4 appears in no edge.
    all_ids = [1, 2, 3, 4]
    edges = [(1, 2, 0.5), (2, 3, 0.4)]
    assert _isolated_node_ids(all_ids, edges) == [4]


def test_multiple_isolated_preserve_order():
    # Nodes 10 and 20 are isolated; 30 and 40 are connected.
    all_ids = [10, 30, 20, 40]
    edges = [(30, 40, 0.9)]
    assert _isolated_node_ids(all_ids, edges) == [10, 20]


def test_empty_edges_all_isolated():
    all_ids = [1, 2, 3]
    assert _isolated_node_ids(all_ids, []) == [1, 2, 3]


def test_node_with_one_edge_not_isolated():
    # Even a single low-weight edge keeps a node out of the isolated set —
    # degree=1 is still part of the graph (the lone edge is signal).
    all_ids = [1, 2]
    edges = [(1, 2, 0.01)]
    assert _isolated_node_ids(all_ids, edges) == []


def test_no_candidate_ids():
    # Defensive: empty all_ids yields empty result regardless of edges.
    assert _isolated_node_ids([], [(1, 2, 0.5)]) == []
