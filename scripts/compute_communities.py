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

import sys
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

from psycopg2.extras import execute_values
from src.storage.database import DatabaseManager
from src.utils.logger import get_logger

logger = get_logger(__name__)


def compute_and_save_communities(
    min_weight: float = 0.25,
    resolution: float = 0.8,
    dry_run: bool = False,
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

    db = DatabaseManager()
    stats = {"nodes": 0, "edges_loaded": 0, "communities": 0, "updated": 0, "modularity": None}

    # Load edges from DB
    with db.get_connection() as conn:
        with conn.cursor() as cur:
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

    if not edges:
        logger.warning("No edges loaded (min_weight=%.2f). Skipping community detection.", min_weight)
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
    return stats


def main():
    parser = argparse.ArgumentParser(description="Compute Louvain communities on narrative graph")
    parser.add_argument(
        "--min-weight", type=float, default=0.25,
        help="Min edge weight to include in community graph (default: 0.25)"
    )
    parser.add_argument(
        "--resolution", type=float, default=0.8,
        help="Louvain resolution: lower = larger communities (default: 0.8)"
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
    print(f"  Dry run:         {args.dry_run}")
    print()

    try:
        stats = compute_and_save_communities(
            min_weight=args.min_weight,
            resolution=args.resolution,
            dry_run=args.dry_run,
        )
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    print(f"Storylines (nodes):  {stats['nodes']}")
    print(f"Edges loaded:        {stats['edges_loaded']}")
    print(f"Communities found:   {stats['communities']}")
    print(f"Modularity:          {stats.get('modularity', 'N/A')}")
    print(f"Storylines updated:  {stats['updated']}")
    if args.dry_run:
        print("\n[DRY RUN] No changes written to database.")
    print("\nDone!")


if __name__ == "__main__":
    main()
