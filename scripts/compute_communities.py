#!/usr/bin/env python3
"""
Community Detection Script

Runs Louvain community detection on the narrative storyline graph and
saves community IDs to the storylines table. Community 0 is always the
largest community (stable color assignment across nightly runs).

Usage:
    python scripts/compute_communities.py
    python scripts/compute_communities.py --min-weight 0.25
    python scripts/compute_communities.py --resolution 0.8
    python scripts/compute_communities.py --dry-run
"""

import os
import re
import sys
import time
import uuid
import argparse
from collections import Counter
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / '.env')

try:
    import networkx as nx
    import community as community_louvain  # python-louvain
    LOUVAIN_AVAILABLE = True
except ImportError:
    LOUVAIN_AVAILABLE = False

try:
    import numpy as np
    from sklearn.metrics import silhouette_score, normalized_mutual_info_score
    METRICS_AVAILABLE = True
except ImportError:
    METRICS_AVAILABLE = False

# Phase 1E (task 1.17) — Leiden + CPM for the 4-way shadow comparison framework.
# Optional dep: guarded like LOUVAIN_AVAILABLE so the script still runs (Louvain
# only) where leidenalg/igraph aren't installed. Prod container has both (Phase 0.2).
# Verify with: docker compose -p app exec backend python -c "import leidenalg, igraph"
try:
    import igraph as ig
    import leidenalg
    LEIDEN_AVAILABLE = True
except ImportError:
    LEIDEN_AVAILABLE = False

try:
    from src.llm.llm_factory import LLMFactory
    _llm_model = LLMFactory.get("t5")
    GEMINI_AVAILABLE = True
except Exception:
    GEMINI_AVAILABLE = False

