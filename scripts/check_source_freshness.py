#!/usr/bin/env python3
"""
Report feeds that stopped producing articles.

For every feed in config/feeds.yaml, finds the newest articles.created_at with
source = feed name. Lists stale feeds (older than max_stale_days, default 14,
per-feed override in feeds.yaml), never-ingested feeds and `status: unsupported`
feeds. Prints the table and, under GitHub Actions, appends it to the run
summary ($GITHUB_STEP_SUMMARY).

Runs as its own pipeline.yml step with `if: always()`. Exit code is 0 whatever
it finds (a dead source must not fail the daily run); non-zero only when the
database cannot be queried.

Usage:
    python scripts/check_source_freshness.py
    python scripts/check_source_freshness.py --config config/feeds.yaml
"""

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / '.env')

import yaml

DEFAULT_MAX_STALE_DAYS = 14


def load_feeds(config_path: Path) -> List[Dict]:
    with open(config_path, encoding='utf-8') as f:
        return (yaml.safe_load(f) or {}).get('feeds', [])


def build_rows(feeds: List[Dict], last_created: Dict[str, datetime], now: datetime) -> List[Dict]:
    """One row per feed that needs attention: stale, never ingested or unsupported."""
    rows = []
    for feed in feeds:
        name = feed['name']
        if feed.get('status') == 'unsupported':
            rows.append({'feed': name, 'status': 'unsupported', 'last': None, 'age': None})
            continue
        last = last_created.get(name)
        if last is None:
            rows.append({'feed': name, 'status': 'never ingested', 'last': None, 'age': None})
            continue
        age = (now - last).days
        if age > feed.get('max_stale_days', DEFAULT_MAX_STALE_DAYS):
            rows.append({'feed': name, 'status': 'stale', 'last': last, 'age': age})
    order = {'stale': 0, 'never ingested': 1, 'unsupported': 2}
    rows.sort(key=lambda r: (order[r['status']], -(r['age'] or 0), r['feed']))
    return rows


def render_markdown(rows: List[Dict], n_feeds: int) -> str:
    lines = ["### Source freshness", ""]
    if not rows:
        lines.append(f"All {n_feeds} feeds produced an article recently.")
        return "\n".join(lines) + "\n"
    lines += ["| Feed | Status | Last article | Age (days) |", "|---|---|---|---|"]
    for r in rows:
        last = r['last'].strftime('%Y-%m-%d') if r['last'] else '—'
        age = str(r['age']) if r['age'] is not None else '—'
        feed = r['feed'].replace('|', '\\|')  # e.g. "Business | Euronews RSS"
        lines.append(f"| {feed} | {r['status']} | {last} | {age} |")
    lines += ["", f"{len(rows)} of {n_feeds} feeds need attention."]
    return "\n".join(lines) + "\n"


def query_last_created(feed_names: List[str]) -> Dict[str, datetime]:
    from src.storage.database import DatabaseManager
    db = DatabaseManager()
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT source, max(created_at) FROM articles "
                    "WHERE source = ANY(%s) GROUP BY source",
                    (feed_names,)
                )
                return {src: ts for src, ts in cur.fetchall()}
    finally:
        db.close()


def write_summary(markdown: str) -> None:
    path = os.environ.get('GITHUB_STEP_SUMMARY')
    if not path:
        return
    try:
        with open(path, 'a', encoding='utf-8') as f:
            f.write(markdown)
    except OSError as e:
        print(f"Could not write run summary {path}: {e}", file=sys.stderr)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--config', default=str(project_root / 'config' / 'feeds.yaml'))
    args = parser.parse_args(argv)

    feeds = load_feeds(Path(args.config))
    supported = [f['name'] for f in feeds if f.get('status') != 'unsupported']
    try:
        last_created = query_last_created(supported)
    except Exception as e:
        print(f"Source freshness check failed: database query error: {e}", file=sys.stderr)
        return 2

    markdown = render_markdown(build_rows(feeds, last_created, datetime.now(timezone.utc)), len(feeds))
    print(markdown)
    write_summary(markdown)
    return 0


if __name__ == '__main__':
    sys.exit(main())
