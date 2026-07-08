#!/usr/bin/env python3
"""
migrate_storylines_to_en.py — One-shot backfill script (use and discard after run).

Translates existing storyline summaries from Italian to English and reformats
them into the 3-bullet CIA/SITREP format used by the updated narrative engine.

Targets: narrative_status IN ('active', 'emerging', 'stabilized') with non-null summary.

Usage:
    python scripts/migrate_storylines_to_en.py
    python scripts/migrate_storylines_to_en.py --dry-run --limit 3
    python scripts/migrate_storylines_to_en.py --limit 10
    python scripts/migrate_storylines_to_en.py --status active,emerging
"""

import os
import re
import sys
import time
import argparse
import logging
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / '.env')

import google.generativeai as genai
from psycopg2.extras import Json
from sentence_transformers import SentenceTransformer

from src.storage.database import DatabaseManager
from src.utils.logger import get_logger

logger = get_logger(__name__)

EMBEDDING_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
RATE_LIMIT_SECONDS = 2.0  # between Gemini calls


def _strip_llm_markdown(text: str) -> str:
    """Strip markdown formatting that Gemini may emit despite instructions."""
    text = re.sub(r'^\s*#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*{1,3}([^*\n]+)\*{1,3}', r'\1', text)
    text = re.sub(r'`([^`\n]+)`', r'\1', text)
    text = re.sub(r'^\s*>\s*', '', text, flags=re.MULTILINE)
    return text.strip()


def _parse_response(text: str, current_title: str, current_summary: str):
    """Parse a cleaned Gemini response into (new_title, new_summary, new_entities)."""
    new_title = current_title
    new_summary = current_summary or ""
    new_entities = None

    # Extract TITLE:
    for line in text.split('\n'):
        line = line.strip()
        if line.upper().startswith('TITLE:'):
            new_title = line[6:].strip().strip('"\'')[:100]
            break

    # Extract EXECUTIVE SUMMARY: block (multiline)
    if 'EXECUTIVE SUMMARY:' in text.upper():
        parts = re.split(r'EXECUTIVE SUMMARY:', text, flags=re.IGNORECASE, maxsplit=1)
        if len(parts) == 2:
            block = parts[1]
            if 'ENTITIES:' in block.upper():
                block = re.split(r'ENTITIES:', block, flags=re.IGNORECASE)[0]
            new_summary = block.strip()

    # Extract ENTITIES:
    for line in text.split('\n'):
        line_stripped = line.strip()
        if re.match(r'^ENTITIES:', line_stripped, re.IGNORECASE):
            entities_raw = re.sub(r'^ENTITIES:', '', line_stripped, flags=re.IGNORECASE).strip()
            parsed = [e.strip().strip('"\'-[]') for e in entities_raw.split(',')]
            parsed = [e for e in parsed if e and len(e) >= 2]
            if parsed:
                new_entities = parsed[:15]
            break

    return new_title, new_summary, new_entities


