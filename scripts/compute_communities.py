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
    resolution: float = 0.2,
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

    # Generate LLM community names (one call per community, resilient to failures)
    if GEMINI_AVAILABLE:
        logger.info("Generating LLM community names (%d communities)...", len(freq))
        community_nodes: dict[int, list] = {}
        for node, cid in partition.items():
            community_nodes.setdefault(cid, []).append(node)

        with db.get_connection() as conn:
            named = 0
            for cid in sorted(community_nodes.keys()):
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

    return stats


def main():
    parser = argparse.ArgumentParser(description="Compute Louvain communities on narrative graph")
    parser.add_argument(
        "--min-weight", type=float, default=0.05,
        help="Min edge weight to include in community graph (default: 0.05)"
    )
    parser.add_argument(
        "--resolution", type=float, default=0.2,
        help="Louvain resolution: lower = larger communities (default: 0.2)"
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
    print(f"Communities named:   {stats.get('communities_named', 'N/A (dry-run or Gemini unavailable)')}")
    if args.dry_run:
        print("\n[DRY RUN] No changes written to database.")
    print("\nDone!")


if __name__ == "__main__":
    main()
