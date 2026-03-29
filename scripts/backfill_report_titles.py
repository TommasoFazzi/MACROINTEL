"""
One-time script: generate LLM titles for existing reports that lack metadata.title.

Usage (on server):
    docker compose -p app exec backend python scripts/backfill_report_titles.py [--dry-run]

Requires: GEMINI_API_KEY env var (reads from .env if present).
"""
import argparse
import json
import logging
import os
import sys

import google.generativeai as genai

# Allow running from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.storage.database import DatabaseManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def extract_bluf(text: str) -> str:
    """
    Extract a large chunk of report text for title generation.
    Skips the H1 header line and collects up to 1800 chars of meaningful content.
    """
    if not text:
        return ""
    lines = []
    total = 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('# '):
            continue
        if stripped.startswith('---'):
            continue
        clean = stripped.replace('**', '').replace('*', '').strip()
        if not clean:
            continue
        lines.append(clean)
        total += len(clean)
        if total >= 1800:
            break
    return '\n'.join(lines)


def generate_title(model: genai.GenerativeModel, report_date: str, focus_areas: list, bluf: str) -> str:
    """Generate a headline title via Gemini 2.0 Flash."""
    prompt = (
        "You are an intelligence editor writing a headline for a daily geopolitical briefing.\n"
        f"Date: {report_date}\n"
        f"Report excerpt:\n{bluf[:1500]}\n\n"
        "Task: Write a headline of maximum 80 characters that captures the MOST SPECIFIC event or development "
        "in the excerpt — name actual countries, leaders, organizations, or conflicts involved.\n"
        "AVOID generic phrases like 'Global Instability', 'Cyberwar', 'AI Race', 'Shifting Alliances', "
        "'Geopolitical Tensions' unless paired with a specific named actor.\n"
        "Good examples: 'Iran Strikes US Bases in Iraq; Israel Expands Ground Operations' or "
        "'China Sanctions EU Officials Over Taiwan; NATO Activates Article 4'\n"
        "Return ONLY the headline. No quotes, no trailing punctuation, no prefix."
    )
    try:
        resp = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(temperature=0.3, max_output_tokens=80),
            request_options={"timeout": 30},
        )
        raw = resp.text.strip().strip('"').strip("'")
        if raw.endswith('.'):
            raw = raw[:-1]
        return raw[:80]
    except Exception as e:
        logger.warning(f"  Title generation failed: {e}")
        return ""


def main():
    parser = argparse.ArgumentParser(description="Backfill LLM titles for reports missing metadata.title")
    parser.add_argument("--dry-run", action="store_true", help="Preview titles without writing to DB")
    args = parser.parse_args()

    # Configure Gemini
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        logger.error("GEMINI_API_KEY not set")
        sys.exit(1)

    genai.configure(api_key=api_key, transport='rest')
    model = genai.GenerativeModel('gemini-2.0-flash')

    db = DatabaseManager()

    # Fetch reports missing a title
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, report_date, report_type,
                       metadata->>'title' as existing_title,
                       metadata->'focus_areas' as focus_areas,
                       LEFT(COALESCE(final_content, draft_content), 3000) as content_preview
                FROM reports
                WHERE metadata->>'title' IS NULL OR TRIM(metadata->>'title') = ''
                ORDER BY report_date DESC
            """)
            rows = cur.fetchall()

    logger.info(f"Found {len(rows)} reports without a title.")

    if not rows:
        logger.info("Nothing to do.")
        return

    for row in rows:
        report_id, report_date, report_type, existing_title, focus_areas_raw, content_preview = row

        # Parse focus_areas from JSONB (comes as list or None)
        if isinstance(focus_areas_raw, list):
            focus_areas = focus_areas_raw
        elif isinstance(focus_areas_raw, str):
            try:
                focus_areas = json.loads(focus_areas_raw)
            except Exception:
                focus_areas = []
        else:
            focus_areas = []

        bluf = extract_bluf(content_preview or "")
        date_str = str(report_date)

        logger.info(f"  [{report_id}] {date_str} ({report_type}) — generating title...")
        title = generate_title(model, date_str, focus_areas, bluf)

        if not title:
            logger.warning(f"  [{report_id}] Skipping — could not generate title")
            continue

        logger.info(f"  [{report_id}] → {title!r}")

        if not args.dry_run:
            with db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE reports
                        SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object('title', %s::text),
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (title, report_id),
                    )
                conn.commit()

    if args.dry_run:
        logger.info("Dry-run complete — no changes written.")
    else:
        logger.info(f"Done — {len(rows)} reports updated.")


if __name__ == "__main__":
    main()
