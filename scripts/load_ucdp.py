#!/usr/bin/env python3
"""
Load UCDP GED conflict events from the UCDP API.

Downloads UCDP Georeferenced Event Dataset (GED) events via paginated API
and inserts into the conflict_events table with PostGIS Point geometries.

Uses tenacity exponential backoff for API resilience.

Usage:
    python scripts/load_ucdp.py                 # Load all events
    python scripts/load_ucdp.py --dry-run        # Count events without saving
    python scripts/load_ucdp.py --year 2024      # Filter by year
    python scripts/load_ucdp.py --limit 5000     # Limit total events
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

import os
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.storage.database import DatabaseManager
from src.utils.logger import get_logger

logger = get_logger(__name__)

UCDP_API = "https://ucdpapi.pcr.uu.se/api/gedevents/24.1"


def _get_headers() -> dict:
    """Build request headers. UCDP now requires an API token (free registration).
    Set UCDP_API_TOKEN in .env — obtain at: https://ucdp.uu.se/apidocs/
    """
    token = os.environ.get("UCDP_API_TOKEN", "").strip()
    if not token:
        raise EnvironmentError(
            "UCDP_API_TOKEN not set. Register at https://ucdp.uu.se/apidocs/ "
            "and add UCDP_API_TOKEN=<your_token> to .env"
        )
    return {"x-ucdp-access-token": token}


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    retry=retry_if_exception_type((
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
        requests.exceptions.HTTPError,
    )),
)
def _fetch_page(page: int, page_size: int, year: int = None) -> dict:
    """Fetch single UCDP page with exponential backoff on 429/502/504."""
    params = {"pagesize": page_size, "page": page}
    if year:
        params["Year"] = year
    resp = requests.get(UCDP_API, params=params, headers=_get_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_ucdp_events(page_size=1000, year=None, max_events=None):
    """Stream UCDP GED events via paginated API. Requires UCDP_API_TOKEN in .env."""
    page = 0
    total_yielded = 0
    while True:
        data = _fetch_page(page, page_size, year)
        events = data.get("Result", [])
        if not events:
            break

        if max_events and total_yielded + len(events) > max_events:
            events = events[:max_events - total_yielded]
            yield events
            break

        yield events
        total_yielded += len(events)
        page += 1
        time.sleep(0.3)  # Base rate limiting between successful requests


def transform_event(raw: dict) -> dict:
    """Transform UCDP API event to conflict_events row."""
    return {
        'event_date': raw.get('date_start'),
        'event_type': str(raw.get('type_of_violence', '')),
        'country': raw.get('country'),
        'location': raw.get('where_description', raw.get('adm_1', '')),
        'latitude': raw.get('latitude'),
        'longitude': raw.get('longitude'),
        'actor1': raw.get('side_a'),
        'actor2': raw.get('side_b', ''),
        'fatalities': raw.get('best'),
        'fatalities_low': raw.get('low'),
        'fatalities_high': raw.get('high'),
        'notes': '; '.join(filter(None, [
            raw.get('source_article', ''),
            raw.get('source_office', ''),
        ])),
        'data_source_id': str(raw.get('id', '')),
    }


INSERT_SQL = """
    INSERT INTO conflict_events (event_date, event_type, country, location, geom,
        actor1, actor2, fatalities, fatalities_low, fatalities_high, notes, data_source_id)
    VALUES (%(event_date)s, %(event_type)s, %(country)s, %(location)s,
        CASE WHEN %(latitude)s IS NOT NULL AND %(longitude)s IS NOT NULL
             THEN ST_SetSRID(ST_Point(%(longitude)s, %(latitude)s), 4326)
             ELSE NULL END,
        %(actor1)s, %(actor2)s, %(fatalities)s, %(fatalities_low)s, %(fatalities_high)s,
        %(notes)s, %(data_source_id)s)
    ON CONFLICT (data_source_id) DO UPDATE SET
        fatalities = EXCLUDED.fatalities,
        fatalities_low = EXCLUDED.fatalities_low,
        fatalities_high = EXCLUDED.fatalities_high,
        notes = EXCLUDED.notes
"""


def main():
    parser = argparse.ArgumentParser(description="Load UCDP GED conflict events")
    parser.add_argument('--dry-run', action='store_true', help='Count events without saving')
    parser.add_argument('--year', type=int, help='Filter by specific year')
    parser.add_argument('--limit', type=int, help='Maximum events to fetch')
    args = parser.parse_args()

    logger.info("=" * 80)
    logger.info("UCDP GED CONFLICT EVENTS LOADER")
    logger.info("=" * 80)

    if args.year:
        logger.info(f"Filtering by year: {args.year}")
    if args.limit:
        logger.info(f"Maximum events: {args.limit}")

    total_events = 0
    total_pages = 0
    saved = 0
    errors = 0

    db = None if args.dry_run else DatabaseManager()

    for batch in fetch_ucdp_events(page_size=1000, year=args.year, max_events=args.limit):
        total_pages += 1
        total_events += len(batch)
        logger.info(f"  Page {total_pages}: {len(batch)} events (total: {total_events})")

        if args.dry_run:
            continue

        # Batch insert
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                for raw_event in batch:
                    try:
                        event = transform_event(raw_event)
                        cur.execute(INSERT_SQL, event)
                        saved += 1
                    except Exception as e:
                        errors += 1
                        if errors <= 5:
                            logger.warning(f"  ✗ Event insert error: {e}")
                conn.commit()

    if args.dry_run:
        logger.info(f"\n[DRY RUN] Total events: {total_events} across {total_pages} pages")
        return 0

    logger.info(f"\n  ✓ Saved: {saved}, Errors: {errors}, Pages: {total_pages}")

    if db:
        db.close()

    logger.info("✓ UCDP GED loading complete!")
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
