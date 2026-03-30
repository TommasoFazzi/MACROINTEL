#!/usr/bin/env python3
"""
Load IMF World Economic Outlook (WEO) forecasts.

Downloads macro projections from IMF WEO SDMX/JSON API and inserts
into macro_forecasts table with vintage tracking.

Key indicators:
    NGDP_RPCH   — Real GDP Growth (%)
    PCPIPCH     — Inflation, avg CPI (%)
    LUR         — Unemployment rate (%)
    GGXCNL_NGDP — Govt Net Lending/Borrowing (% GDP, fiscal balance proxy)
    GGXWDG_NGDP — Government Debt (% GDP)
    BCA_NGDPD   — Current Account Balance (% GDP)

Usage:
    python scripts/load_imf_weo.py                    # Load latest WEO
    python scripts/load_imf_weo.py --dry-run           # Preview without saving
    python scripts/load_imf_weo.py --vintage April2024 # Specific edition
"""

import sys
import time
import argparse
from pathlib import Path
from datetime import datetime

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / '.env')

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.storage.database import DatabaseManager
from src.utils.logger import get_logger

logger = get_logger(__name__)

# IMF WEO API — SDMX format
IMF_WEO_API = "https://www.imf.org/external/datamapper/api/v1"

# Key macro indicators to fetch
WEO_INDICATORS = {
    'NGDP_RPCH': {'name': 'Real GDP Growth', 'unit': '%'},
    'PCPIPCH': {'name': 'Inflation (avg CPI)', 'unit': '%'},
    'LUR': {'name': 'Unemployment Rate', 'unit': '%'},
    'GGXWDG_NGDP': {'name': 'Government Debt', 'unit': '% GDP'},
    'BCA_NGDPD': {'name': 'Current Account Balance', 'unit': '% GDP'},
    'GGXCNL_NGDP': {'name': 'Govt Net Lending/Borrowing (fiscal balance)', 'unit': '% GDP'},
}


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
        requests.exceptions.HTTPError,
    )),
)
def fetch_weo_indicator(indicator_code: str) -> dict:
    """Fetch a single WEO indicator for all countries.

    Returns: {iso3: {year: value, ...}, ...}
    """
    url = f"{IMF_WEO_API}/{indicator_code}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    # IMF API returns: {"values": {"NGDP_RPCH": {"USA": {"2023": "2.5", ...}, ...}}}
    values = data.get("values", {}).get(indicator_code, {})
    return values


INSERT_SQL = """
    INSERT INTO macro_forecasts (iso3, indicator_code, indicator_name, year, value, unit, vintage)
    VALUES (%(iso3)s, %(indicator_code)s, %(indicator_name)s, %(year)s, %(value)s, %(unit)s, %(vintage)s)
    ON CONFLICT (iso3, indicator_code, year, vintage) DO UPDATE SET
        value = EXCLUDED.value,
        indicator_name = EXCLUDED.indicator_name,
        last_updated = NOW()
"""


def main():
    parser = argparse.ArgumentParser(description="Load IMF WEO macro forecasts")
    parser.add_argument('--dry-run', action='store_true', help='Preview without saving')
    parser.add_argument('--vintage', type=str, default=None,
                        help='WEO vintage label (e.g. April2024)')
    args = parser.parse_args()

    vintage = args.vintage or f"auto_{datetime.now().strftime('%Y%m')}"

    logger.info("=" * 80)
    logger.info("IMF WEO MACRO FORECASTS LOADER")
    logger.info("=" * 80)
    logger.info(f"Vintage: {vintage}")

    total_saved = 0
    total_errors = 0

    db = None if args.dry_run else DatabaseManager()

    for indicator_code, meta in WEO_INDICATORS.items():
        logger.info(f"\n  → {indicator_code}: {meta['name']}")
        try:
            country_data = fetch_weo_indicator(indicator_code)
            entries = 0
            for iso3, year_values in country_data.items():
                if not iso3 or len(iso3) != 3:
                    continue
                for year_str, value_str in year_values.items():
                    try:
                        year = int(year_str)
                        value = float(value_str) if value_str else None
                    except (ValueError, TypeError):
                        continue

                    if value is None or year < 2020 or year > 2030:
                        continue

                    entries += 1

                    if not args.dry_run:
                        params = {
                            'iso3': iso3.upper(),
                            'indicator_code': indicator_code,
                            'indicator_name': meta['name'],
                            'year': year,
                            'value': value,
                            'unit': meta['unit'],
                            'vintage': vintage,
                        }
                        with db.get_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute(INSERT_SQL, params)
                            conn.commit()
                        total_saved += 1

            logger.info(f"    ✓ {entries} data points")
        except Exception as e:
            logger.warning(f"    ✗ Failed: {e}")
            total_errors += 1

        time.sleep(0.5)  # Rate limiting between indicators

    if args.dry_run:
        logger.info("\n[DRY RUN] No data saved")
        return 0

    logger.info(f"\n  ✓ Total saved: {total_saved}")
    logger.info(f"  ✗ Errors: {total_errors}")

    if db:
        db.close()

    logger.info("✓ IMF WEO loading complete!")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        logger.info("\n\nInterrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        sys.exit(1)
