#!/usr/bin/env python3
"""
Backfill derived historical context columns for macro_indicators.

Steps:
  1. --seed-fred   : populate raw 90-day history for FRED daily indicators
  2. --compute     : compute ma_7d/ma_30d/std_30d/pct_change_7d/pct_change_30d/percentile_rank_30d

Usage:
    python scripts/backfill_macro_history.py                       # full run (seed + compute)
    python scripts/backfill_macro_history.py --seed-fred           # only raw FRED seed
    python scripts/backfill_macro_history.py --compute             # only derive columns
    python scripts/backfill_macro_history.py --indicator US_10Y_YIELD
    python scripts/backfill_macro_history.py --days 90
    python scripts/backfill_macro_history.py --dry-run
"""

import argparse
import logging
import sys
from datetime import date, timedelta
from typing import Optional

sys.path.insert(0, '.')

from src.storage.database import DatabaseManager
from src.integrations.openbb_service import OpenBBMarketService

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

# UPDATE SQL — parametrized with %s (never f-string on indicator_key)
UPDATE_SQL = """
UPDATE macro_indicators mi
SET
    ma_7d = (
        SELECT AVG(h.value) FROM (
            SELECT value FROM macro_indicators
            WHERE indicator_key = mi.indicator_key AND country_code = mi.country_code
              AND date <= mi.date
            ORDER BY date DESC LIMIT 7
        ) h
    ),
    ma_30d = (
        SELECT AVG(h.value) FROM (
            SELECT value FROM macro_indicators
            WHERE indicator_key = mi.indicator_key AND country_code = mi.country_code
              AND date <= mi.date
            ORDER BY date DESC LIMIT 30
        ) h
    ),
    std_30d = (
        SELECT STDDEV_POP(h.value) FROM (
            SELECT value FROM macro_indicators
            WHERE indicator_key = mi.indicator_key AND country_code = mi.country_code
              AND date <= mi.date
            ORDER BY date DESC LIMIT 30
        ) h
    ),
    pct_change_7d = (
        SELECT CASE WHEN v7 IS NOT NULL AND v7 != 0
               THEN ROUND(((mi.value - v7) / ABS(v7) * 100)::NUMERIC, 4)
               ELSE NULL END
        FROM (
            SELECT value AS v7 FROM macro_indicators
            WHERE indicator_key = mi.indicator_key AND country_code = mi.country_code
              AND date < mi.date
            ORDER BY date DESC LIMIT 1 OFFSET 6
        ) s
    ),
    pct_change_30d = (
        SELECT CASE WHEN v30 IS NOT NULL AND v30 != 0
               THEN ROUND(((mi.value - v30) / ABS(v30) * 100)::NUMERIC, 4)
               ELSE NULL END
        FROM (
            SELECT value AS v30 FROM macro_indicators
            WHERE indicator_key = mi.indicator_key AND country_code = mi.country_code
              AND date < mi.date
            ORDER BY date DESC LIMIT 1 OFFSET 29
        ) s
    ),
    percentile_rank_30d = (
        SELECT ROUND(
            (COUNT(*) FILTER (WHERE h.value <= mi.value)::NUMERIC
             / NULLIF(COUNT(*), 0) * 100)::NUMERIC, 1)
        FROM (
            SELECT value FROM macro_indicators
            WHERE indicator_key = mi.indicator_key AND country_code = mi.country_code
              AND date <= mi.date
            ORDER BY date DESC LIMIT 30
        ) h
    ),
    pct_change_12m = (
        SELECT CASE WHEN v12 IS NOT NULL AND v12 != 0
               THEN ROUND(((mi.value - v12) / ABS(v12) * 100)::NUMERIC, 4)
               ELSE NULL END
        FROM (
            SELECT value AS v12 FROM macro_indicators
            WHERE indicator_key = mi.indicator_key AND country_code = mi.country_code
              AND date < mi.date
            ORDER BY date DESC LIMIT 1 OFFSET 11
        ) s
    )
WHERE indicator_key = %s
"""


def _extract_value(item, fred_series: str) -> Optional[float]:
    """Extract numeric value from an OpenBB FRED result item."""
    value = getattr(item, fred_series.lower(), None)
    if value is None:
        value = getattr(item, fred_series, None)
    if value is None:
        for attr in ['value', 'close', 'data', 'y']:
            if hasattr(item, attr):
                value = getattr(item, attr)
                break
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_date(item) -> Optional[date]:
    """Extract date from an OpenBB FRED result item."""
    raw = getattr(item, 'date', None)
    if raw is None:
        return None
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw)[:10])
    except (ValueError, TypeError):
        return None


