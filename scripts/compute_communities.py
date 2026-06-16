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

try:
    from src.llm.llm_factory import LLMFactory
    _llm_model = LLMFactory.get("t5")
    GEMINI_AVAILABLE = True
except Exception:
    GEMINI_AVAILABLE = False

from psycopg2.extras import execute_values
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


def compute_and_save_communities(
    min_weight: float = 0.05,
    resolution: float = 0.8,
    dry_run: bool = False,
    max_name: int = 60,
) -> dict:
    """
    Load edge graph from DB, run Louvain, save community_id to storylines.

    Community IDs are assigned by descending community size:
    - community 0 = largest community (most stable across runs)
    - community 1 = second largest, etc.

    Returns stats dict.
    """
    if not LOUVAIN_AVAILABLE:
        raise RuntimeError(
            "python-louvain and networkx are required. "
            "Run: pip install python-louvain networkx"
        )

    start_time = time.time()
    run_id = str(uuid.uuid4())  # shared between narrative_run_metrics + storyline_community_history (task 1.10)
    db = DatabaseManager()
    stats = {
        "nodes": 0, "edges_loaded": 0, "communities": 0, "updated": 0,
        "modularity": None,
        # Phase 1C task 1.8 — observability counters (persisted in narrative_run_metrics)
        "storylines_total": 0,
        "edges_pre_filter": 0,
        "n_singletons": 0,
        "max_community_size": 0,
        "runtime_seconds": 0.0,
        "run_id": run_id,
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

    # Build undirected weighted graph
    # Only include edges where BOTH endpoints are in the active set.
    # nx.Graph.add_edge() auto-creates missing nodes — filtering prevents
    # archived/deleted storylines from sneaking in via stale edges.
    G = nx.Graph()
    G.add_nodes_from(all_ids)
    active_ids = set(all_ids)
    for source, target, weight in edges:
        if source not in active_ids or target not in active_ids:
            continue
        # For undirected graph, keep max weight if edge already exists
        if G.has_edge(source, target):
            G[source][target]['weight'] = max(G[source][target]['weight'], weight)
        else:
            G.add_edge(source, target, weight=weight)

    # Run Louvain with fixed seed for reproducible community IDs
    partition = community_louvain.best_partition(
        G, random_state=42, weight='weight', resolution=resolution
    )

    # Compute modularity score (higher = better community structure; target > 0.4)
    modularity = community_louvain.modularity(partition, G, weight='weight')
    stats["modularity"] = round(modularity, 4)

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
        "Louvain found %d communities from %d nodes (%d edges, min_weight=%.2f, resolution=%.2f) — modularity=%.3f",
        stats["communities"], stats["nodes"], stats["edges_loaded"],
        min_weight, resolution, modularity
    )

    if dry_run:
        logger.info("[DRY RUN] Would update %d storylines with community IDs", len(partition))
        stats["updated"] = len(partition)
        return stats

    # Save to DB using a single batch UPDATE
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            execute_values(cur, """
                UPDATE storylines AS s
                SET community_id = v.cid
                FROM (VALUES %s) AS v(sid, cid)
                WHERE s.id = v.sid
            """, [(sid, cid) for sid, cid in partition.items()])

            # Null out any storyline not in partition (e.g. archived since last run)
            if partition:
                cur.execute(
                    "UPDATE storylines SET community_id = NULL "
                    "WHERE id != ALL(%s) AND community_id IS NOT NULL",
                    (list(partition.keys()),)
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
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO narrative_run_metrics (
                        run_id, pipeline_step,
                        n_storylines_total, n_storylines_active,
                        n_edges_pre_filter, n_edges_post_filter,
                        n_communities, n_singletons, max_community_size,
                        silhouette, community_coherence_med,
                        tcs, tcs_overlap_size, tcs_unreliable,
                        modularity, runtime_seconds
                    ) VALUES (
                        %s, 'community_detection',
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        stats.get("run_id"),
                        stats.get("storylines_total"),
                        stats.get("nodes"),
                        stats.get("edges_pre_filter"),
                        stats.get("edges_loaded"),
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
                    ),
                )
            conn.commit()
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
    args = parser.parse_args()

    print("=" * 60)
    print("COMMUNITY DETECTION")
    print("=" * 60)
    print(f"  Min edge weight: {args.min_weight}")
    print(f"  Resolution:      {args.resolution}")
    print(f"  Max LLM names:   {args.max_name}")
    print(f"  Dry run:         {args.dry_run}")
    print()

    try:
        stats = compute_and_save_communities(
            min_weight=args.min_weight,
            resolution=args.resolution,
            dry_run=args.dry_run,
            max_name=args.max_name,
        )
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    print(f"Storylines (nodes):  {stats['nodes']}")
    print(f"Edges loaded:        {stats['edges_loaded']}")
    print(f"Communities found:   {stats['communities']}")
    print(f"Modularity:          {stats.get('modularity', 'N/A')}")
    print(f"Storylines updated:  {stats['updated']}")
    print(f"Communities named:   {stats.get('communities_named', 'N/A (dry-run or Gemini unavailable)')}")
    if args.dry_run:
        print("\n[DRY RUN] No changes written to database.")
    print("\nDone!")


if __name__ == "__main__":
    main()
