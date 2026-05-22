#!/usr/bin/env python3
"""
Daily Market Data Fetch Script (OpenBB Integration)

Fetches macro economic indicators from OpenBB and stores in database.
Should be run daily BEFORE report generation (e.g., 9:00 AM via cron).

Usage:
    python scripts/fetch_daily_market_data.py
    python scripts/fetch_daily_market_data.py --date 2025-01-10
    python scripts/fetch_daily_market_data.py --dry-run
    python scripts/fetch_daily_market_data.py --force  # Refresh even if data exists

Cron example (run at 9:00 AM daily):
    0 9 * * * cd /path/to/INTELLIGENCE_ITA && python scripts/fetch_daily_market_data.py >> logs/market_data.log 2>&1
"""

import sys
import argparse
from datetime import date, datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.utils.logger import get_logger
from src.storage.database import DatabaseManager
from src.integrations.market_calendar import get_market_close_date

logger = get_logger(__name__)


def fetch_macro_data(target_date: date, dry_run: bool = False, force: bool = False) -> bool:
    """
    Fetch macro indicators from OpenBB and store in database.

    Args:
        target_date: Date to fetch data for
        dry_run: If True, only log what would be fetched without saving
        force: If True, refresh data even if already exists

    Returns:
        True if successful, False otherwise
    """
    logger.info("=" * 60)
    logger.info(f"DAILY MARKET DATA FETCH - {target_date}")
    logger.info("=" * 60)

    if dry_run:
        logger.info("DRY RUN MODE - No data will be saved")
    if force:
        logger.info("FORCE MODE - Will refresh existing data")

    # Weekends: equity and commodity markets are closed; the evening fetch already captured
    # the last session close on Friday. Nothing useful to fetch on Saturday or Sunday.
    if target_date.weekday() >= 5:
        logger.info(f"Skipping fetch for {target_date} (weekend)")
        return True

    # Holiday awareness: log when fetching on a US market holiday.
    try:
        from src.integrations.market_calendar import fetch_mode
        _mode = fetch_mode(target_date)
        if _mode == 'holiday':
            logger.info(
                f"NOTE: {target_date} is a US market holiday (NYSE closed). "
                "FRED/FX/crypto data unaffected. Equity/commodity will reflect last trading close."
            )
    except ImportError:
        pass  # pandas_market_calendars not installed — skip

    try:
        from src.integrations.openbb_service import OpenBBMarketService

        db = DatabaseManager()
        service = OpenBBMarketService(db)

        # Delete existing data if force mode
        if force and not dry_run:
            with db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM macro_indicators WHERE date = %s AND country_code = 'US'",
                        (target_date,)
                    )
                    deleted = cur.rowcount
                    if deleted > 0:
                        logger.info(f"  Deleted {deleted} existing US indicators (RO data preserved)")

        # Fetch macro indicators (includes SHIPPING_BDI via BDRY ETF)
        logger.info("\nFetching macro indicators (US/global)...")
        if not dry_run:
            macro_success = service.ensure_daily_macro_data(target_date)
        else:
            logger.info("  Would fetch: US_10Y_YIELD, VIX, BRENT_OIL, EUR_USD, GOLD, SP500, SHIPPING_BDI...")
            macro_success = True

        # Fetch Romania indicators (EUR_RON, BNR_RATE, ROBOR_3M, RO_10Y_YIELD, etc.)
        logger.info("\nFetching macro indicators (Romania)...")
        if not dry_run:
            ro_success = service.fetch_ro_indicators(target_date)
            if not ro_success:
                logger.warning("Romania macro fetch: partial or failed — check logs above")
        else:
            logger.info("  Would fetch: EUR_RON, BNR_RATE, ROBOR_3M, RO_10Y_YIELD, RO_10Y_DE_SPREAD, RO_CPI_YOY, BET_INDEX...")
            ro_success = True

        # Summary
        logger.info("\n" + "=" * 60)
        if macro_success:
            logger.info("SUCCESS: Market data fetch completed")

            if not dry_run:
                # Display what was saved
                context_text = service.get_macro_context_text(target_date)
                if context_text:
                    logger.info("\nMACRO CONTEXT PREVIEW (US/global):")
                    logger.info("-" * 40)
                    for line in context_text.split('\n'):
                        logger.info(f"  {line}")
                    logger.info("-" * 40)
        else:
            logger.warning("PARTIAL SUCCESS: Some US/global indicators may be missing")

        logger.info("=" * 60)
        return macro_success

    except ImportError as e:
        logger.error(f"Import error: {e}")
        logger.error("Make sure OpenBB is installed: pip install openbb")
        return False

    except Exception as e:
        logger.error(f"Fetch failed: {e}")
        return False


def backfill_macro_data(days: int = 7, dry_run: bool = False) -> dict:
    """
    Backfill macro data for the last N days.

    Args:
        days: Number of days to backfill
        dry_run: If True, only log what would be fetched

    Returns:
        Dictionary with success/failure counts
    """
    logger.info(f"BACKFILLING {days} DAYS OF MACRO DATA")

    stats = {'success': 0, 'failed': 0, 'skipped': 0}
    today = date.today()

    try:
        from src.integrations.market_calendar import fetch_mode as _fetch_mode
    except ImportError:
        _fetch_mode = None  # fallback: weekday-only check

    for i in range(days):
        target_date = today - timedelta(days=i)

        if _fetch_mode is not None:
            mode = _fetch_mode(target_date)
            if mode == 'skip':
                logger.info(f"  {target_date}: Skipping (weekend)")
                stats['skipped'] += 1
                continue
            if mode == 'holiday':
                logger.info(f"  {target_date}: NYSE holiday — equity/commodity data will be last-close stale")
        else:
            # pandas_market_calendars not available — fall back to weekend-only skip
            if target_date.weekday() >= 5:
                logger.info(f"  {target_date}: Skipping (weekend)")
                stats['skipped'] += 1
                continue

        logger.info(f"\n--- Processing {target_date} ---")
        success = fetch_macro_data(target_date, dry_run)

        if success:
            stats['success'] += 1
        else:
            stats['failed'] += 1

    logger.info("\n" + "=" * 60)
    logger.info(f"BACKFILL COMPLETE: {stats['success']} success, {stats['failed']} failed, {stats['skipped']} skipped")
    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Fetch daily macro market data from OpenBB"
    )

    parser.add_argument(
        '--date',
        type=str,
        default=None,
        help='Target date (YYYY-MM-DD format). Default: today'
    )

    parser.add_argument(
        '--backfill',
        type=int,
        default=0,
        metavar='DAYS',
        help='Backfill last N days of data'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be fetched without saving'
    )

    parser.add_argument(
        '--force',
        action='store_true',
        help='Force refresh even if data already exists for the date'
    )

    args = parser.parse_args()

    # Parse target date
    if args.date:
        try:
            target_date = datetime.strptime(args.date, '%Y-%m-%d').date()
        except ValueError:
            logger.error(f"Invalid date format: {args.date}. Use YYYY-MM-DD")
            return 1
    else:
        target_date = get_market_close_date()
        logger.info(f"Auto-detected market close date: {target_date}")

    # Run backfill or single-day fetch
    if args.backfill > 0:
        stats = backfill_macro_data(args.backfill, args.dry_run)
        return 0 if stats['failed'] == 0 else 1
    else:
        success = fetch_macro_data(target_date, args.dry_run, args.force)
        return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