from psycopg2.extras import execute_values, Json
from src.storage.database import DatabaseManager
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _name_community(cid: int, nodes_in_community: list, conn) -> str | None:
    """Call Gemini to generate a 2-4 word macro-theme label for a community.

    Returns the name string, or None if Gemini is unavailable or the call fails.
    """
    if not GEMINI_AVAILABLE:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT title FROM storylines
                WHERE id = ANY(%s) AND title IS NOT NULL
                ORDER BY momentum_score DESC NULLS LAST
                LIMIT 15
            """, (nodes_in_community,))
            titles = [row[0] for row in cur.fetchall()]

        if not titles:
            return None

        headlines_text = "\n".join(f"- {t}" for t in titles)
        prompt = (
            "You are an expert Geopolitical Analyst. I will give you a list of news headlines "
            "that form a specific intelligence cluster.\n"
            "Your task is to give a short, overarching name to this cluster.\n"
            "Rule 1: The name must be in English.\n"
            "Rule 2: It must be extremely concise (2 to 4 words maximum).\n"
            "Rule 3: Use a professional geopolitical/macro-economic tone "
            "(e.g., 'Gulf Energy Crisis', 'Red Sea Maritime Threats', 'Sino-US Tech War').\n"
            "Rule 4: Return ONLY the short name, nothing else. No markdown, no quotes.\n\n"
            f"Headlines in this cluster:\n{headlines_text}"
        )
        result = _llm_model.generate(
            prompt,
            max_tokens=20,
            temperature=0.2,
        )
        # Strip stray markdown (quotes, asterisks, etc.)
        name = re.sub(r'[*`"\'#]', '', result).strip()[:80]
        return name if name else None

    except Exception as e:
        logger.error(f"Failed to name community {cid}: {e} — skipping")
        return None


def _disparity_filter_backbone(
    edges: list[tuple], alpha: float, fallback_threshold: float
) -> tuple[list[tuple], dict]:
    """Serrano-Boguñá-Vespignani (2009) disparity filter — extracts the
    statistically significant edge backbone from a weighted graph.

    For each node i with degree k_i and strength s_i, and each incident edge
    (i,j) with weight w_ij:
        p_ij  = w_ij / s_i
        a_ij  = (1 - p_ij)^(k_i - 1)
    Edge (i,j) is kept if a_ij < alpha from EITHER endpoint (union, conservative:
    preserves significant edges between nodes of unequal degree).

    Degree-1 nodes are degenerate (formula gives a=1, edge would always be
    pruned). They are kept unconditionally — the lone edge IS the only signal.

    Fallback: if the backbone ends up empty (alpha too aggressive for the data
    distribution), the function falls back to weight >= fallback_threshold to
    guarantee Louvain has something to chew on. This is a guard, not a normal
    operating mode.

    Args:
        edges: list of (source_id, target_id, weight) tuples — assumed
               already deduplicated and weight > 0.
        alpha: significance threshold (typical 0.1 - 0.5; 0.3 = default).
        fallback_threshold: min weight if backbone is empty.

    Returns:
        (backbone_edges, stats) where stats has:
            n_input, n_backbone, n_fallback_kept, used_fallback,
            backbone_weight_p50, backbone_weight_p75
    """
    stats = {
        "n_input": len(edges),
        "n_backbone": 0,
        "n_fallback_kept": 0,
        "used_fallback": False,
        "backbone_weight_p50": None,
        "backbone_weight_p75": None,
    }
    if not edges:
        return [], stats

    # Per-node: collect (neighbor, weight) lists to compute degree + strength
    incidents: dict = {}
    for s, t, w in edges:
        incidents.setdefault(s, []).append((t, w))
        incidents.setdefault(t, []).append((s, w))

    # Pre-compute node strengths and degrees
    node_k = {n: len(adj) for n, adj in incidents.items()}
    node_s = {n: sum(w for _, w in adj) for n, adj in incidents.items()}

    # Edge survives if it passes the test from EITHER endpoint
    kept: set = set()
    for s, t, w in edges:
        key = (s, t) if s < t else (t, s)
        if key in kept:
            continue
        # From s's side
        k_s, str_s = node_k[s], node_s[s]
        if k_s <= 1:
            kept.add(key)
            continue
        p_s = w / str_s if str_s > 0 else 0.0
        a_s = (1.0 - p_s) ** (k_s - 1)
        # From t's side
        k_t, str_t = node_k[t], node_s[t]
        if k_t <= 1:
            kept.add(key)
            continue
        p_t = w / str_t if str_t > 0 else 0.0
        a_t = (1.0 - p_t) ** (k_t - 1)
        if a_s < alpha or a_t < alpha:
            kept.add(key)

    backbone = [(s, t, w) for (s, t, w) in edges
                if (s, t) in kept or (t, s) in kept]

    if not backbone:
        # Fallback: backbone empty (alpha too tight for data). Use a simple
        # weight threshold to avoid handing Louvain an empty graph.
        backbone = [(s, t, w) for (s, t, w) in edges if w >= fallback_threshold]
        stats["used_fallback"] = True
        stats["n_fallback_kept"] = len(backbone)

    stats["n_backbone"] = len(backbone)
    if backbone and METRICS_AVAILABLE:
        weights = np.array([w for _, _, w in backbone], dtype=float)
        stats["backbone_weight_p50"] = float(np.median(weights))
        stats["backbone_weight_p75"] = float(np.percentile(weights, 75))
    elif backbone:
        # Pure-Python fallback if numpy unavailable
        weights = sorted(w for _, _, w in backbone)
        n = len(weights)
        stats["backbone_weight_p50"] = weights[n // 2]
        stats["backbone_weight_p75"] = weights[(3 * n) // 4]

    return backbone, stats


def _isolated_node_ids(all_ids: list, edges: list[tuple]) -> list:
    """Return the storyline IDs in `all_ids` that have degree=0 in `edges`
    (Phase 1F task 1.23, design.md § Decision 12).

    These are storylines no edge survived the weight/backbone filter for; they
    are not part of any community structure and receive community_id = NULL
    rather than a one-off Louvain singleton cluster.

    Args:
        all_ids: candidate node IDs (all active storylines).
        edges: list of (source_id, target_id, weight) tuples actually fed to
               community detection.

    Returns:
        list of IDs from `all_ids` that appear in no edge, preserving order.
    """
    connected = set()
    for s, t, _ in edges:
        connected.add(s)
        connected.add(t)
    return [n for n in all_ids if n not in connected]


def _score_dict(
    name: str,
    partition: dict,
    n_active: int,
    modularity: float,
    sil,
    coh_med,
    runtime_ms: int,
    gamma_used=None,
    gamma_sweep_range=None,
) -> dict:
    """Assemble the unified, JSON-serializable score schema shared by the Louvain
    and Leiden scorers (Phase 1E task 1.20). Keys match the shadow_partitions JSONB
    schema in design.md § Decision 22.
    """
    freq = Counter(partition.values())
    n_comm = len(freq)
    max_size = max(freq.values()) if freq else 0
    return {
        "name": name,
        "n_edges": None,  # filled by callers that know the edge count
        "n_communities": n_comm,
        "n_singletons": sum(1 for c in freq.values() if c == 1),
        "max_community_size": max_size,
        "avg_community_size": round(n_active / n_comm, 2) if n_comm else 0.0,
        "modularity": round(modularity, 4),
        "silhouette": sil,
        "coherence_med": coh_med,
        "runtime_ms": runtime_ms,
        "gamma_used": gamma_used,
        "gamma_sweep_range": gamma_sweep_range,
    }


def _run_louvain_and_score(
    db: DatabaseManager,
    all_ids: list,
    edges: list[tuple],
    resolution: float,
    name: str = "louvain",
) -> dict:
    """Build the graph from `edges`, run Louvain, return the unified score schema
    (Phase 1E task 1.20). `gamma_used`/`gamma_sweep_range` are None (Louvain has no
    γ-sweep). Used by `--compare-sparsification` and `compute_shadow_partitions`.
    """
    t0 = time.time()
    G = nx.Graph()
    G.add_nodes_from(all_ids)
    for s, t, w in edges:
        G.add_edge(s, t, weight=w)

    partition = community_louvain.best_partition(
        G, random_state=42, weight='weight', resolution=resolution
    )
    modularity = community_louvain.modularity(partition, G, weight='weight')

    freq = Counter(partition.values())
    rank = {old_id: new_id for new_id, (old_id, _) in enumerate(freq.most_common())}
    partition = {node: rank[cid] for node, cid in partition.items()}

    sil, coh_med = _compute_quality_metrics(db, partition)
    runtime_ms = int((time.time() - t0) * 1000)

    result = _score_dict(name, partition, len(all_ids), modularity, sil, coh_med, runtime_ms)
    result["n_edges"] = len(edges)
    return result


def _run_leiden_cpm_and_score(
    db: DatabaseManager,
    all_ids: list,
    edges: list[tuple],
    cfg,
    name: str = "leiden",
) -> dict:
    """Run the Leiden+CPM adaptive γ-sweep and return the unified score schema
    (Phase 1E task 1.20). `gamma_used` and `gamma_sweep_range` are populated.
    """
    t0 = time.time()
    partition, sweep_stats, gamma_used, gamma_range = _run_leiden_cpm_adaptive_sweep(
        db, all_ids, edges, cfg
    )
    runtime_ms = int((time.time() - t0) * 1000)

    result = _score_dict(
        name,
        partition,
        len(all_ids),
        sweep_stats.get("modularity", 0.0),
        sweep_stats.get("silhouette"),
        sweep_stats.get("community_coherence_med"),
        runtime_ms,
        gamma_used=round(gamma_used, 6),
        gamma_sweep_range=[round(g, 6) for g in gamma_range],
    )
    result["n_edges"] = len(edges)
    result["gate_failed"] = sweep_stats.get("gate_failed", False)
    return result


def _build_igraph(all_ids: list, edges: list[tuple]) -> "ig.Graph":
    """Build a weighted undirected igraph.Graph from a node list + edge tuples,
    preserving the original storyline IDs as the vertex ``name`` attribute.

    igraph indexes vertices 0..n-1 internally; we map storyline_id → index so the
    partition can be translated back to storyline IDs by the callers.
    """
    idx_of = {sid: i for i, sid in enumerate(all_ids)}
    g = ig.Graph(n=len(all_ids), directed=False)
    g.vs["name"] = list(all_ids)
    ig_edges = []
    weights = []
    for s, t, w in edges:
        si, ti = idx_of.get(s), idx_of.get(t)
        if si is None or ti is None:
            continue  # endpoint not in the active node set — skip defensively
        ig_edges.append((si, ti))
        weights.append(w)
    if ig_edges:
        g.add_edges(ig_edges)
        g.es["weight"] = weights
    return g


def _run_leiden_cpm(all_ids: list, edges: list[tuple], resolution_param: float) -> dict:
    """Run Leiden + CPM on the graph defined by (all_ids, edges) and return a
    partition dict ``{storyline_id: community_id}`` — parallel to
    ``community_louvain.best_partition`` (Phase 1E task 1.18, design.md § Decision 1).

    Uses ``leidenalg.find_partition(g, CPMVertexPartition, weights='weight',
    resolution_parameter=γ, seed=42)``. seed=42 makes the partition deterministic:
    two consecutive runs on the same graph give the same partition. Community IDs
    are renumbered by descending size (largest = 0), matching the Louvain path.

    γ (resolution_parameter) is NOT dimensionless in CPM — it is an internal
    weight-density threshold, hence the adaptive per-run sweep (task 1.19).
    """
    if not LEIDEN_AVAILABLE:
        raise RuntimeError(
            "leidenalg and python-igraph are required for Leiden+CPM. "
            "Run: pip install leidenalg python-igraph"
        )

    g = _build_igraph(all_ids, edges)
    weights = g.es["weight"] if g.ecount() > 0 else None
    part = leidenalg.find_partition(
        g,
        leidenalg.CPMVertexPartition,
        weights=weights,
        resolution_parameter=resolution_param,
        seed=42,
    )

    # membership[i] = community index of vertex i → translate via vertex name.
    names = g.vs["name"]
    raw = {names[i]: cid for i, cid in enumerate(part.membership)}

    # Renumber by descending community size (largest = 0) — stable color/ID story.
    freq = Counter(raw.values())
    rank = {old: new for new, (old, _) in enumerate(freq.most_common())}
    return {node: rank[cid] for node, cid in raw.items()}


def _partition_size_stats(partition: dict, n_active: int) -> dict:
    """Community-size summary for a partition (shared by Leiden sweep + scorers)."""
    freq = Counter(partition.values())
    n_comm = len(freq)
    max_size = max(freq.values()) if freq else 0
    return {
        "n_communities": n_comm,
        "n_singletons": sum(1 for c in freq.values() if c == 1),
        "max_community_size": max_size,
        "avg_community_size": round(n_active / n_comm, 2) if n_comm else 0.0,
        "max_community_ratio": round(max_size / n_active, 4) if n_active else 0.0,
    }


def _run_leiden_cpm_adaptive_sweep(
    db: DatabaseManager, all_ids: list, edges: list[tuple], cfg
) -> tuple:
    """Adaptive γ-sweep for Leiden+CPM (Phase 1E task 1.19, design.md § Decision 1).

    γ is NOT dimensionless in CPM — it is a weight-density threshold, so the sweep
    is derived per-run from the *current* edge set's weight distribution:

        median_w     = median(edge weights)
        gamma_range  = geomspace(0.1*median_w, 2.0*median_w, 8)  clamped into the
                       community.resolution_sweep bounding box [min, max]

    For each γ it runs Leiden+CPM and scores modularity + silhouette + coherence,
    then picks the winner via the scale-invariant composite gate:
        avg_community_size ∈ [80, 240]  AND  max_size_ratio ≤ 0.20  AND  coherence ≥ 0.45
    If no γ passes the gate, returns the partition with the highest coherence_med
    and flags ``gate_failed=True``.

    Returns:
        (partition, partition_stats, gamma_used, gamma_sweep_range)
        - partition: {storyline_id: community_id} of the winner
        - partition_stats: dict (size stats + modularity/silhouette/coherence + gate_failed)
        - gamma_used: the γ that produced the winner
        - gamma_sweep_range: list[float] of the 8 γ values tried (for audit)
    """
    if not LEIDEN_AVAILABLE:
        raise RuntimeError("leidenalg/python-igraph required for the Leiden γ-sweep")
    if not METRICS_AVAILABLE:
        raise RuntimeError("numpy required for the Leiden γ-sweep")

    n_active = len(all_ids)
    weights = np.array([w for _, _, w in edges], dtype=float)
    if weights.size == 0:
        # No edges → single γ at the config centerpoint, degenerate partition.
        gamma = float(cfg.community.resolution_parameter)
        partition = _run_leiden_cpm(all_ids, edges, gamma)
        stats = _partition_size_stats(partition, n_active)
        stats.update({"modularity": 0.0, "silhouette": None,
                      "community_coherence_med": None, "gate_failed": True})
        return partition, stats, gamma, [gamma]

    median_w = float(np.median(weights))
    lo_box = min(cfg.community.resolution_sweep)
    hi_box = max(cfg.community.resolution_sweep)
    lo = float(np.clip(0.1 * median_w, lo_box, hi_box))
    hi = float(np.clip(2.0 * median_w, lo_box, hi_box))
    if hi <= lo:  # degenerate clamp (median outside box) — fall back to the box
        lo, hi = lo_box, hi_box
    gamma_range = [float(g) for g in np.geomspace(lo, hi, num=8)]

    g_ig = _build_igraph(all_ids, edges)
    ig_weights = g_ig.es["weight"] if g_ig.ecount() > 0 else None

    qg = cfg.community.quality_gate
    candidates = []  # (passes_gate, coherence_sort_key, gamma, partition, stats)
    for gamma in gamma_range:
        partition = _run_leiden_cpm(all_ids, edges, gamma)
        size_stats = _partition_size_stats(partition, n_active)
        sil, coh_med = _compute_quality_metrics(db, partition)
        # Newman modularity (comparable to the Louvain path) on the igraph.
        membership = [partition[name] for name in g_ig.vs["name"]]
        try:
            modularity = float(g_ig.modularity(membership, weights=ig_weights))
        except Exception:
            modularity = 0.0

        stats = {**size_stats, "modularity": round(modularity, 4),
                 "silhouette": sil, "community_coherence_med": coh_med}

        # Decision 22 composite gate: avg_community_size ∈ [80, 240] and
        # max_size_ratio ≤ 0.20 are the ex-ante shadow-observation constants
        # (NOT the tunable applied quality_gate); coherence floor is from config.
        passes = (
            80 <= size_stats["avg_community_size"] <= 240
            and size_stats["max_community_ratio"] <= 0.20
            and coh_med is not None and coh_med >= qg.coherence_median_min
        )
        coh_key = coh_med if coh_med is not None else -1.0
        candidates.append((passes, coh_key, gamma, partition, stats))

    passing = [c for c in candidates if c[0]]
    if passing:
        # Among gate-passers, prefer the highest coherence.
        _, _, gamma_used, partition, stats = max(passing, key=lambda c: c[1])
        stats["gate_failed"] = False
    else:
        # Nobody passed — fall back to highest coherence, flag the failure.
        _, _, gamma_used, partition, stats = max(candidates, key=lambda c: c[1])
        stats["gate_failed"] = True
        logger.warning(
            "Leiden γ-sweep: no γ passed the composite gate (tried %s) — "
            "falling back to highest-coherence γ=%.5f",
            [round(g, 5) for g in gamma_range], gamma_used,
        )

    return partition, stats, gamma_used, gamma_range


def compute_shadow_partitions(
    db: DatabaseManager,
    all_ids: list,
    active_edges: list[tuple],
    backbone_edges: list[tuple],
    cfg,
) -> list:
    """Compute the 4-way shadow comparison partitions (Phase 1E task 1.21,
    design.md § Decision 22): Louvain-full, Louvain-backbone, Leiden-full,
    Leiden-backbone. Returns a list of unified score dicts (JSON-serializable),
    one per partition.

    These are pure metrics — only ``louvain_full`` is the partition actually
    applied to ``storylines.community_id`` by the caller. Leiden is skipped (with
    a logged warning) when leidenalg/igraph are unavailable, so the framework
    degrades gracefully to the 2 Louvain partitions.
    """
    resolution = cfg.community.resolution
    results = []

    results.append(_run_louvain_and_score(db, all_ids, active_edges, resolution, name="louvain_full"))
    results.append(_run_louvain_and_score(db, all_ids, backbone_edges, resolution, name="louvain_backbone"))

    if LEIDEN_AVAILABLE and METRICS_AVAILABLE:
        try:
            results.append(_run_leiden_cpm_and_score(db, all_ids, active_edges, cfg, name="leiden_full"))
            results.append(_run_leiden_cpm_and_score(db, all_ids, backbone_edges, cfg, name="leiden_backbone"))
        except Exception as e:
            logger.warning("Leiden shadow partitions failed (%s) — keeping Louvain-only shadow", e)
    else:
        logger.warning(
            "leidenalg/igraph (or numpy) unavailable — shadow comparison runs Louvain-only "
            "(2 partitions instead of 4). Install deps for the full 4-way framework."
        )

    logger.info(
        "Shadow partitions (%d): %s",
        len(results),
        ", ".join(f"{r['name']}(n={r['n_communities']}, coh={r['coherence_med']})" for r in results),
    )
    return results


def compare_sparsification(
    min_weight: float = 0.05,
    resolution: float = 0.8,
) -> dict:
    """Run Louvain twice — on the full active edge set and on the disparity-filter
    backbone — and print a side-by-side comparison. NO DB WRITES.

    This is a diagnostic tool to validate the impact of Phase 1D
    `--apply-sparsification` on production data before promoting it as default.
    """
    if not LOUVAIN_AVAILABLE:
        raise RuntimeError(
            "python-louvain and networkx are required. "
            "Run: pip install python-louvain networkx"
        )

    from src.nlp.config import load_clustering_config
    cfg = load_clustering_config()
    db = DatabaseManager()

    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT source_story_id, target_story_id, weight "
                "FROM storyline_edges WHERE weight >= %s",
                (min_weight,),
            )
            raw_edges = cur.fetchall()

            cur.execute(
                "SELECT id FROM storylines "
                "WHERE narrative_status IN ('emerging', 'active', 'stabilized')"
            )
            all_ids = [row[0] for row in cur.fetchall()]

    if not raw_edges:
        logger.warning("No edges loaded (min_weight=%.2f). Nothing to compare.", min_weight)
        return {}

    active_ids = set(all_ids)
    edge_max: dict = {}
    for s, t, w in raw_edges:
        if s not in active_ids or t not in active_ids:
            continue
        key = (s, t) if s < t else (t, s)
        prev = edge_max.get(key)
        if prev is None or w > prev:
            edge_max[key] = w
    active_edges = [(s, t, w) for (s, t), w in edge_max.items()]

    cfg_sp = cfg.sparsification
    backbone_edges, backbone_stats = _disparity_filter_backbone(
        active_edges, alpha=cfg_sp.alpha, fallback_threshold=cfg_sp.fallback_threshold,
    )

    logger.info(
        "Disparity filter: %d → %d edges (%.1f%% kept, p50=%s, p75=%s, fallback=%s)",
        backbone_stats["n_input"], backbone_stats["n_backbone"],
        100.0 * backbone_stats["n_backbone"] / max(backbone_stats["n_input"], 1),
        f"{backbone_stats['backbone_weight_p50']:.3f}" if backbone_stats["backbone_weight_p50"] is not None else "n/a",
        f"{backbone_stats['backbone_weight_p75']:.3f}" if backbone_stats["backbone_weight_p75"] is not None else "n/a",
        backbone_stats["used_fallback"],
    )

    logger.info("Running Louvain on FULL graph (%d edges)...", len(active_edges))
    full_result = _run_louvain_and_score(db, all_ids, active_edges, resolution)

    logger.info("Running Louvain on BACKBONE graph (%d edges)...", len(backbone_edges))
    backbone_result = _run_louvain_and_score(db, all_ids, backbone_edges, resolution)

    def _fmt(v):
        if v is None:
            return "n/a"
        if isinstance(v, float):
            return f"{v:.4f}"
        return str(v)

    def _delta(full, backbone):
        if full is None or backbone is None:
            return "n/a"
        if isinstance(full, float) or isinstance(backbone, float):
            d = backbone - full
            sign = "+" if d >= 0 else ""
            return f"{sign}{d:.4f}"
        d = backbone - full
        sign = "+" if d >= 0 else ""
        return f"{sign}{d}"

    print("=" * 78)
    print("SPARSIFICATION COMPARISON — Louvain on FULL vs BACKBONE (dry-run, no DB writes)")
    print("=" * 78)
    print(f"  Nodes (active storylines):  {len(all_ids)}")
    print(f"  min_weight:                 {min_weight}")
    print(f"  resolution:                 {resolution}")
    print(f"  Disparity alpha:            {cfg_sp.alpha}")
    print(f"  Backbone fallback used:     {backbone_stats['used_fallback']}")
    print(f"  Backbone weight p50/p75:    {_fmt(backbone_stats['backbone_weight_p50'])}"
          f" / {_fmt(backbone_stats['backbone_weight_p75'])}")
    print()
    print(f"  {'Metric':<32} {'FULL':>14} {'BACKBONE':>14} {'Δ':>14}")
    print(f"  {'-'*32} {'-'*14} {'-'*14} {'-'*14}")
    metrics = [
        "n_edges", "n_communities", "n_singletons", "max_community_size",
        "avg_community_size", "modularity", "silhouette", "coherence_med",
    ]
    for m in metrics:
        f = full_result.get(m)
        b = backbone_result.get(m)
        print(f"  {m:<32} {_fmt(f):>14} {_fmt(b):>14} {_delta(f, b):>14}")
    print("=" * 78)

    return {
        "full": full_result,
        "backbone": backbone_result,
        "backbone_stats": backbone_stats,
    }


def compute_and_save_communities(
    min_weight: float = 0.05,
    resolution: float = 0.8,
    dry_run: bool = False,
    max_name: int = 60,
    apply_sparsification: bool = False,
) -> dict:
    """
    Load edge graph from DB, run Louvain, save community_id to storylines.

    Community IDs are assigned by descending community size:
    - community 0 = largest community (most stable across runs)
    - community 1 = second largest, etc.

    apply_sparsification: when True, runs Louvain on the disparity-filter
    backbone instead of the raw min_weight-filtered edge set (Phase 1D
    promotion). Default False = shadow mode (compute backbone metrics, but
    Louvain still uses the full graph — zero behavior change).

    Returns stats dict.
    """
    if not LOUVAIN_AVAILABLE:
        raise RuntimeError(
            "python-louvain and networkx are required. "
            "Run: pip install python-louvain networkx"
        )

    start_time = time.time()
    run_id = str(uuid.uuid4())  # shared between narrative_run_metrics + storyline_community_history (task 1.10)
    # Local import avoids module-load coupling to the config (and keeps
    # compute_and_save_communities callable from tests with monkeypatched cfg).
    from src.nlp.config import load_clustering_config
    cfg = load_clustering_config()
    db = DatabaseManager()
    stats = {
        "nodes": 0, "edges_loaded": 0, "communities": 0, "updated": 0,
        "modularity": None,
        # Phase 1C task 1.8 — observability counters (persisted in narrative_run_metrics)
        "storylines_total": 0,
        "edges_pre_filter": 0,
        "n_singletons": 0,
        "n_isolated": 0,  # Phase 1F task 1.23 — degree=0 storylines → community_id NULL
        "max_community_size": 0,
        "runtime_seconds": 0.0,
        "run_id": run_id,
        # Phase 1D — disparity filter backbone stats (always computed in shadow)
        "n_edges_post_filter": None,
        "backbone_weight_p50": None,
        "backbone_weight_p75": None,
        "sparsification_used_fallback": False,
        "sparsification_applied": apply_sparsification,
        # Phase 1E task 1.21 — 4-way shadow comparison partitions (JSONB)
        "shadow_partitions": None,
    }

    # Load edges from DB
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            # Pre-filter edge count for observability (Phase 1C task 1.8)
            cur.execute("SELECT count(*) FROM storyline_edges")
            stats["edges_pre_filter"] = cur.fetchone()[0]

            cur.execute("""
                SELECT source_story_id, target_story_id, weight
                FROM storyline_edges
                WHERE weight >= %s
            """, (min_weight,))
            edges = cur.fetchall()
            # edges_loaded is updated after the active-only / dedup pass below
            # so the number matches the graph Louvain actually sees.
            stats["edges_loaded"] = len(edges)

            # Also load all active storyline IDs (include isolated nodes)
            cur.execute("""
                SELECT id FROM storylines
                WHERE narrative_status IN ('emerging', 'active', 'stabilized')
            """)
            all_ids = [row[0] for row in cur.fetchall()]
            stats["nodes"] = len(all_ids)

            # Total storylines across all statuses (Phase 1C task 1.8)
            cur.execute("SELECT count(*) FROM storylines")
            stats["storylines_total"] = cur.fetchone()[0]

    if not edges:
        logger.warning("No edges loaded (min_weight=%.2f). Skipping community detection.", min_weight)
        stats["runtime_seconds"] = round(time.time() - start_time, 3)
        _persist_run_metrics(db, stats, dry_run)
        return stats

    # Restrict edges to active endpoints (also dedup on undirected key, keeping
    # max weight). Done BEFORE disparity filter so backbone stats reflect the
    # actual graph Louvain will see — not the raw storyline_edges table.
    active_ids = set(all_ids)
    edge_max: dict = {}
    for source, target, weight in edges:
        if source not in active_ids or target not in active_ids:
            continue
        key = (source, target) if source < target else (target, source)
        prev = edge_max.get(key)
        if prev is None or weight > prev:
            edge_max[key] = weight
    active_edges = [(s, t, w) for (s, t), w in edge_max.items()]
    # Refresh edges_loaded so observability numbers refer to the actual graph
    # Louvain sees (post min_weight + post active-only dedup), not raw rows.
    stats["edges_loaded"] = len(active_edges)

    # Phase 1D — disparity filter (Serrano-Boguñá-Vespignani 2009)
    # Always computed in shadow; only used by Louvain when apply_sparsification.
    cfg_sp = cfg.sparsification
    backbone_edges, backbone_stats = _disparity_filter_backbone(
        active_edges, alpha=cfg_sp.alpha, fallback_threshold=cfg_sp.fallback_threshold,
    )
    stats["n_edges_post_filter"] = backbone_stats["n_backbone"]
    stats["backbone_weight_p50"] = backbone_stats["backbone_weight_p50"]
    stats["backbone_weight_p75"] = backbone_stats["backbone_weight_p75"]
    stats["sparsification_used_fallback"] = backbone_stats["used_fallback"]
    logger.info(
        "Disparity filter: %d → %d edges (%.1f%% kept, p50=%s, p75=%s, fallback=%s, mode=%s)",
        backbone_stats["n_input"], backbone_stats["n_backbone"],
        100.0 * backbone_stats["n_backbone"] / max(backbone_stats["n_input"], 1),
        f"{stats['backbone_weight_p50']:.3f}" if stats["backbone_weight_p50"] is not None else "n/a",
        f"{stats['backbone_weight_p75']:.3f}" if stats["backbone_weight_p75"] is not None else "n/a",
        backbone_stats["used_fallback"],
        "APPLIED" if apply_sparsification else "shadow",
    )

    # Phase 1E task 1.21 — 4-way shadow comparison (design.md § Decision 22).
    # Computes Louvain/Leiden × full/backbone partitions as pure metrics and
    # persists them to narrative_run_metrics.shadow_partitions JSONB. Does NOT
    # touch community_id (that's the applied Louvain partition below). Skipped on
    # dry_run and when the framework is disabled in config.
    if cfg.community.shadow_comparison.enabled and not dry_run:
        try:
            stats["shadow_partitions"] = compute_shadow_partitions(
                db, all_ids, active_edges, backbone_edges, cfg
            )
        except Exception as e:
            logger.warning("Shadow comparison failed (%s) — continuing without it", e)
            stats["shadow_partitions"] = None

    # Build undirected weighted graph. In shadow mode (default) Louvain uses the
    # full active_edges set — disparity backbone is observed but not enforced,
    # so behavior is unchanged from Phase 1C baseline.
    G = nx.Graph()
    G.add_nodes_from(all_ids)
    edges_for_louvain = backbone_edges if apply_sparsification else active_edges
    for source, target, weight in edges_for_louvain:
        G.add_edge(source, target, weight=weight)

    # Run Louvain with fixed seed for reproducible community IDs
    partition = community_louvain.best_partition(
        G, random_state=42, weight='weight', resolution=resolution
    )

    # Compute modularity score (higher = better community structure; target > 0.4)
    # Computed on the full partition (incl. isolated nodes) — Louvain's own metric.
    modularity = community_louvain.modularity(partition, G, weight='weight')
    stats["modularity"] = round(modularity, 4)

    # Phase 1F task 1.23 — singleton isolation (design.md § Decision 12).
    # Storylines with degree=0 (no edge survived min_weight/backbone) are not part
    # of any community structure; Louvain assigns each its own singleton cluster.
    # We pull them OUT of the partition so they get community_id = NULL (set below),
    # instead of polluting community_id with one-off cluster numbers and inflating
    # the n_singletons / coherence metrics with non-community noise.
    isolated_ids = _isolated_node_ids(all_ids, edges_for_louvain)
    if isolated_ids:
        for n in isolated_ids:
            partition.pop(n, None)
        logger.info(
            "Singleton isolation: %d storylines with degree=0 → community_id = NULL",
            len(isolated_ids),
        )
    stats["n_isolated"] = len(isolated_ids)

    # Renumber: community with most members = 0, then descending by size
    freq = Counter(partition.values())
    rank = {old_id: new_id for new_id, (old_id, _) in enumerate(freq.most_common())}
    partition = {node: rank[cid] for node, cid in partition.items()}

    stats["communities"] = len(freq)
    # Phase 1C task 1.8 — community size distribution observability
    stats["n_singletons"] = sum(1 for cnt in freq.values() if cnt == 1)
    stats["max_community_size"] = max(freq.values()) if freq else 0

    # Phase 1C task 1.9 — silhouette + community_coherence_med
    sil, coh_med = _compute_quality_metrics(db, partition)
    stats["silhouette"] = sil
    stats["community_coherence_med"] = coh_med

    # Phase 1C task 1.11 — TCS NMI on intersection with previous run
    tcs, overlap_size, unreliable = _compute_tcs_on_intersection(db, run_id, partition)
    stats["tcs"] = tcs
    stats["tcs_overlap_size"] = overlap_size
    stats["tcs_unreliable"] = unreliable
    # EPR deferred: requires per-run edge snapshot (not in scope for Phase 1C —
    # storyline_edges is cumulative, no historical state to diff against).
    logger.info(
        "Louvain found %d communities from %d nodes (%d edges [%s], min_weight=%.2f, resolution=%.2f) — modularity=%.3f",
        stats["communities"], stats["nodes"], len(edges_for_louvain),
        "backbone" if apply_sparsification else "full",
        min_weight, resolution, modularity
    )

    if dry_run:
        logger.info("[DRY RUN] Would update %d storylines with community IDs", len(partition))
        stats["updated"] = len(partition)
        return stats

    # Save to DB using a single batch UPDATE
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            if partition:
                execute_values(cur, """
                    UPDATE storylines AS s
                    SET community_id = v.cid
                    FROM (VALUES %s) AS v(sid, cid)
                    WHERE s.id = v.sid
                """, [(sid, cid) for sid, cid in partition.items()])

                # Null out any storyline not in partition: archived since last run,
                # OR isolated degree=0 storylines pulled out above (Phase 1F task 1.23).
                cur.execute(
                    "UPDATE storylines SET community_id = NULL "
                    "WHERE id != ALL(%s) AND community_id IS NOT NULL",
                    (list(partition.keys()),)
                )
            else:
                # Degenerate run: every node was isolated (or no communities). Clear
                # all stale community_ids so nothing carries over from a prior run.
                cur.execute(
                    "UPDATE storylines SET community_id = NULL "
                    "WHERE community_id IS NOT NULL"
                )
        conn.commit()

    stats["updated"] = len(partition)
    logger.info("Saved community IDs for %d storylines", stats["updated"])

    # Phase 1C task 1.10 — partition snapshot for TCS/EPR/Hungarian lineage
    _persist_partition_history(db, run_id, partition)

    # Generate LLM community names (one call per community, resilient to failures)
    if GEMINI_AVAILABLE:
        community_nodes: dict[int, list] = {}
        for node, cid in partition.items():
            community_nodes.setdefault(cid, []).append(node)

        # Name only the largest max_name communities — singletons already skipped below
        communities_to_name = sorted(
            community_nodes.keys(), key=lambda c: len(community_nodes[c]), reverse=True
        )[:max_name]
        logger.info(
            "Generating LLM community names (%d/%d communities, max_name=%d)...",
            len(communities_to_name), len(freq), max_name,
        )

        with db.get_connection() as conn:
            named = 0
            for cid in communities_to_name:
                nodes = community_nodes[cid]
                if len(nodes) < 2:
                    # Skip singletons — no meaningful macro-theme from a single storyline
                    continue
                name = _name_community(cid, nodes, conn)
                if name:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE storylines SET community_name = %s WHERE id = ANY(%s)",
                            (name, nodes),
                        )
                    conn.commit()
                    logger.info("  Community %d (%d nodes) → '%s'", cid, len(nodes), name)
                    named += 1
                time.sleep(1.5)  # respect Gemini rate limits
        logger.info("Named %d/%d communities", named, len(freq))
        stats["communities_named"] = named
    else:
        logger.warning("GEMINI_API_KEY not set — community_name not generated")

    # Phase 1C task 1.8 — persist run metrics (final step before return)
    stats["runtime_seconds"] = round(time.time() - start_time, 3)
    _persist_run_metrics(db, stats, dry_run)

    return stats


def _compute_tcs_on_intersection(db: DatabaseManager, current_run_id: str, partition: dict) -> tuple:
    """Compute Temporal Cluster Stability via NMI on the storyline intersection
    between the current partition and the most recent prior partition (Phase 1C
    task 1.11).

    Returns (tcs, overlap_size, unreliable):
        - tcs: float in [0, 1] (NMI), or None when overlap < 30 (statistically
          meaningless) or sklearn missing or first run (no history).
        - overlap_size: |intersection of storyline IDs|.
        - unreliable: True when 0 < overlap_size < 50 (NMI computable but noisy).
    """
    if not METRICS_AVAILABLE or not partition:
        return None, 0, False

    # Most recent previous run (any pipeline_step other than the current run)
    prev_rows = []
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT storyline_id, community_id
                    FROM storyline_community_history
                    WHERE run_id = (
                        SELECT run_id FROM storyline_community_history
                        WHERE run_id != %s
                        ORDER BY ts DESC
                        LIMIT 1
                    )
                    """,
                    (current_run_id,),
                )
                prev_rows = cur.fetchall()
    except Exception as e:
        msg = str(e).lower()
        if "storyline_community_history" in msg and ("does not exist" in msg or "undefinedtable" in msg):
            logger.debug("storyline_community_history missing — skipping TCS (apply migration 043)")
            return None, 0, False
        logger.warning("TCS prev-partition query failed: %s", e)
        return None, 0, False

    if not prev_rows:
        logger.info("TCS: no previous partition history — first run, skipping")
        return None, 0, False

    prev_partition = {sid: cid for sid, cid in prev_rows}
    common_ids = set(partition.keys()) & set(prev_partition.keys())
    overlap_size = len(common_ids)

    if overlap_size < 30:
        logger.info("TCS: overlap=%d < 30 → unreliable, returning NULL", overlap_size)
        return None, overlap_size, True

    labels_curr = [partition[sid] for sid in common_ids]
    labels_prev = [prev_partition[sid] for sid in common_ids]
    try:
        tcs = float(normalized_mutual_info_score(labels_prev, labels_curr))
    except Exception as e:
        logger.warning("normalized_mutual_info_score failed: %s", e)
        return None, overlap_size, True

    unreliable = overlap_size < 50
    logger.info(
        "TCS=%.4f (overlap=%d storylines, unreliable=%s)",
        tcs, overlap_size, unreliable,
    )
    return tcs, overlap_size, unreliable


