#!/usr/bin/env python3
"""
Rebuild graph edges for all active storylines.

Calls _update_graph_connections() for every active/emerging/stabilized storyline,
regenerating Jaccard-weighted edges from current key_entities + entity_idf weights.

Use after a major graph cleanup (e.g., migration 016) when storyline_edges still
contains stale edges referencing now-archived storylines.

Usage:
    python scripts/rebuild_graph_edges.py
    python scripts/rebuild_graph_edges.py --dry-run
"""

import sys
import argparse
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / '.env')

from src.storage.database import DatabaseManager
from src.nlp.narrative_processor import NarrativeProcessor
from src.utils.logger import get_logger

logger = get_logger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Rebuild graph edges for all active storylines")
    parser.add_argument("--dry-run", action="store_true", help="Count storylines without rebuilding")
    args = parser.parse_args()

    db = DatabaseManager()
    processor = NarrativeProcessor(db)

    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id FROM storylines
                WHERE narrative_status IN ('emerging', 'active', 'stabilized')
                ORDER BY momentum_score DESC NULLS LAST
            """)
            ids = [row[0] for row in cur.fetchall()]

    print("=" * 60)
    print("REBUILD GRAPH EDGES")
    print("=" * 60)
    print(f"  Storylines to process: {len(ids)}")
    print(f"  Dry run:               {args.dry_run}")
    print()

    if args.dry_run:
        print("[DRY RUN] No changes written to database.")
        return

    # Step 0: Clean up stale edges involving old archived storylines (>30 days)
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM storyline_edges
                WHERE source_story_id IN (
                    SELECT id FROM storylines
                    WHERE narrative_status = 'archived'
                    AND last_update < NOW() - INTERVAL '30 days'
                )
                OR target_story_id IN (
                    SELECT id FROM storylines
                    WHERE narrative_status = 'archived'
                    AND last_update < NOW() - INTERVAL '30 days'
                )
            """)
            deleted = cur.rowcount
            conn.commit()
    if deleted:
        print(f"  Cleaned {deleted} stale edges (archived >30 days)")
        print()

    total_edges = 0
    for i, sid in enumerate(ids):
        n = processor._update_graph_connections(sid)
        total_edges += n or 0
        if (i + 1) % 100 == 0:
            print(f"  {i+1}/{len(ids)} storylines processed ({total_edges} edges so far)")

    print(f"\nDone!")
    print(f"  Storylines processed: {len(ids)}")
    print(f"  Edges inserted:       {total_edges}")
    print()
    print("Next step: python scripts/compute_communities.py --min-weight 0.25")


if __name__ == "__main__":
    main()
