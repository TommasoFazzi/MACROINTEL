#!/usr/bin/env python3
"""
GeoNames Gazetteer Loader

Loads the GeoNames allCountries.txt dump into the geo_gazetteer PostgreSQL table,
merging alternate names from alternateNames.txt into a TEXT[] column for multilingual
lookup.

Usage:
    # Download data first:
    #   wget https://download.geonames.org/export/dump/allCountries.zip
    #   wget https://download.geonames.org/export/dump/alternateNames.zip
    #   unzip allCountries.zip && unzip alternateNames.zip

    python scripts/load_geonames.py --countries /path/to/allCountries.txt
    python scripts/load_geonames.py --countries allCountries.txt --altnames alternateNames.txt
    python scripts/load_geonames.py --countries allCountries.txt --dry-run

Notes:
    - Migration 021_geo_gazetteer.sql must be applied first
    - Filters to ~2-3M rows (feature classes A/P/H/L only)
    - Inserts in batches of 5000 with ON CONFLICT DO NOTHING (idempotent)
    - Estimated runtime: ~10-15 minutes for full dataset
"""

import sys
import os
import argparse
import time
from pathlib import Path
from collections import defaultdict
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env", override=False)

from psycopg2.extras import execute_values
from src.storage.database import DatabaseManager
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Feature classes and codes to import (geopolitically relevant subset)
FEATURE_FILTER: dict[str, set[str]] = {
    'A': {'PCLI', 'PCLD', 'PCLF', 'PCLS', 'ADM1', 'ADM2', 'ADM3'},
    'P': {'PPLC', 'PPLA', 'PPLA2', 'PPLA3', 'PPL', 'PPLG', 'PPLS'},
    'H': {'SEA', 'OCN', 'STR', 'STRT', 'LK', 'LKS', 'RSV', 'BAY', 'GULF', 'CHAN', 'COVE'},
    'L': {'AREA', 'RGN', 'RGNH', 'CONT'},
}

# GeoNames allCountries.txt column indices
COL_GEONAME_ID = 0
COL_NAME = 1
COL_ASCII_NAME = 2
COL_ALTERNATE_NAMES = 3   # comma-separated list (we parse via alternateNames.txt instead)
COL_LATITUDE = 4
COL_LONGITUDE = 5
COL_FEATURE_CLASS = 6
COL_FEATURE_CODE = 7
COL_COUNTRY_CODE = 8
COL_POPULATION = 14
COL_TIMEZONE = 17

BATCH_SIZE = 5000


def load_alternate_names(altnames_path: Path) -> dict[int, list[str]]:
    """
    Parse alternateNames.txt and build a dict {geoname_id: [alt_name, ...]}.

    Only collects names that are:
    - Language: English ('en'), or no language code (transliterations/short names)
    - Not a link (http) or IATA/ICAO code
    """
    logger.info(f"Loading alternate names from {altnames_path}...")
    alt_map: dict[int, list[str]] = defaultdict(list)

    # Column indices for alternateNames.txt:
    # alternateNameId, geonameid, isolanguage, alternate name, isPreferredName, isShortName, isColloquial, isHistoric
    USEFUL_LANGS = {'en', 'abbr', ''}

    with open(altnames_path, encoding='utf-8', errors='replace') as f:
        for line_num, line in enumerate(f, 1):
            if line_num % 1_000_000 == 0:
                logger.info(f"  Processed {line_num:,} alternate name lines...")
            parts = line.rstrip('\n').split('\t')
            if len(parts) < 4:
                continue
            try:
                geoname_id = int(parts[1])
                iso_lang = parts[2].lower()
                alt_name = parts[3].strip()
            except (ValueError, IndexError):
                continue

            # Skip links, IATA/ICAO codes, Wikipedia links
            if not alt_name or alt_name.startswith('http') or len(alt_name) > 100:
                continue
            if iso_lang not in USEFUL_LANGS:
                continue

            alt_map[geoname_id].append(alt_name)

    logger.info(f"Loaded alternate names for {len(alt_map):,} geoname IDs")
    return alt_map