def _insert_raw_if_missing(db, dt: date, key: str, value: float,
                            unit: str, category: str, country_code: str,
                            dry_run: bool) -> bool:
    """Insert raw observation — ON CONFLICT DO NOTHING, safe to re-run."""
    if dry_run:
        return True
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO macro_indicators
                        (date, indicator_key, value, unit, category, country_code)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (date, indicator_key) DO NOTHING
                """, (dt, key, value, unit, category, country_code))
        return True
    except Exception as e:
        logger.error(f"  insert_raw failed for {key} @ {dt}: {e}")
        return False


def seed_fred_history(db, openbb_service: OpenBBMarketService,
                      indicator_keys=None, days: int = 90,
                      dry_run: bool = False) -> None:
    """
    Populate raw historical rows for FRED daily indicators.

    Iterates ALL observations in the 90-day window (not just the latest).
    Uses INSERT ON CONFLICT DO NOTHING — safe to re-run.
    Monthly FRED series are excluded (too few observations to matter for MAs).
    """
    from src.integrations.openbb_service import get_obb

    target = date.today()
    start = target - timedelta(days=days)
    obb = get_obb()

    if not obb:
        logger.error("OpenBB not available — cannot seed FRED history")
        return

    total_saved = 0
    for key, config in openbb_service.MACRO_INDICATORS.items():
        if indicator_keys and key not in indicator_keys:
            continue
        if config.get('fetch_category') != 'fred':
            continue
        fred_series = config.get('fred_series')
        if not fred_series:
            continue
        freq = openbb_service.FRED_SERIES_FREQUENCY.get(fred_series, 'monthly')
        if freq != 'daily':
            continue  # monthly indicators have few observations; skip seed

        logger.info(f"  Seeding {key} ({fred_series}) from {start} to {target} ...")
        try:
            result = obb.economy.fred_series(
                symbol=fred_series,
                start_date=str(start),
                end_date=str(target),
                provider='fred',
            )
        except Exception as e:
            logger.warning(f"  {key}: FRED fetch failed: {e}")
            continue

        if not result or not result.results:
            logger.warning(f"  {key}: no data returned")
            continue

        saved = 0
        for item in result.results:
            val = _extract_value(item, fred_series)
            dt = _extract_date(item)
            if val is None or dt is None:
                continue
            if _insert_raw_if_missing(db, dt, key, val,
                                      config['unit'], config['category'],
                                      config.get('country_code', 'US'),
                                      dry_run):
                saved += 1

        logger.info(f"  {key}: {'(dry-run) would seed' if dry_run else 'seeded'} {saved} rows")
        total_saved += saved

    logger.info(f"Seed complete — {total_saved} rows {'(dry-run)' if dry_run else 'inserted/skipped'}")


def compute_derived_columns(db, indicator_keys=None, dry_run: bool = False) -> None:
    """
    Compute ma_7d/ma_30d/std_30d/pct_change_7d/pct_change_30d/percentile_rank_30d
    for all existing macro_indicator rows. Idempotent UPDATE.

    Commit per indicator_key to avoid long locks.
    """
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                if indicator_keys:
                    cur.execute(
                        "SELECT DISTINCT indicator_key FROM macro_indicators "
                        "WHERE indicator_key = ANY(%s) ORDER BY indicator_key",
                        (list(indicator_keys),)
                    )
                else:
                    cur.execute(
                        "SELECT DISTINCT indicator_key FROM macro_indicators "
                        "ORDER BY indicator_key"
                    )
                keys = [row[0] for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"Failed to list indicator keys: {e}")
        return

    logger.info(f"Computing derived columns for {len(keys)} indicator(s)...")
    updated = 0
    for key in keys:
        if dry_run:
            logger.info(f"  (dry-run) would UPDATE {key}")
            continue
        try:
            with db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(UPDATE_SQL, (key,))
                    count = cur.rowcount
            logger.info(f"  {key}: updated {count} row(s)")
            updated += count
        except Exception as e:
            logger.error(f"  {key}: UPDATE failed: {e}")

    if not dry_run:
        logger.info(f"Compute complete — {updated} total rows updated")


def main():
    parser = argparse.ArgumentParser(description="Backfill macro_indicators historical columns")
    parser.add_argument('--seed-fred', action='store_true',
                        help='Seed raw 90-day FRED daily history')
    parser.add_argument('--compute', action='store_true',
                        help='Compute derived columns (ma_7d, ma_30d, etc.)')
    parser.add_argument('--indicator', metavar='KEY', action='append',
                        help='Limit to specific indicator key (repeatable)')
    parser.add_argument('--days', type=int, default=90,
                        help='History window in days for seed (default: 90)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print what would be done without modifying DB')
    args = parser.parse_args()

    # Default: run both steps if no flag given
    run_seed = args.seed_fred or (not args.seed_fred and not args.compute)
    run_compute = args.compute or (not args.seed_fred and not args.compute)

    if args.dry_run:
        logger.info("DRY-RUN mode — no DB modifications")

    db = DatabaseManager()
    service = OpenBBMarketService(db)

    if run_seed:
        logger.info("=== Step 1: Seed FRED daily history ===")
        seed_fred_history(db, service,
                          indicator_keys=set(args.indicator) if args.indicator else None,
                          days=args.days,
                          dry_run=args.dry_run)

    if run_compute:
        logger.info("=== Step 2: Compute derived columns ===")
        compute_derived_columns(db,
                                indicator_keys=set(args.indicator) if args.indicator else None,
                                dry_run=args.dry_run)

    logger.info("Done.")


if __name__ == '__main__':
    main()
