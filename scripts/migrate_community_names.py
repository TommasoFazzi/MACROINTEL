#!/usr/bin/env python3
"""
migrate_community_names.py — One-shot backfill script (use and discard after run).

Generates LLM-based 2-4 word macro-theme names for all existing communities
and saves them to the community_name column in the storylines table.

Requires migration 022_community_name.sql to be applied first:
    psql $DATABASE_URL -f migrations/022_community_name.sql

Usage:
    python scripts/migrate_community_names.py
    python scripts/migrate_community_names.py --dry-run --limit 3
    python scripts/migrate_community_names.py --limit 5
"""

import os
import re
import sys
import time
import argparse
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / '.env')

import google.generativeai as genai

from src.storage.database import DatabaseManager
from src.utils.logger import get_logger

logger = get_logger(__name__)

RATE_LIMIT_SECONDS = 1.5


def _name_community(cid: int, titles: list[str], model) -> str | None:
    """Call Gemini to generate a 2-4 word macro-theme label for a community."""
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
    response = model.generate_content(
        prompt,
        generation_config={"max_output_tokens": 20, "temperature": 0.2},
        request_options={"timeout": 30},
    )
    name = re.sub(r'[*`"\'#]', '', response.text).strip()[:80]
    return name if name else None


def migrate_community_names(dry_run: bool = False, limit: int = 0) -> dict:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set in environment")

    genai.configure(api_key=api_key, transport='rest')
    model = genai.GenerativeModel('gemini-2.5-flash-lite')

    db = DatabaseManager()
    stats = {"total": 0, "named": 0, "failed": 0, "skipped_dry_run": 0}

    # Get all distinct community IDs
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT community_id
                FROM storylines
                WHERE community_id IS NOT NULL
                ORDER BY community_id
            """)
            community_ids = [row[0] for row in cur.fetchall()]

    if limit > 0:
        community_ids = community_ids[:limit]

    stats["total"] = len(community_ids)
    logger.info("Found %d communities to name", len(community_ids))

    for i, cid in enumerate(community_ids, 1):
        logger.info("[%d/%d] Community %d", i, len(community_ids), cid)

        if dry_run:
            logger.info("  [DRY RUN] Would call Gemini and update.")
            stats["skipped_dry_run"] += 1
            continue

        try:
            # Fetch top-15 storyline titles for this community
            with db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT title FROM storylines
                        WHERE community_id = %s AND title IS NOT NULL
                        ORDER BY momentum_score DESC NULLS LAST
                        LIMIT 15
                    """, (cid,))
                    titles = [row[0] for row in cur.fetchall()]

            name = _name_community(cid, titles, model)
            if not name:
                logger.warning("  Community %d: empty response from Gemini", cid)
                stats["failed"] += 1
            else:
                with db.get_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE storylines SET community_name = %s WHERE community_id = %s",
                            (name, cid),
                        )
                    conn.commit()
                logger.info("  → '%s' (%d storylines)", name, len(titles))
                stats["named"] += 1

        except Exception as e:
            logger.error("  Failed community %d: %s — skipping", cid, e)
            stats["failed"] += 1

        time.sleep(RATE_LIMIT_SECONDS)

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Backfill community_name for all existing Louvain communities"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without writing to DB")
    parser.add_argument("--limit", type=int, default=0,
                        help="Process only first N communities (0 = all)")
    args = parser.parse_args()

    print("=" * 60)
    print("COMMUNITY NAMES MIGRATION")
    print("=" * 60)
    print(f"  Limit:    {args.limit or 'all'}")
    print(f"  Dry run:  {args.dry_run}")
    print()

    stats = migrate_community_names(dry_run=args.dry_run, limit=args.limit)

    print(f"\nTotal communities found:  {stats['total']}")
    print(f"Successfully named:        {stats['named']}")
    print(f"Failed (skipped):          {stats['failed']}")
    if args.dry_run:
        print(f"Skipped (dry run):         {stats['skipped_dry_run']}")
        print("\n[DRY RUN] No changes written to database.")
    print("\nDone!")


if __name__ == "__main__":
    main()
