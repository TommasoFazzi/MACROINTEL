#!/usr/bin/env python3
"""
Recompute derived macro columns (previous_value + 7 historical-context columns).

WHY THIS EXISTS
---------------
The derived columns (previous_value, ma_7d, ma_30d, std_30d, pct_change_7d,
pct_change_30d, percentile_rank_30d, pct_change_12m) are computed once at INSERT
time by OpenBBMarketService._save_macro_indicator. That is correct for the live
append-only daily fetch (all prior rows already exist), but NOT after a backfill:
inserting a row mid-history shifts the row-based windows (LIMIT 7/30, OFFSET 6/29)
of every subsequent row and invalidates their previous_value. Only a recompute pass
over the affected rows restores consistency.

This script reuses OpenBBMarketService._recompute_derived_columns — the exact same
SQL used by the live path — so results are guaranteed identical, just applied to the
whole table (or a scoped subset).

SAFETY
------
Only derived columns are written. value / date / indicator_key are NEVER touched.
The operation is idempotent and reversible (re-run to recompute again). Always run
--dry-run first: it performs the recompute inside a transaction, reports how many
rows would change with before/after samples, then ROLLS BACK without writing.

Usage:
    python scripts/recompute_macro_derived.py --dry-run
    python scripts/recompute_macro_derived.py --dry-run --indicator SILVER
    python scripts/recompute_macro_derived.py --since 2026-01-01 --dry-run
    python scripts/recompute_macro_derived.py            # real run (writes + commits)

In production (no venv on server — run inside the backend container):
    docker compose -p app exec backend python scripts/recompute_macro_derived.py --dry-run
"""

import sys
import argparse
from datetime import datetime, date
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.utils.logger import get_logger
from src.storage.database import DatabaseManager
from src.integrations.openbb_service import OpenBBMarketService

logger = get_logger(__name__)

# Columns recomputed by _recompute_derived_columns — also the columns we diff in dry-run.
DERIVED_COLUMNS = [
    'previous_value', 'ma_7d', 'ma_30d', 'std_30d',
    'pct_change_7d', 'pct_change_30d', 'percentile_rank_30d', 'pct_change_12m',
]

DEFAULT_SAMPLE_INDICATORS = ['SILVER', 'GOLD', 'US_10Y_YIELD']


def _snapshot(cur, indicator_key, country_code, since):
    """Fetch value + derived columns for in-scope rows, keyed by (date, indicator_key, country_code)."""
    where = []
    params = []
    if indicator_key is not None:
        where.append("indicator_key = %s")
        params.append(indicator_key)
    if country_code is not None:
        where.append("country_code = %s")
        params.append(country_code)
    if since is not None:
        where.append("date >= %s")
        params.append(since)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    cur.execute(f"""
        SELECT date, indicator_key, country_code, value, {', '.join(DERIVED_COLUMNS)}
        FROM macro_indicators
        {where_sql}
    """, params)
    snap = {}
    for row in cur.fetchall():
        key = (row[0], row[1], row[2])
        snap[key] = {'value': row[3], **{col: row[4 + i] for i, col in enumerate(DERIVED_COLUMNS)}}
    return snap


def _changed_keys(before, after):
    """Return list of keys whose any derived column differs (NULL-aware)."""
    changed = []
    for key, b in before.items():
        a = after.get(key)
        if a is None:
            continue
        for col in DERIVED_COLUMNS:
            if b[col] != a[col]:  # Decimal/None compare; != treats None vs val as changed
                changed.append(key)
                break
    return changed


def _print_sample(before, after, sample_indicators, limit=6):
    """Print before/after derived columns for sample indicators (most recent rows)."""
    for ind in sample_indicators:
        rows = sorted([k for k in before if k[1] == ind], key=lambda k: k[0], reverse=True)[:limit]
        if not rows:
            continue
        print(f"\n  --- {ind} (last {len(rows)} rows) ---")
        for key in rows:
            d = key[0]
            b, a = before[key], after.get(key, {})
            diffs = []
            for col in DERIVED_COLUMNS:
                bv, av = b[col], a.get(col)
                if bv != av:
                    diffs.append(f"{col}: {bv} -> {av}")
            status = "  (no change)" if not diffs else ""
            print(f"   {d} value={b['value']}{status}")
            for diff in diffs:
                print(f"       {diff}")


def main():
    parser = argparse.ArgumentParser(description="Recompute derived macro columns")
    parser.add_argument('--dry-run', action='store_true',
                        help='Recompute in a transaction, report changes, then rollback (no write)')
    parser.add_argument('--indicator', type=str, default=None,
                        help='Limit to a single indicator_key (default: all)')
    parser.add_argument('--country', type=str, default=None,
                        help='Limit to a country_code (e.g. US, RO; default: all)')
    parser.add_argument('--since', type=str, default=None,
                        help='Limit to rows with date >= YYYY-MM-DD (default: all history)')
    parser.add_argument('--sample', type=str, default=None,
                        help='Comma-separated indicators to show before/after in dry-run '
                             f'(default: {",".join(DEFAULT_SAMPLE_INDICATORS)})')
    args = parser.parse_args()

    since = None
    if args.since:
        try:
            since = datetime.strptime(args.since, '%Y-%m-%d').date()
        except ValueError:
            logger.error(f"Invalid --since date: {args.since}. Use YYYY-MM-DD")
            return 1

    sample_indicators = (args.sample.split(',') if args.sample else DEFAULT_SAMPLE_INDICATORS)

    scope = []
    if args.indicator:
        scope.append(f"indicator={args.indicator}")
    if args.country:
        scope.append(f"country={args.country}")
    if since:
        scope.append(f"since={since}")
    scope_str = ", ".join(scope) if scope else "ALL history"

    print("=" * 70)
    print(f"RECOMPUTE DERIVED MACRO COLUMNS — scope: {scope_str}")
    print(f"Mode: {'DRY-RUN (no write)' if args.dry_run else 'REAL RUN (writes + commits)'}")
    print("=" * 70)

    db = DatabaseManager()
    service = OpenBBMarketService(db)

    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                if args.dry_run:
                    before = _snapshot(cur, args.indicator, args.country, since)
                    n_updated = service._recompute_derived_columns(
                        cur, indicator_key=args.indicator, country_code=args.country, since=since
                    )
                    after = _snapshot(cur, args.indicator, args.country, since)
                    changed = _changed_keys(before, after)

                    print(f"\nRows in scope : {len(before)}")
                    print(f"Rows matched  : {n_updated}")
                    print(f"Rows CHANGED  : {len(changed)}")
                    _print_sample(before, after, sample_indicators)

                    conn.rollback()  # discard the UPDATE — nothing is persisted
                    print("\n[DRY-RUN] Rolled back — no data written.")
                else:
                    n_updated = service._recompute_derived_columns(
                        cur, indicator_key=args.indicator, country_code=args.country, since=since
                    )
                    print(f"\nRows recomputed and committed: {n_updated}")
        return 0
    except Exception as e:
        logger.error(f"Recompute failed: {e}")
        return 1


if __name__ == '__main__':
    sys.exit(main())