def _persist_partition_history(db: DatabaseManager, run_id: str, partition: dict) -> None:
    """Snapshot the partition into storyline_community_history (Phase 1C task 1.10).

    One row per (run_id, storyline_id). Used by TCS intersection (task 1.11) and
    Hungarian cross-run matching (Phase 4A). Silent no-op when table missing.
    """
    if not partition:
        return
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO storyline_community_history (run_id, storyline_id, community_id)
                    VALUES %s
                    ON CONFLICT (run_id, storyline_id) DO NOTHING
                    """,
                    [(run_id, sid, cid) for sid, cid in partition.items()],
                )
            conn.commit()
        logger.info("Persisted partition history: %d rows (run_id=%s)", len(partition), run_id)
    except Exception as e:
        msg = str(e).lower()
        if "storyline_community_history" in msg and ("does not exist" in msg or "undefinedtable" in msg):
            logger.debug(
                "storyline_community_history missing — skipping snapshot "
                "(apply migration 043 to enable)"
            )
        else:
            logger.warning("Failed to persist partition history: %s", e)


def _vec_to_array(value):
    """Convert pgvector return value to numpy array (handles list / pgvector type / None)."""
    if value is None:
        return None
    if hasattr(value, 'tolist'):
        value = value.tolist()
    elif not isinstance(value, list):
        try:
            value = list(value)
        except TypeError:
            return None
    return np.asarray(value, dtype=np.float32) if value else None


def _compute_quality_metrics(db: DatabaseManager, partition: dict) -> tuple:
    """Return (silhouette, community_coherence_med) — Phase 1C task 1.9.

    silhouette: sklearn silhouette_score on current_embedding, cosine metric.
                None when sklearn missing, <2 communities, or <(k+1) samples.
    community_coherence_med: median of per-community medians of pairwise cosine
                similarity on summary_vector. None when <2 communities qualify
                (need ≥2 members with summary_vector each).
    """
    if not METRICS_AVAILABLE or not partition:
        return None, None

    storyline_ids = list(partition.keys())
    rows = []
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, current_embedding, summary_vector "
                "FROM storylines WHERE id = ANY(%s)",
                (storyline_ids,),
            )
            rows = cur.fetchall()

    current_by_id = {}
    summary_by_id = {}
    for sid, cur_emb, sum_emb in rows:
        ca = _vec_to_array(cur_emb)
        sa = _vec_to_array(sum_emb)
        if ca is not None:
            current_by_id[sid] = ca
        if sa is not None:
            summary_by_id[sid] = sa

    # ---- silhouette ----
    silhouette = None
    if len(current_by_id) >= 3:
        emb_ids = [sid for sid in storyline_ids if sid in current_by_id]
        labels = [partition[sid] for sid in emb_ids]
        distinct_labels = set(labels)
        # silhouette requires 2 <= n_labels < n_samples
        if 2 <= len(distinct_labels) < len(emb_ids):
            X = np.stack([current_by_id[sid] for sid in emb_ids])
            try:
                silhouette = float(silhouette_score(X, labels, metric='cosine'))
            except Exception as e:
                logger.debug("silhouette_score failed: %s", e)

    # ---- community_coherence_med ----
    community_members = {}
    for sid, cid in partition.items():
        if sid in summary_by_id:
            community_members.setdefault(cid, []).append(summary_by_id[sid])

    per_community_medians = []
    for cid, embs in community_members.items():
        if len(embs) < 2:
            continue
        M = np.stack(embs)
        # Cosine similarity matrix (manual to avoid extra sklearn dependency depth)
        norms = np.linalg.norm(M, axis=1, keepdims=True)
        norms[norms == 0] = 1e-10
        Mn = M / norms
        sim = Mn @ Mn.T
        # Upper triangle (i < j) — pairwise similarities only
        iu = np.triu_indices(len(embs), k=1)
        sims = sim[iu]
        if sims.size:
            per_community_medians.append(float(np.median(sims)))

    coherence_med = float(np.median(per_community_medians)) if per_community_medians else None

    if silhouette is not None:
        logger.info("silhouette=%.4f (cosine, %d storylines)", silhouette, len(emb_ids))
    if coherence_med is not None:
        logger.info(
            "community_coherence_med=%.4f (median of %d community medians)",
            coherence_med, len(per_community_medians),
        )

    return silhouette, coherence_med


def _persist_run_metrics(db: DatabaseManager, stats: dict, dry_run: bool) -> None:
    """Insert one row into narrative_run_metrics for the current run.

    Silent no-op when:
    - dry_run=True (diagnostic runs should not pollute metrics history)
    - the table doesn't exist yet (migration 043 not applied — matches the
      `log_oracle_query` pattern in DatabaseManager)
    Any other error is logged as warning but does NOT fail the pipeline.
    """
    if dry_run:
        return

    base_cols = [
        "run_id", "pipeline_step",
        "n_storylines_total", "n_storylines_active",
        "n_edges_pre_filter", "n_edges_post_filter",
        "n_communities", "n_singletons", "max_community_size",
        "silhouette", "community_coherence_med",
        "tcs", "tcs_overlap_size", "tcs_unreliable",
        "modularity", "runtime_seconds",
        "backbone_weight_p50", "backbone_weight_p75",
    ]
    base_vals = [
        stats.get("run_id"), "community_detection",
        stats.get("storylines_total"),
        stats.get("nodes"),
        stats.get("edges_pre_filter"),
        # Phase 1D: n_edges_post_filter = disparity backbone size
        # (was: dedup edge count; that's now reflected in edges_loaded).
        stats.get("n_edges_post_filter"),
        stats.get("communities"),
        stats.get("n_singletons"),
        stats.get("max_community_size"),
        stats.get("silhouette"),
        stats.get("community_coherence_med"),
        stats.get("tcs"),
        stats.get("tcs_overlap_size"),
        stats.get("tcs_unreliable"),
        stats.get("modularity"),
        stats.get("runtime_seconds"),
        stats.get("backbone_weight_p50"),
        stats.get("backbone_weight_p75"),
    ]

    def _insert(cols, vals):
        placeholders = ", ".join(["%s"] * len(cols))
        sql = (
            f"INSERT INTO narrative_run_metrics ({', '.join(cols)}) "
            f"VALUES ({placeholders})"
        )
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, vals)
            conn.commit()

    # Phase 1E task 1.21 — include shadow_partitions JSONB when present. If the
    # column is missing (migration 046 not applied yet), fall back to the base
    # column set so we never lose the rest of the run metrics.
    shadow = stats.get("shadow_partitions")
    try:
        if shadow is not None:
            try:
                _insert(base_cols + ["shadow_partitions"], base_vals + [Json(shadow)])
            except Exception as e:
                msg = str(e).lower()
                if "shadow_partitions" in msg and ("does not exist" in msg or "undefinedcolumn" in msg):
                    logger.debug("shadow_partitions column missing — persisting metrics without it (apply migration 046)")
                    _insert(base_cols, base_vals)
                else:
                    raise
        else:
            _insert(base_cols, base_vals)
        logger.info("Persisted run metrics to narrative_run_metrics")
    except Exception as e:
        msg = str(e).lower()
        if "narrative_run_metrics" in msg and ("does not exist" in msg or "undefinedtable" in msg):
            logger.debug(
                "narrative_run_metrics table missing — skipping metrics persistence "
                "(apply migration 043 to enable)"
            )
        else:
            logger.warning("Failed to persist run metrics: %s", e)


def main():
    # Defaults are loaded from config/narrative_clustering.yaml; CLI flags override.
    from src.nlp.config import load_clustering_config
    cfg = load_clustering_config()
    cfg_min_weight = cfg.community.min_weight
    cfg_resolution = cfg.community.resolution

    parser = argparse.ArgumentParser(description="Compute Louvain communities on narrative graph")
    parser.add_argument(
        "--min-weight", type=float, default=cfg_min_weight,
        help=f"Min edge weight to include in community graph (default from config: {cfg_min_weight})"
    )
    parser.add_argument(
        "--resolution", type=float, default=cfg_resolution,
        help=f"Louvain resolution: lower = larger communities (default from config: {cfg_resolution})"
    )
    parser.add_argument(
        "--max-name", type=int, default=60,
        help="Max number of communities to name with LLM (largest first, default: 60)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Compute communities but do not write to DB"
    )
    parser.add_argument(
        "--apply-sparsification", action="store_true",
        help=(
            "Phase 1D: run Louvain on the disparity-filter backbone instead of "
            "the full edge set. Default = shadow mode (backbone stats are still "
            "computed and persisted, but Louvain uses the full graph)."
        ),
    )
    parser.add_argument(
        "--compare-sparsification", action="store_true",
        help=(
            "Diagnostic: run Louvain twice (full graph + disparity backbone) "
            "and print a side-by-side comparison of partition quality. NO DB "
            "WRITES. Implies --dry-run. Used to validate the impact of "
            "--apply-sparsification on production data before promoting it."
        ),
    )
    args = parser.parse_args()

    if args.compare_sparsification:
        print("=" * 60)
        print("SPARSIFICATION COMPARE MODE (dry-run, no DB writes)")
        print("=" * 60)
        print(f"  Min edge weight:        {args.min_weight}")
        print(f"  Resolution:             {args.resolution}")
        print()
        try:
            compare_sparsification(
                min_weight=args.min_weight,
                resolution=args.resolution,
            )
        except RuntimeError as e:
            print(f"ERROR: {e}")
            sys.exit(1)
        print("\nDone (no DB writes).")
        return

    print("=" * 60)
    print("COMMUNITY DETECTION")
    print("=" * 60)
    print(f"  Min edge weight:        {args.min_weight}")
    print(f"  Resolution:             {args.resolution}")
    print(f"  Max LLM names:          {args.max_name}")
    print(f"  Dry run:                {args.dry_run}")
    print(f"  Apply sparsification:   {args.apply_sparsification} (False = shadow)")
    print()

    try:
        stats = compute_and_save_communities(
            min_weight=args.min_weight,
            resolution=args.resolution,
            dry_run=args.dry_run,
            max_name=args.max_name,
            apply_sparsification=args.apply_sparsification,
        )
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    print(f"Storylines (nodes):     {stats['nodes']}")
    print(f"Edges (active+dedup):   {stats['edges_loaded']}")
    print(f"Edges (backbone):       {stats.get('n_edges_post_filter', 'N/A')}"
          f" (p50={stats.get('backbone_weight_p50')}, p75={stats.get('backbone_weight_p75')}, "
          f"fallback={stats.get('sparsification_used_fallback')})")
    print(f"Communities found:      {stats['communities']}")
    print(f"Isolated (degree=0):    {stats.get('n_isolated', 0)} → community_id NULL")
    print(f"Modularity:             {stats.get('modularity', 'N/A')}")
    print(f"Storylines updated:     {stats['updated']}")
    print(f"Communities named:      {stats.get('communities_named', 'N/A (dry-run or Gemini unavailable)')}")
    if args.dry_run:
        print("\n[DRY RUN] No changes written to database.")
    print("\nDone!")


if __name__ == "__main__":
    main()
