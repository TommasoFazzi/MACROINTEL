#!/usr/bin/env python3
"""
fetch_romania_macro.py — Fetch and display Romania macro indicators.

Calls ensure_daily_macro_data() (idempotent — skips if data already fetched today)
then prints an RO-specific context preview. Useful for standalone verification or
triggering the macro fetch independently of the global pipeline.

Usage:
    python scripts/fetch_romania_macro.py
    python scripts/fetch_romania_macro.py --date 2026-05-10
    python scripts/fetch_romania_macro.py --force   # re-fetch even if data exists
"""

import sys
import argparse
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.utils.logger import get_logger
from src.storage.database import DatabaseManager

logger = get_logger(__name__)

_RO_KEYS = ["BNR_RATE", "RO_CPI_YOY", "EUR_RON", "RO_DEFICIT_GDP", "RO_10Y_YIELD"]


def _show_ro_context(db: DatabaseManager, target_date: date) -> None:
    """Print current Romania macro indicator values from DB."""
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT ON (mi.indicator_key)
                        mi.indicator_key, mi.value, mi.unit, mi.date,
                        COALESCE(meta.expected_frequency, 'unknown') AS freq,
                        meta.is_stale,
                        meta.staleness_days
                    FROM macro_indicators mi
                    LEFT JOIN macro_indicator_metadata meta ON meta.key = mi.indicator_key
                    WHERE mi.country_code = 'RO'
                      AND mi.indicator_key = ANY(%s)
                    ORDER BY mi.indicator_key, mi.date DESC
                """, [_RO_KEYS])
                rows = cur.fetchall()
    except Exception as e:
        logger.error(f"DB query failed: {e}")
        return

    if not rows:
        logger.warning("No Romania macro data found in DB. Run the pipeline first.")
        return

    logger.info("\nROMANIA MACRO INDICATORS")
    logger.info("-" * 50)
    for key, value, unit, data_date, freq, is_stale, staleness_days in rows:
        stale_tag = f" [STALE {staleness_days}d]" if is_stale else " [live]"
        logger.info(f"  {key:<20} {float(value):.4f} {unit or '':<12} data={data_date} freq={freq}{stale_tag}")
    logger.info("-" * 50)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Romania macro indicators")
    parser.add_argument("--date", help="Target date YYYY-MM-DD (default: today)")
    parser.add_argument("--force", action="store_true", help="Re-fetch even if data exists for today")
    args = parser.parse_args()

    target_date = date.today()
    if args.date:
        try:
            target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            logger.error(f"Invalid date format: {args.date}. Use YYYY-MM-DD.")
            return 1

    logger.info("=" * 60)
    logger.info(f"ROMANIA MACRO FETCH — {target_date}")
    logger.info("=" * 60)

    try:
        from src.integrations.openbb_service import OpenBBMarketService
    except ImportError as e:
        logger.error(f"OpenBB import failed: {e}")
        return 1

    db = DatabaseManager()
    service = OpenBBMarketService(db)

    if args.force:
        try:
            with db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM macro_indicators WHERE country_code = 'RO'",
                        # Delete all RO rows (not just today) so FRED monthly data is re-fetched
                    )
                    logger.info(f"Deleted {cur.rowcount} existing RO indicators (force mode)")
        except Exception as e:
            logger.error(f"Force-delete failed: {e}")
            return 1
    elif service._has_macro_data(target_date, country_code='RO'):
        logger.info(f"RO macro data already present for {target_date} — skipping fetch (use --force to override)")
        _show_ro_context(db, target_date)
        db.close()
        return 0

    # Fetch only the 5 RO indicators, independent of the global US pipeline state
    success = service.fetch_ro_indicators(target_date)

    if success:
        logger.info("Macro fetch completed.")
    else:
        logger.warning("Macro fetch reported partial failure.")

    _show_ro_context(db, target_date)
    db.close()

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
