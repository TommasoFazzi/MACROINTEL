#!/usr/bin/env python3
"""
Load UCDP GED conflict events from the UCDP API.

Downloads UCDP Georeferenced Event Dataset (GED) events via paginated API
and inserts into the conflict_events table with PostGIS Point geometries.

Uses tenacity exponential backoff for API resilience.

Dataset modes (verified against https://ucdp.uu.se/apidocs/ and the Candidate
codebook v1.5, 2026-10):
  - Stable GED (default): UCDP_GED_API — verified annual release, 1989 through
    2025-12-31 (v26.1).
  - GED Candidate (--candidate / --candidate-version): provisional data with at
    most a month's lag. Three kinds of version exist:
      * monthly  `26.0.N`  — events whose date_start OR date_end falls inside
        month N ONLY (a delta, NOT cumulative). The same event can appear in two
        consecutive monthly releases. To cover a period you must load every
        monthly version in it.
      * quarterly/combined `26.01.26.03`, `26.01.26.06`, … — cumulative from
        January up to the given month (3/6/9/12 months). One load replaces the
        corresponding monthly ones.
      * Not every candidate event survives into the final annual GED, and
        fatality counts get revised — rows are tagged source='UCDP_GED_CANDIDATE'.

Usage:
    python scripts/load_ucdp.py                              # Full stable load (1989-2025)
    python scripts/load_ucdp.py --dry-run                    # Count without saving
    python scripts/load_ucdp.py --limit 5000                 # Limit events fetched
    python scripts/load_ucdp.py --start-date 2025-01-01 --end-date 2025-12-31  # one year
    python scripts/load_ucdp.py --candidate-version 26.01.26.06   # Jan-Jun 2026 (cumulative)
    python scripts/load_ucdp.py --candidate-version 26.0.7        # July 2026 only
    python scripts/load_ucdp.py --candidate                  # default monthly (UCDP_CANDIDATE_API)

NOTE: StartDate/EndDate filter on the date_end field (YYYY-MM-DD) and work on
all GED versions. Unrecognised params are silently IGNORED by the UCDP API, not
rejected — e.g. `Year=2025` returns the unfiltered total with a 200, so always
check the returned sample dates before trusting that a filter applied.
Deduplication is handled via ON CONFLICT on data_source_id.
"""

import re
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

# Stable GED: verified data, updated annually. v26.1 covers 1989–2025-12-31
# (417,968 events). Each annual release is a full re-issue that also *revises*
# prior years; INSERT_SQL upserts every mutable column ON CONFLICT, so re-running
# a load against a newer version pulls in corrections (fatalities AND recoded
# actors). Candidate rows carry provisional actor codes `XXX<id>` (~15-30% of
# recent months) that UCDP resolves later; this refresh is what picks that up.
# Rows UCDP later DROPS are never deleted by an upsert (a provisional event that
# vanishes from a newer candidate stays in the table until removed by hand).
UCDP_GED_API = "https://ucdpapi.pcr.uu.se/api/gedevents/26.1"

# GED Candidate default: latest monthly delta (see module docstring — monthly
# versions are NOT cumulative; use --candidate-version for other periods).
UCDP_API_BASE = "https://ucdpapi.pcr.uu.se/api/gedevents/"
UCDP_CANDIDATE_API = UCDP_API_BASE + "26.0.8"

# Accepted --candidate-version shapes: monthly `26.0.8`, combined `26.01.26.06`.
CANDIDATE_VERSION_RE = re.compile(r"^\d{2}\.0\.\d{1,2}$|^\d{2}\.\d{2}\.\d{2}\.\d{2}$")


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
    StartDate/EndDate filter on the date_end field (YYYY-MM-DD) and work on all GED versions.
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


def transform_event(raw: dict, source: str = 'UCDP_GED') -> dict:
    """Transform UCDP API event to conflict_events row.

    'source' records the dataset vintage ('UCDP_GED' stable vs
    'UCDP_GED_CANDIDATE' provisional) so downstream consumers can tell verified
    rows from provisional ones — provisional fatality counts get revised.
    """
    return {
        'source': source,
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
        actor1, actor2, fatalities, fatalities_low, fatalities_high, notes,
        source, data_source_id)
    VALUES (%(event_date)s, %(event_type)s, %(country)s, %(location)s,
        CASE WHEN %(latitude)s IS NOT NULL AND %(longitude)s IS NOT NULL
             THEN ST_SetSRID(ST_Point(%(longitude)s, %(latitude)s), 4326)
             ELSE NULL END,
        %(actor1)s, %(actor2)s, %(fatalities)s, %(fatalities_low)s, %(fatalities_high)s,
        %(notes)s, %(source)s, %(data_source_id)s)
    ON CONFLICT (data_source_id) DO UPDATE SET
        event_date = EXCLUDED.event_date,
        event_type = EXCLUDED.event_type,
        country = EXCLUDED.country,
        location = EXCLUDED.location,
        geom = EXCLUDED.geom,
        actor1 = EXCLUDED.actor1,
        actor2 = EXCLUDED.actor2,
        fatalities = EXCLUDED.fatalities,
        fatalities_low = EXCLUDED.fatalities_low,
        fatalities_high = EXCLUDED.fatalities_high,
        notes = EXCLUDED.notes,
        source = EXCLUDED.source
"""


def main():
    parser = argparse.ArgumentParser(description="Load UCDP GED conflict events")
    parser.add_argument('--dry-run', action='store_true', help='Count events without saving')
    parser.add_argument('--candidate', action='store_true',
                        help='Use GED Candidate endpoint (provisional, more recent data)')
    parser.add_argument('--candidate-version', type=str, metavar='VER',
                        help='Candidate version to load, e.g. 26.0.7 (monthly) or '
                             '26.01.26.06 (Jan-Jun, cumulative). Implies --candidate.')
    parser.add_argument('--start-date', type=str, metavar='YYYY-MM-DD',
                        help='Filter on date_end, inclusive (all versions)')
    parser.add_argument('--end-date', type=str, metavar='YYYY-MM-DD',
                        help='Filter on date_end, inclusive (all versions)')
    parser.add_argument('--limit', type=int, help='Maximum events to fetch')
    args = parser.parse_args()

    if args.candidate_version:
        if not CANDIDATE_VERSION_RE.match(args.candidate_version):
            parser.error(f"invalid --candidate-version {args.candidate_version!r} "
                         "(expected e.g. 26.0.7 or 26.01.26.06)")
        args.candidate = True
        base_url = UCDP_API_BASE + args.candidate_version
    else:
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

    # Tag rows with the dataset vintage so provisional (revisable) events stay
    # distinguishable from verified ones in conflict_events.source.
    source_tag = 'UCDP_GED_CANDIDATE' if args.candidate else 'UCDP_GED'

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
                        event = transform_event(raw_event, source=source_tag)
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