def migrate_storylines(
    dry_run: bool = False,
    limit: int = 0,
    status_filter: list[str] = None,
) -> dict:
    if status_filter is None:
        status_filter = ['active', 'emerging', 'stabilized']

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set in environment")

    genai.configure(api_key=api_key, transport='rest')
    model = genai.GenerativeModel('gemini-2.5-flash-lite')

    logger.info("Loading embedding model %s...", EMBEDDING_MODEL_NAME)
    embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)

    db = DatabaseManager()
    stats = {"total": 0, "migrated": 0, "failed": 0, "skipped_dry_run": 0}

    # Fetch storylines to migrate
    placeholders = ','.join(['%s'] * len(status_filter))
    query = f"""
        SELECT id, title, summary, key_entities
        FROM storylines
        WHERE narrative_status IN ({placeholders})
          AND summary IS NOT NULL AND summary != ''
        ORDER BY id
    """
    if limit > 0:
        query += f" LIMIT {limit}"

    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, status_filter)
            rows = cur.fetchall()

    stats["total"] = len(rows)
    logger.info("Found %d storylines to migrate (status: %s)", len(rows), status_filter)

    for i, (sid, title, summary, key_entities) in enumerate(rows, 1):
        logger.info("[%d/%d] Storyline #%d: %s", i, len(rows), sid, (title or '')[:60])

        if dry_run:
            logger.info("  [DRY RUN] Would call Gemini and update.")
            stats["skipped_dry_run"] += 1
            continue

        try:
            prompt = (
                "You are a senior geopolitical intelligence analyst.\n"
                "Translate the following storyline brief to English and reformat it into "
                "3 bullet points. Use dry, operational SITREP style. "
                "Do not add facts not present in the original text.\n\n"
                f"CURRENT TITLE: {title}\n"
                f"CURRENT SUMMARY: {summary}\n\n"
                "Respond in this EXACT format (no markdown, no extra text):\n"
                "TITLE: [Max 8 words, SITREP style]\n"
                "EXECUTIVE SUMMARY:\n"
                "- [Bullet 1: Core event]\n"
                "- [Bullet 2: Key actors and context]\n"
                "- [Bullet 3: Strategic/economic/security implications]\n"
                "ENTITIES: [comma-separated proper nouns — People, Organizations, Locations]"
            )

            response = model.generate_content(
                prompt,
                generation_config={"max_output_tokens": 400, "temperature": 0.3},
                request_options={"timeout": 30},
            )
            text = _strip_llm_markdown(response.text)
            new_title, new_summary, new_entities = _parse_response(text, title, summary)

            # Encode new summary
            summary_vector = embedding_model.encode(new_summary).tolist()

            with db.get_connection() as conn:
                with conn.cursor() as cur:
                    if new_entities:
                        cur.execute("""
                            UPDATE storylines SET
                                title = %s,
                                summary = %s,
                                summary_vector = %s::vector,
                                key_entities = %s
                            WHERE id = %s
                        """, (new_title, new_summary, summary_vector, Json(new_entities), sid))
                    else:
                        cur.execute("""
                            UPDATE storylines SET
                                title = %s,
                                summary = %s,
                                summary_vector = %s::vector
                            WHERE id = %s
                        """, (new_title, new_summary, summary_vector, sid))
                conn.commit()

            logger.info("  → '%s'", new_title[:60])
            stats["migrated"] += 1

        except Exception as e:
            logger.error("  Failed storyline #%d: %s — skipping", sid, e)
            stats["failed"] += 1

        time.sleep(RATE_LIMIT_SECONDS)

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Backfill storyline summaries: translate to English SITREP 3-bullet format"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without writing to DB")
    parser.add_argument("--limit", type=int, default=0,
                        help="Process only first N storylines (0 = all)")
    parser.add_argument("--status", type=str, default="active,emerging,stabilized",
                        help="Comma-separated narrative_status values to target")
    args = parser.parse_args()

    status_filter = [s.strip() for s in args.status.split(',')]

    print("=" * 60)
    print("STORYLINE MIGRATION: Italian → English SITREP Format")
    print("=" * 60)
    print(f"  Status filter: {status_filter}")
    print(f"  Limit:         {args.limit or 'all'}")
    print(f"  Dry run:       {args.dry_run}")
    print()

    stats = migrate_storylines(
        dry_run=args.dry_run,
        limit=args.limit,
        status_filter=status_filter,
    )

    print(f"\nTotal storylines found:  {stats['total']}")
    print(f"Successfully migrated:   {stats['migrated']}")
    print(f"Failed (skipped):        {stats['failed']}")
    if args.dry_run:
        print(f"Skipped (dry run):       {stats['skipped_dry_run']}")
        print("\n[DRY RUN] No changes written to database.")
    print("\nDone!")


if __name__ == "__main__":
    main()
