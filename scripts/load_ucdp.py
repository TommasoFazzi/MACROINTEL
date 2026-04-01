#!/usr/bin/env python3
"""
Load UCDP GED conflict events from the UCDP API.

Downloads UCDP Georeferenced Event Dataset (GED) events via paginated API
and inserts into the conflict_events table with PostGIS Point geometries.

Uses tenacity exponential backoff for API resilience.

Two dataset modes:
  - Stable GED (default): UCDP_GED_API — verified data, coverage through 2024-12-31
  - GED Candidate (--candidate): UCDP_CANDIDATE_API — provisional monthly data,
    coverage through ~Feb 2026 (version 26.0.2). Supports StartDate/EndDate filtering.

Usage:
    python scripts/load_ucdp.py                              # Full stable load (1989-2024)
    python scripts/load_ucdp.py --dry-run                    # Count without saving
    python scripts/load_ucdp.py --limit 5000                 # Limit events fetched
    python scripts/load_ucdp.py --candidate                  # Full candidate load
    python scripts/load_ucdp.py --candidate --start-date 2025-01-01   # 2025+ only
    python scripts/load_ucdp.py --candidate --start-date 2025-01-01 --end-date 2025-12-31

NOTE: The stable GED endpoint ignores StartDate/EndDate/Year filters — it always
returns all events. Use --candidate for date-filtered incremental updates.
Deduplication is handled via ON CONFLICT on data_source_id.
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

# Stable GED: verified data, updated annually. v25.1 covers 1989–2024-12-31.
UCDP_GED_API = "https://ucdpapi.pcr.uu.se/api/gedevents/25.1"

# GED Candidate: provisional monthly data. v26.0.2 covers up to ~Feb 2026.
# Supports StartDate/EndDate filters for incremental updates.
UCDP_CANDIDATE_API = "https://ucdpapi.pcr.uu.se/api/gedeventscandidate/26.0.2"


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
def _fetch_page(
    base_url: str,
    page_size: int = 1000,
    start_date: str = None,
    end_date: str = None,
    url: str = None,
) -> dict:
    """Fetch single UCDP page with exponential backoff on 429/502/504.
    If 'url' is provided, it takes precedence (used for HATEOAS NextPageUrl).
    StartDate/EndDate only work on the Candidate endpoint.
    """
    headers = _get_headers()

    if url:
        resp = requests.get(url, headers=headers, timeout=30)
    else:
        params = {"pagesize": page_size}
        if start_date:
            params["StartDate"] = start_date
        if end_date:
            params["EndDate"] = end_date
        resp = requests.get(base_url, params=params, headers=headers, timeout=30)

    if resp.status_code in (401, 403):
        logger.error("❌ Authentication failed (401/403). Check UCDP_API_TOKEN in .env")
        resp.raise_for_status()
    elif resp.status_code == 429:
        logger.warning("⚠️ Rate limit reached (5,000 requests/day). Cooling down...")
        time.sleep(60)
        resp.raise_for_status()

    resp.raise_for_status()
    return resp.json()


def fetch_ucdp_events(
    base_url: str,
    page_size: int = 1000,
    start_date: str = None,
    end_date: str = None,
    max_events: int = None,
):
    """Stream UCDP GED events via paginated API. Requires UCDP_API_TOKEN in .env.
    Uses NextPageUrl HATEOAS for pagination (compliant with 2026 API spec).
    StartDate/EndDate filtering only effective on the Candidate endpoint.
    """
    next_url = None
    total_yielded = 0
    request_count = 0
    MAX_DAILY_REQUESTS = 5000  # UCDP 2026 strict limit

    while True:
        if request_count >= MAX_DAILY_REQUESTS:
            logger.error(f"❌ Stop: Daily limit of {MAX_DAILY_REQUESTS} requests reached.")
            break

        data = _fetch_page(
            base_url=base_url,
            page_size=page_size,
            start_date=start_date,
            end_date=end_date,
            url=next_url,
        )
        request_count += 1

        events = data.get("Result", [])
        if not events:
            break

        if max_events and total_yielded + len(events) > max_events:
            events = events[:max_events - total_yielded]
            yield events
            break

        yield events
        total_yielded += len(events)

        next_url = data.get("NextPageUrl")
        if not next_url:
            break

        time.sleep(0.3)  # Base rate limiting


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
    parser.add_argument('--candidate', action='store_true',
                        help='Use GED Candidate endpoint (provisional, more recent data)')
    parser.add_argument('--start-date', type=str, metavar='YYYY-MM-DD',
                        help='Filter by start date (Candidate endpoint only)')
    parser.add_argument('--end-date', type=str, metavar='YYYY-MM-DD',
                        help='Filter by end date (Candidate endpoint only)')
    parser.add_argument('--limit', type=int, help='Maximum events to fetch')
    args = parser.parse_args()

    base_url = UCDP_CANDIDATE_API if args.candidate else UCDP_GED_API

    logger.info("=" * 80)
    logger.info("UCDP GED CONFLICT EVENTS LOADER")
    logger.info("=" * 80)
    logger.info(f"Endpoint: {'GED Candidate (provisional)' if args.candidate else 'GED Stable'}")
    logger.info(f"URL: {base_url}")
    if args.start_date:
        logger.info(f"Start date: {args.start_date}")
    if args.end_date:
        logger.info(f"End date: {args.end_date}")
    if args.limit:
        logger.info(f"Maximum events: {args.limit}")

    total_events = 0
    total_pages = 0
    saved = 0
    errors = 0

    db = None if args.dry_run else DatabaseManager()

    for batch in fetch_ucdp_events(
        base_url=base_url,
        page_size=1000,
        start_date=args.start_date,
        end_date=args.end_date,
        max_events=args.limit,
    ):
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
