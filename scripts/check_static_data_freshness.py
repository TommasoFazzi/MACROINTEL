#!/usr/bin/env python3
"""
Check freshness of static/reference data in the DB vs upstream sources.

Performs lightweight upstream checks (no bulk downloads) and compares
against what's currently loaded in the database.

Usage:
    python scripts/check_static_data_freshness.py
    python scripts/check_static_data_freshness.py --no-db   # upstream only (no DB access)
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime, timezone

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / '.env')

import os
import requests

# ── ANSI colours ──────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def ok(msg):    return f"{GREEN}✓{RESET} {msg}"
def warn(msg):  return f"{YELLOW}~{RESET} {msg}"
def stale(msg): return f"{RED}↑{RESET} {msg}"
def info(msg):  return f"{CYAN}·{RESET} {msg}"

# ── DB helpers ────────────────────────────────────────────────────────────────

def _db_query(db, sql, params=None):
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def db_imf_state(db):
    rows = _db_query(db, """
        SELECT vintage, COUNT(DISTINCT iso3), MIN(year), MAX(year), COUNT(*)
        FROM macro_forecasts
        GROUP BY vintage
        ORDER BY vintage DESC
        LIMIT 5
    """)
    return rows  # [(vintage, n_countries, min_year, max_year, total_rows)]


def db_worldbank_state(db):
    rows = _db_query(db, """
        SELECT COUNT(*), MAX(last_updated)
        FROM country_profiles
    """)
    return rows[0] if rows else (0, None)


def db_ucdp_state(db):
    rows = _db_query(db, """
        SELECT COUNT(*), MIN(event_date), MAX(event_date)
        FROM conflict_events
    """)
    return rows[0] if rows else (0, None, None)


def db_opensanctions_state(db):
    rows = _db_query(db, """
        SELECT COUNT(*), MAX(last_updated)
        FROM sanctions_registry
    """)
    return rows[0] if rows else (0, None)


# ── Upstream checks (lightweight, no bulk download) ───────────────────────────

def check_imf_upstream():
    """
    Derive the current WEO edition from calendar (no API needed — IMF releases
    April and October each year). Then make one tiny API call to confirm data exists.
    """
    now = datetime.now()
    year = now.year
    month = now.month

    if month >= 10:
        expected_vintage = f"October{year}"
    elif month >= 4:
        expected_vintage = f"April{year}"
    else:
        expected_vintage = f"October{year - 1}"

    # Lightweight probe: fetch 1 country for 1 indicator to confirm API is live
    try:
        url = "https://www.imf.org/external/datamapper/api/v1/NGDP_RPCH/ITA"
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        years_available = sorted(data.get("values", {}).get("NGDP_RPCH", {}).get("ITA", {}).keys())
        latest_year = years_available[-1] if years_available else "?"
        return {"vintage": expected_vintage, "latest_year": latest_year, "reachable": True}
    except Exception as e:
        return {"vintage": expected_vintage, "latest_year": "?", "reachable": False, "error": str(e)}


def check_worldbank_upstream():
    """
    Fetch one page (1 row) from WB API to read the lastupdated field.
    """
    try:
        url = "https://api.worldbank.org/v2/country/all/indicator/NY.GDP.MKTP.KD.ZG"
        resp = requests.get(url, params={"format": "json", "per_page": 1}, timeout=15)
        resp.raise_for_status()
        meta = resp.json()[0]  # first element is pagination + metadata
        last_updated = meta.get("lastupdated", "?")
        total_pages = meta.get("pages", "?")
        total_records = meta.get("total", "?")
        return {
            "last_updated": last_updated,
            "total_records": total_records,
            "reachable": True,
        }
    except Exception as e:
        return {"last_updated": "?", "reachable": False, "error": str(e)}


def check_ucdp_upstream():
    """
    Query UCDP candidate API with pagesize=1 ordered by date descending to get
    the most recent event date — no bulk download.
    """
    token = os.getenv("UCDP_API_TOKEN", "").strip()
    headers = {"x-ucdp-access-token": token} if token else {}

    results = {}
    for label, base_url in [
        ("stable",    "https://ucdpapi.pcr.uu.se/api/gedevents/25.1"),
        ("candidate", "https://ucdpapi.pcr.uu.se/api/gedevents/26.0.2"),
    ]:
        try:
            resp = requests.get(
                base_url,
                params={"pagesize": 1, "page": 1},
                headers=headers,
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            total = data.get("TotalCount", "?")
            # Fetch latest event: use StartDate near today to get most recent page
            results[label] = {"total": total, "reachable": True}
        except Exception as e:
            results[label] = {"total": "?", "reachable": False, "error": str(e)}

    # Probe latest event date from candidate (most recent data)
    try:
        resp = requests.get(
            "https://ucdpapi.pcr.uu.se/api/gedevents/26.0.2",
            params={"pagesize": 1, "page": results["candidate"].get("total", 1)},
            headers=headers,
            timeout=20,
        )
        resp.raise_for_status()
        events = resp.json().get("Result", [])
        if events:
            results["candidate"]["latest_event_date"] = events[-1].get("date_end", "?")
    except Exception:
        pass

    return results


def check_opensanctions_upstream():
    """
    HTTP HEAD on the export file to read Last-Modified without downloading.
    Also check the index JSON for dataset metadata.
    """
    url = "https://data.opensanctions.org/datasets/latest/default/entities.ftm.json"
    try:
        resp = requests.head(url, timeout=15, allow_redirects=True)
        last_modified = resp.headers.get("Last-Modified", "?")
        content_length = resp.headers.get("Content-Length")
        size_gb = round(int(content_length) / 1e9, 2) if content_length else "?"
        return {
            "last_modified": last_modified,
            "size_gb": size_gb,
            "reachable": True,
        }
    except Exception as e:
        return {"last_modified": "?", "reachable": False, "error": str(e)}


# ── Rendering ─────────────────────────────────────────────────────────────────

def _section(title):
    print(f"\n{BOLD}{'─' * 60}{RESET}")
    print(f"{BOLD}  {title}{RESET}")
    print(f"{BOLD}{'─' * 60}{RESET}")


def render_imf(db_rows, upstream):
    _section("IMF World Economic Outlook (macro_forecasts)")

    if db_rows:
        print(info("DB vintages loaded:"))
        for vintage, n_countries, min_yr, max_yr, total in db_rows:
            print(f"    {vintage:>15}  |  {n_countries} countries  |  years {min_yr}–{max_yr}  |  {total} rows")
        db_latest = db_rows[0][0]  # most recent vintage
    else:
        print(warn("  No rows in macro_forecasts"))
        db_latest = None

    print()
    if upstream["reachable"]:
        expected = upstream["vintage"]
        api_year = upstream["latest_year"]
        print(info(f"Upstream: expected vintage = {BOLD}{expected}{RESET}  |  latest data year in API = {api_year}"))
        if db_latest and db_latest == expected:
            print(ok(f"DB is up to date ({db_latest})"))
        elif db_latest:
            print(stale(f"UPDATE AVAILABLE — DB has {db_latest}, upstream expects {expected}"))
        else:
            print(stale(f"No data in DB — run load_imf_weo.py"))
    else:
        print(warn(f"IMF API unreachable: {upstream.get('error', '?')}"))


def render_worldbank(db_row, upstream):
    _section("World Bank (country_profiles)")

    n_countries, db_updated = db_row
    if n_countries:
        db_date = db_updated.date() if hasattr(db_updated, 'date') else str(db_updated)
        print(info(f"DB: {n_countries} countries, last updated {db_date}"))
    else:
        print(warn("  No rows in country_profiles"))
        db_date = None

    print()
    if upstream["reachable"]:
        upstream_date = upstream["last_updated"]
        print(info(f"Upstream last updated: {BOLD}{upstream_date}{RESET}"))
        if db_date and upstream_date != "?":
            # Parse upstream date (format: "YYYY-MM-DD")
            try:
                up_dt = datetime.strptime(upstream_date, "%Y-%m-%d").date()
                db_dt = db_updated.date() if hasattr(db_updated, 'date') else None
                if db_dt and up_dt > db_dt:
                    print(stale(f"UPDATE AVAILABLE — upstream {upstream_date} > DB {db_dt}"))
                elif db_dt:
                    print(ok(f"DB is up to date (upstream: {upstream_date}, DB: {db_dt})"))
            except ValueError:
                print(info(f"Cannot compare dates automatically — check manually"))
        elif not db_date:
            print(stale("No data in DB — run load_world_bank.py"))
    else:
        print(warn(f"World Bank API unreachable: {upstream.get('error', '?')}"))


def render_ucdp(db_row, upstream):
    _section("UCDP Conflict Events (conflict_events)")

    n_events, min_date, max_date = db_row
    if n_events:
        print(info(f"DB: {n_events:,} events, range {min_date} → {max_date}"))
    else:
        print(warn("  No rows in conflict_events"))

    print()
    for label in ("stable", "candidate"):
        u = upstream.get(label, {})
        if u.get("reachable"):
            total = u.get("total", "?")
            latest = u.get("latest_event_date", "")
            latest_str = f"  |  latest event: {latest}" if latest else ""
            print(info(f"Upstream {label}: {total:,} total events{latest_str}" if isinstance(total, int) else f"Upstream {label}: {total} events{latest_str}"))
            if n_events and isinstance(total, int):
                if total > n_events:
                    print(stale(f"  UPDATE AVAILABLE ({label}) — upstream has {total:,}, DB has {n_events:,}"))
                else:
                    print(ok(f"  DB >= upstream count for {label} dataset"))
        else:
            print(warn(f"  UCDP {label} API unreachable: {u.get('error', '?')}"))


def render_opensanctions(db_row, upstream):
    _section("OpenSanctions (sanctions_registry)")

    n_entities, db_updated = db_row
    if n_entities:
        db_date = db_updated.date() if hasattr(db_updated, 'date') else str(db_updated)
        print(info(f"DB: {n_entities:,} entities, last loaded {db_date}"))
    else:
        print(warn("  No rows in sanctions_registry"))
        db_date = None

    print()
    if upstream["reachable"]:
        last_mod = upstream["last_modified"]
        size = upstream["size_gb"]
        print(info(f"Upstream Last-Modified: {BOLD}{last_mod}{RESET}  |  dump size: {size} GB"))
        if db_date and last_mod != "?":
            try:
                # HTTP Last-Modified format: "Thu, 07 May 2026 14:32:00 GMT"
                up_dt = datetime.strptime(last_mod, "%a, %d %b %Y %H:%M:%S %Z").date()
                db_dt = db_updated.date() if hasattr(db_updated, 'date') else None
                if db_dt and up_dt > db_dt:
                    print(stale(f"UPDATE AVAILABLE — upstream {up_dt} > DB {db_dt}"))
                    print(info(f"  Download first: curl -fsSLO https://data.opensanctions.org/datasets/latest/default/entities.ftm.json"))
                elif db_dt:
                    print(ok(f"DB is up to date (upstream: {up_dt}, DB: {db_dt})"))
            except ValueError:
                print(info(f"Last-Modified: {last_mod} — compare manually with DB date {db_date}"))
        elif not db_date:
            print(stale("No data in DB — download dump then run load_opensanctions.py"))
            print(info(f"  Download: curl -fsSLO https://data.opensanctions.org/datasets/latest/default/entities.ftm.json"))
    else:
        print(warn(f"OpenSanctions unreachable: {upstream.get('error', '?')}"))


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Check freshness of static DB data vs upstream")
    parser.add_argument("--no-db", action="store_true", help="Skip DB queries (upstream check only)")
    args = parser.parse_args()

    print(f"\n{BOLD}Static Data Freshness Check — {datetime.now().strftime('%Y-%m-%d %H:%M')}{RESET}")

    db = None
    if not args.no_db:
        try:
            from src.storage.database import DatabaseManager
            db = DatabaseManager()
        except Exception as e:
            print(f"\n{RED}Cannot connect to DB: {e}{RESET}")
            print("Run with --no-db to skip DB queries.\n")
            sys.exit(1)

    # Upstream checks (parallel-ish via sequential calls — fast enough)
    print(f"\n{info('Querying upstream APIs (no bulk downloads)...')}")

    imf_up   = check_imf_upstream()
    wb_up    = check_worldbank_upstream()
    ucdp_up  = check_ucdp_upstream()
    osanc_up = check_opensanctions_upstream()

    # DB state
    imf_db   = db_imf_state(db)        if db else []
    wb_db    = db_worldbank_state(db)   if db else (0, None)
    ucdp_db  = db_ucdp_state(db)       if db else (0, None, None)
    osanc_db = db_opensanctions_state(db) if db else (0, None)

    # Render
    render_imf(imf_db, imf_up)
    render_worldbank(wb_db, wb_up)
    render_ucdp(ucdp_db, ucdp_up)
    render_opensanctions(osanc_db, osanc_up)

    print(f"\n{BOLD}{'─' * 60}{RESET}")
    print(f"{GREEN}✓{RESET} = up to date   "
          f"{YELLOW}~{RESET} = warning/unknown   "
          f"{RED}↑{RESET} = update available\n")

    if db:
        db.close()


if __name__ == "__main__":
    main()