def load_countries(
    countries_path: Path,
    alt_map: Optional[dict[int, list[str]]],
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Parse allCountries.txt, filter by feature class/code, and batch-insert into geo_gazetteer.

    Returns stats dict.
    """
    db = DatabaseManager()
    stats = {'rows_read': 0, 'rows_filtered': 0, 'rows_inserted': 0, 'batches': 0}

    batch: list[tuple] = []
    start_time = time.time()

    logger.info(f"Loading countries data from {countries_path}...")
    logger.info(f"Feature filter: {FEATURE_FILTER}")
    if dry_run:
        logger.info("DRY-RUN: rows will be parsed but NOT inserted")

    with open(countries_path, encoding='utf-8', errors='replace') as f:
        for line_num, line in enumerate(f, 1):
            stats['rows_read'] += 1

            if line_num % 500_000 == 0:
                elapsed = time.time() - start_time
                logger.info(
                    f"  Read {line_num:,} lines | filtered {stats['rows_filtered']:,} | "
                    f"inserted {stats['rows_inserted']:,} | {elapsed:.0f}s elapsed"
                )

            parts = line.rstrip('\n').split('\t')
            if len(parts) < 18:
                continue

            feature_class = parts[COL_FEATURE_CLASS]
            feature_code = parts[COL_FEATURE_CODE]

            # Apply feature filter
            if feature_class not in FEATURE_FILTER:
                continue
            if feature_code not in FEATURE_FILTER[feature_class]:
                continue

            try:
                geoname_id = int(parts[COL_GEONAME_ID])
                lat = float(parts[COL_LATITUDE])
                lng = float(parts[COL_LONGITUDE])
            except (ValueError, IndexError):
                continue

            name = parts[COL_NAME].strip()
            ascii_name = parts[COL_ASCII_NAME].strip() or name
            country_code = parts[COL_COUNTRY_CODE].strip() or None

            try:
                population = int(parts[COL_POPULATION]) if parts[COL_POPULATION] else 0
            except ValueError:
                population = 0

            timezone = parts[COL_TIMEZONE].strip() or None

            # Merge alternate names from altnames file
            alt_names: list[str] = []
            if alt_map is not None:
                alt_names = list(dict.fromkeys(alt_map.get(geoname_id, [])))  # dedupe, preserve order

            # Also parse the inline alternate names column (comma-separated, English-looking)
            inline_alts = parts[COL_ALTERNATE_NAMES].strip()
            if inline_alts:
                for a in inline_alts.split(','):
                    a = a.strip()
                    if a and a not in alt_names and len(a) <= 100:
                        alt_names.append(a)

            stats['rows_filtered'] += 1
            batch.append((
                geoname_id,
                name,
                ascii_name,
                alt_names,      # TEXT[]
                lat,
                lng,
                feature_class,
                feature_code,
                country_code,
                population,
                timezone,
            ))

            if len(batch) >= BATCH_SIZE and not dry_run:
                _insert_batch(db, batch)
                stats['rows_inserted'] += len(batch)
                stats['batches'] += 1
                batch = []

    # Final batch
    if batch and not dry_run:
        _insert_batch(db, batch)
        stats['rows_inserted'] += len(batch)
        stats['batches'] += 1

    return stats


def _insert_batch(db: DatabaseManager, batch: list[tuple]):
    """Insert a batch of rows into geo_gazetteer using execute_values."""
    sql = """
        INSERT INTO geo_gazetteer
            (geoname_id, name, ascii_name, alternate_names,
             latitude, longitude, feature_class, feature_code,
             country_code, population, timezone)
        VALUES %s
        ON CONFLICT (geoname_id) DO NOTHING
    """
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            execute_values(cur, sql, batch, page_size=BATCH_SIZE)
        conn.commit()


def verify_load(db: DatabaseManager) -> dict[str, int]:
    """Return row counts by feature class for verification."""
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT feature_class, COUNT(*) AS cnt
                FROM geo_gazetteer
                GROUP BY feature_class
                ORDER BY cnt DESC
            """)
            rows = cur.fetchall()
            cur.execute("SELECT COUNT(*) FROM geo_gazetteer")
            total = cur.fetchone()[0]
    result = {r[0]: r[1] for r in rows}
    result['TOTAL'] = total
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Load GeoNames dump into geo_gazetteer PostgreSQL table",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/load_geonames.py --countries allCountries.txt
  python scripts/load_geonames.py --countries allCountries.txt --altnames alternateNames.txt
  python scripts/load_geonames.py --countries allCountries.txt --dry-run
        """
    )
    parser.add_argument('--countries', required=True, type=Path,
                        help='Path to GeoNames allCountries.txt')
    parser.add_argument('--altnames', type=Path, default=None,
                        help='Path to GeoNames alternateNames.txt (optional, recommended)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Parse and filter but do not insert into DB')

    args = parser.parse_args()

    if not args.countries.exists():
        print(f"ERROR: {args.countries} not found")
        sys.exit(1)

    start = time.time()

    # Step 1: Load alternate names (optional but recommended)
    alt_map = None
    if args.altnames:
        if not args.altnames.exists():
            logger.warning(f"alternateNames.txt not found at {args.altnames} — skipping")
        else:
            alt_map = load_alternate_names(args.altnames)

    # Step 2: Load and insert countries
    stats = load_countries(args.countries, alt_map, dry_run=args.dry_run)

    elapsed = time.time() - start
    logger.info("")
    logger.info("=" * 60)
    logger.info("GeoNames Load Complete")
    logger.info("=" * 60)
    logger.info(f"Lines read:     {stats['rows_read']:,}")
    logger.info(f"Rows filtered:  {stats['rows_filtered']:,}  (feature class A/P/H/L)")
    logger.info(f"Rows inserted:  {stats['rows_inserted']:,}")
    logger.info(f"Batches:        {stats['batches']}")
    logger.info(f"Elapsed:        {elapsed:.0f}s ({elapsed/60:.1f} min)")

    if args.dry_run:
        logger.info("\n[DRY-RUN] No data written to database.")
        return

    # Step 3: Verify
    db = DatabaseManager()
    counts = verify_load(db)
    logger.info("")
    logger.info("Row counts by feature class:")
    for fc, cnt in sorted(counts.items(), key=lambda x: -x[1]):
        logger.info(f"  {fc}: {cnt:,}")
    logger.info("=" * 60)
    logger.info("Done! Run geocode_geonames.py to start geocoding entities.")


if __name__ == "__main__":
    main()
