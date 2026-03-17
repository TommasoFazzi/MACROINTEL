#!/usr/bin/env python3
"""
Hybrid GeoNames + Gemini Geocoder

Replaces the Photon-first approach with entity resolution via local GeoNames DB
and Gemini 2.0 Flash Chain-of-Thought disambiguation.

Resolution pipeline per entity:
  1. Lookup GeoNames (exact/ascii/alternate name match)
     → 0 matches: go to Gemini
     → 1 unique match globally: accept (skip Gemini — unambiguous)
     → >1 matches: go to Gemini for spatial context
  2. Gemini 2.0 Flash CoT → { reasoning, clean_name, country_code, feature_type }
  3. Filtered GeoNames lookup using Gemini output
  4. Fallback → Photon API (for highly specific locations not in GeoNames)
  5. UPDATE entities SET latitude, longitude, geo_status='FOUND'

Usage:
    # Daily pipeline (replaces step 7):
    python scripts/geocode_geonames.py --limit 200

    # One-time backfill (top 2000 by mention_count):
    python scripts/geocode_geonames.py --limit 2000 --backfill

    # Dry-run (validate without writing):
    python scripts/geocode_geonames.py --dry-run --limit 50

    # Specific entity types only:
    python scripts/geocode_geonames.py --types GPE LOC --limit 500

Requirements:
    - Migration 021_geo_gazetteer.sql applied
    - geo_gazetteer table populated via load_geonames.py
    - GEMINI_API_KEY set in .env
    - Photon fallback: PHOTON_URL env var (optional, defaults to komoot.io)
"""

import sys
import os
import re
import time
import json
import requests
import argparse
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env", override=False)

from src.storage.database import DatabaseManager
from src.utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ENTITY_TYPES_GEO = {'GPE', 'LOC', 'FAC'}  # types eligible for geocoding

# Gemini setup (uses gemini-2.0-flash as per CLAUDE.md NLP layer convention)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_AVAILABLE = False
_llm_model = None

try:
    import google.generativeai as genai
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY, transport='rest')
        _llm_model = genai.GenerativeModel('gemini-2.0-flash')
        GEMINI_AVAILABLE = True
except ImportError:
    pass

GEMINI_DELAY = 0.2          # 200ms between Gemini calls → ~5 req/sec (safe under 1500 RPM)
PHOTON_URL = os.environ.get("PHOTON_URL", "https://photon.komoot.io/api")
PHOTON_DELAY = 0.1          # 100ms courtesy delay
USER_AGENT = "INTEL_ITA_Intelligence_Map/2.0"

# Feature type → GeoNames feature_class + feature_codes
FEATURE_TYPE_MAP: dict[str, tuple[str, list[str]]] = {
    'country':  ('A', ['PCLI', 'PCLD', 'PCLF', 'PCLS']),
    'state':    ('A', ['ADM1', 'ADM2']),
    'city':     ('P', ['PPLC', 'PPLA', 'PPLA2', 'PPL', 'PPLS']),
    'region':   ('L', ['AREA', 'RGN', 'RGNH', 'CONT']),
    'sea':      ('H', ['SEA', 'OCN', 'BAY', 'GULF', 'CHAN']),
    'strait':   ('H', ['STR', 'CHAN']),
    'river':    ('H', ['STM', 'STMH', 'STMI']),
    'lake':     ('H', ['LK', 'LKS', 'LKN', 'RSV']),
    'facility': ('S', ['AIRP', 'MILB', 'PORT', 'PRNQ', 'RSTN']),
    'other':    None,   # no feature filter
}

# Structured output schema for Gemini
GEMINI_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {
            "type": "string",
            "description": "1-2 sentence spatial interpretation citing headline context"
        },
        "clean_name": {
            "type": "string",
            "description": "Canonical English geographic name"
        },
        "country_code": {
            "type": "string",
            "description": "ISO 3166-1 alpha-2 of the country the entity is LOCATED IN; null if it IS a country"
        },
        "feature_type": {
            "type": "string",
            "enum": [
                "country", "state", "city", "region",
                "sea", "strait", "river", "lake", "facility", "other"
            ]
        },
        "is_geographic": {
            "type": "boolean",
            "description": "False for persons, organizations, abstract concepts"
        }
    },
    "required": ["reasoning", "clean_name", "feature_type", "is_geographic"]
}


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class GeoResult:
    lat: float
    lng: float
    source: str       # 'geonames_direct', 'geonames_gemini', 'photon'
    clean_name: str   # canonical name used for lookup
    reasoning: str = ""


# ---------------------------------------------------------------------------
# GeoNames lookup functions
# ---------------------------------------------------------------------------

def _lookup_gazetteer_all(db: DatabaseManager, name: str) -> list[tuple]:
    """
    Find all GeoNames matches for a name (exact ascii, original name, or alternate name).
    Returns list of (geoname_id, ascii_name, latitude, longitude, feature_class, feature_code,
                     country_code, population).
    """
    name_lower = name.lower().strip()
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT geoname_id, ascii_name, latitude, longitude,
                       feature_class, feature_code, country_code, population
                FROM geo_gazetteer
                WHERE lower(ascii_name) = %s
                   OR lower(name) = %s
                   OR %s = ANY(alternate_names)
                ORDER BY population DESC NULLS LAST
                LIMIT 20
            """, (name_lower, name_lower, name))
            return cur.fetchall()


def _lookup_gazetteer_filtered(
    db: DatabaseManager,
    clean_name: str,
    country_code: Optional[str],
    feature_type: str,
) -> Optional[GeoResult]:
    """
    Lookup GeoNames with Gemini-provided filters (country_code, feature_type).
    Returns best match or None.
    """
    name_lower = clean_name.lower().strip()
    feature_info = FEATURE_TYPE_MAP.get(feature_type)

    params: list = [name_lower, name_lower, clean_name]
    feature_clause = ""
    if feature_info is not None:
        fc, codes = feature_info
        feature_clause = f"AND feature_class = %s AND feature_code = ANY(%s)"
        params += [fc, codes]

    country_clause = ""
    if country_code:
        country_clause = "AND country_code = %s"
        params.append(country_code.upper())

    sql = f"""
        SELECT geoname_id, ascii_name, latitude, longitude,
               feature_class, feature_code, country_code, population
        FROM geo_gazetteer
        WHERE (
            lower(ascii_name) = %s
            OR lower(name) = %s
            OR %s = ANY(alternate_names)
        )
        {feature_clause}
        {country_clause}
        ORDER BY population DESC NULLS LAST
        LIMIT 1
    """

    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()

    if row:
        _, ascii_name, lat, lng, *_ = row
        return GeoResult(
            lat=float(lat), lng=float(lng),
            source='geonames_gemini',
            clean_name=ascii_name or clean_name,
        )
    return None


# ---------------------------------------------------------------------------
# Gemini disambiguation
# ---------------------------------------------------------------------------

def _gemini_resolve(entity_name: str, article_titles: list[str]) -> Optional[dict]:
    """
    Call Gemini 2.0 Flash with CoT to resolve entity name to canonical geo.
    Returns parsed dict or None on failure.
    """
    if not GEMINI_AVAILABLE:
        return None

    headlines = "\n".join(f"- {t}" for t in article_titles[:5]) if article_titles else "(no headlines available)"

    prompt = (
        "You are a geopolitical entity resolver for an intelligence analytics platform.\n"
        "First, reason about the spatial context using the article headlines below.\n"
        "Then provide your resolution.\n\n"
        f'Entity: "{entity_name}"\n\n'
        f"Article headlines where this entity appears:\n{headlines}\n\n"
        "Instructions:\n"
        "- reasoning: 1-2 sentences explaining your spatial interpretation, citing headline context\n"
        "- clean_name: canonical English geographic name (e.g. 'Washington D.C.', not 'Washington')\n"
        "- country_code: ISO 3166-1 alpha-2 of the country this entity is LOCATED IN "
        "(null if the entity itself IS a country)\n"
        "- feature_type: one of country|state|city|region|sea|strait|river|lake|facility|other\n"
        "- is_geographic: false for persons, organizations, financial instruments, or abstract concepts"
    )

    try:
        response = _llm_model.generate_content(
            prompt,
            generation_config={
                "response_mime_type": "application/json",
                "response_schema": GEMINI_RESPONSE_SCHEMA,
                "max_output_tokens": 200,
                "temperature": 0.1,
            },
            request_options={"timeout": 15},
        )
        raw = response.text.strip()
        # Strip accidental markdown fences
        raw = re.sub(r'^```[a-z]*\n?', '', raw).rstrip('`').strip()
        result = json.loads(raw)
        logger.debug(
            f"  Gemini resolved '{entity_name}' → "
            f"clean='{result.get('clean_name')}' "
            f"country={result.get('country_code')} "
            f"type={result.get('feature_type')} "
            f"geo={result.get('is_geographic')} | "
            f"reasoning: {result.get('reasoning', '')[:80]}"
        )
        return result
    except Exception as e:
        logger.warning(f"  Gemini resolution failed for '{entity_name}': {e}")
        return None
    finally:
        time.sleep(GEMINI_DELAY)


# ---------------------------------------------------------------------------
# Photon fallback
# ---------------------------------------------------------------------------

_photon_session = requests.Session()
_photon_session.headers.update({'User-Agent': USER_AGENT})
_photon_last_request = 0.0


def _photon_geocode(name: str, entity_type: str) -> Optional[GeoResult]:
    """Photon API fallback for entities not found in GeoNames."""
    global _photon_last_request
    elapsed = time.time() - _photon_last_request
    if elapsed < PHOTON_DELAY:
        time.sleep(PHOTON_DELAY - elapsed)
    _photon_last_request = time.time()

    params = {'q': name, 'limit': 1, 'lang': 'en'}
    if entity_type == 'GPE':
        params['osm_tag'] = 'place'
    elif entity_type == 'LOC':
        params['osm_tag'] = 'natural'
    elif entity_type == 'FAC':
        params['osm_tag'] = 'building'

    try:
        resp = _photon_session.get(PHOTON_URL, params=params, timeout=10)
        resp.raise_for_status()
        features = resp.json().get('features', [])
        if features:
            coords = features[0]['geometry']['coordinates']
            lng, lat = coords[0], coords[1]
            return GeoResult(lat=lat, lng=lng, source='photon', clean_name=name)
    except Exception as e:
        logger.warning(f"  Photon fallback failed for '{name}': {e}")
    return None


# ---------------------------------------------------------------------------
# Main geocoding logic
# ---------------------------------------------------------------------------

def _get_article_titles(db: DatabaseManager, entity_id: int, limit: int = 5) -> list[str]:
    """Fetch recent article titles where this entity was mentioned."""
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT a.title
                FROM articles a
                JOIN entity_mentions em ON a.id = em.article_id
                WHERE em.entity_id = %s AND a.title IS NOT NULL
                ORDER BY a.published_date DESC NULLS LAST
                LIMIT %s
            """, (entity_id, limit))
            return [row[0] for row in cur.fetchall()]


def geocode_entity(
    db: DatabaseManager,
    entity_id: int,
    entity_name: str,
    entity_type: str,
) -> Optional[GeoResult]:
    """
    Full hybrid geocoding pipeline for one entity.
    Returns GeoResult or None if the entity cannot/should not be geocoded.
    """
    if entity_type not in ENTITY_TYPES_GEO:
        return None  # ORG, PERSON → skip

    # Step 1: GeoNames direct lookup
    matches = _lookup_gazetteer_all(db, entity_name)

    if len(matches) == 1:
        # Single global match → unambiguous, skip Gemini
        _, ascii_name, lat, lng, fc, fcode, cc, pop = matches[0]
        logger.info(f"  ✓ Unique GeoNames match: '{ascii_name}' ({fc}/{fcode}, {cc}) pop={pop}")
        return GeoResult(
            lat=float(lat), lng=float(lng),
            source='geonames_direct',
            clean_name=ascii_name or entity_name,
        )

    # Step 2: Gemini CoT disambiguation
    article_titles = _get_article_titles(db, entity_id)
    if len(matches) > 1:
        logger.info(f"  → {len(matches)} GeoNames matches for '{entity_name}' — calling Gemini")
    else:
        logger.info(f"  → 0 GeoNames matches for '{entity_name}' — calling Gemini")

    resolution = _gemini_resolve(entity_name, article_titles)

    if resolution is None:
        # Gemini failed → try Photon as last resort
        logger.warning(f"  Gemini unavailable — falling back to Photon for '{entity_name}'")
        return _photon_geocode(entity_name, entity_type)

    if not resolution.get('is_geographic', True):
        logger.info(f"  ✗ Gemini: '{entity_name}' is not geographic → skip")
        return None

    clean_name = resolution.get('clean_name') or entity_name
    country_code = resolution.get('country_code')
    feature_type = resolution.get('feature_type', 'other')
    reasoning = resolution.get('reasoning', '')

    # Step 3: GeoNames lookup with Gemini output
    result = _lookup_gazetteer_filtered(db, clean_name, country_code, feature_type)
    if result:
        result.reasoning = reasoning
        logger.info(
            f"  ✓ GeoNames+Gemini: '{clean_name}' ({feature_type}, {country_code}) "
            f"→ {result.lat:.4f}, {result.lng:.4f}"
        )
        return result

    # Step 4: Photon fallback (highly specific locations, military bases, etc.)
    logger.info(f"  → GeoNames miss for '{clean_name}' — Photon fallback")
    photon_result = _photon_geocode(clean_name, entity_type)
    if photon_result:
        photon_result.reasoning = reasoning
        logger.info(f"  ✓ Photon: '{clean_name}' → {photon_result.lat:.4f}, {photon_result.lng:.4f}")
        return photon_result

    logger.warning(f"  ✗ All sources failed for '{entity_name}' / '{clean_name}'")
    return None


# ---------------------------------------------------------------------------
# DB update
# ---------------------------------------------------------------------------

def _update_entity(
    db: DatabaseManager,
    entity_id: int,
    result: Optional[GeoResult],
    dry_run: bool,
) -> str:
    """Write geocoding result to entities table. Returns status string."""
    if result is None:
        status = 'NOT_FOUND'
        if not dry_run:
            with db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE entities SET geo_status = %s, geocoded_at = %s WHERE id = %s",
                        (status, datetime.now(), entity_id)
                    )
                conn.commit()
        return status

    if not dry_run:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE entities
                    SET latitude = %s,
                        longitude = %s,
                        geo_status = 'FOUND',
                        geocoded_at = %s
                    WHERE id = %s
                """, (result.lat, result.lng, datetime.now(), entity_id))
            conn.commit()
    return 'FOUND'


# ---------------------------------------------------------------------------
# Batch processor
# ---------------------------------------------------------------------------

def run_geocoding(
    limit: int = 200,
    backfill: bool = False,
    entity_types: list[str] = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Main geocoding batch processor.

    Args:
        limit: Max entities to process
        backfill: If True, also include NOT_FOUND + RETRY statuses
        entity_types: List of entity types (default: GPE, LOC, FAC)
        dry_run: Parse and resolve but do not write to DB

    Returns:
        Stats dict
    """
    if entity_types is None:
        entity_types = list(ENTITY_TYPES_GEO)

    db = DatabaseManager()

    if not GEMINI_AVAILABLE:
        logger.warning("GEMINI_API_KEY not set or google-generativeai not installed — Gemini disabled")
        logger.warning("Falling back to GeoNames direct + Photon only")

    # Query entities to process
    status_filter = "IN ('PENDING', 'NOT_FOUND', 'RETRY')" if backfill else "= 'PENDING'"

    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT id, name, entity_type, mention_count
                FROM entities
                WHERE geo_status {status_filter}
                  AND entity_type = ANY(%s)
                ORDER BY mention_count DESC
                LIMIT %s
            """, (entity_types, limit))
            entities = cur.fetchall()

    logger.info(f"Entities to process: {len(entities)} (limit={limit}, backfill={backfill})")
    if dry_run:
        logger.info("DRY-RUN: no DB writes will occur")

    stats = {
        'total': len(entities),
        'found': 0,
        'not_found': 0,
        'skipped': 0,
        'geonames_direct': 0,
        'geonames_gemini': 0,
        'photon': 0,
        'gemini_calls': 0,
    }

    start = time.time()

    for idx, (entity_id, name, entity_type, mention_count) in enumerate(entities, 1):
        pct = idx / len(entities) * 100
        elapsed = time.time() - start
        eta = (elapsed / idx) * (len(entities) - idx) if idx > 1 else 0
        logger.info(
            f"[{idx}/{len(entities)} | {pct:.0f}% | ETA {eta/60:.1f}min] "
            f"'{name}' ({entity_type}, {mention_count} mentions)"
        )

        result = geocode_entity(db, entity_id, name, entity_type)

        if result is None and entity_type not in ENTITY_TYPES_GEO:
            stats['skipped'] += 1
            continue

        status = _update_entity(db, entity_id, result, dry_run)

        if status == 'FOUND' and result:
            stats['found'] += 1
            stats[result.source] = stats.get(result.source, 0) + 1
            if result.source != 'geonames_direct':
                stats['gemini_calls'] += 1
        else:
            stats['not_found'] += 1
            if entity_type in ENTITY_TYPES_GEO:
                stats['gemini_calls'] += 1  # Gemini was called even for NOT_FOUND geo entities

        # Checkpoint every 100 entities
        if idx % 100 == 0:
            logger.info(
                f"  Checkpoint: {stats['found']} found, {stats['not_found']} not found | "
                f"geonames_direct={stats['geonames_direct']} "
                f"geonames_gemini={stats['geonames_gemini']} "
                f"photon={stats['photon']}"
            )

    total_time = time.time() - start
    logger.info("")
    logger.info("=" * 60)
    logger.info("Geocoding Complete")
    logger.info("=" * 60)
    logger.info(f"Processed:        {len(entities)}")
    logger.info(f"Found:            {stats['found']} ({stats['found']/max(1,len(entities))*100:.1f}%)")
    logger.info(f"Not found:        {stats['not_found']}")
    logger.info(f"Skipped (type):   {stats['skipped']}")
    logger.info(f"Source breakdown:")
    logger.info(f"  GeoNames direct:  {stats['geonames_direct']}  (no Gemini needed)")
    logger.info(f"  GeoNames+Gemini:  {stats['geonames_gemini']}")
    logger.info(f"  Photon fallback:  {stats['photon']}")
    logger.info(f"Gemini calls:     {stats['gemini_calls']}")
    logger.info(f"Total time:       {total_time/60:.1f} min")
    if dry_run:
        logger.info("\n[DRY-RUN] No data written.")
    logger.info("=" * 60)

    return stats


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Hybrid GeoNames+Gemini entity geocoder (replaces geocode_entities.py)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Daily pipeline (same usage as old geocode_entities.py):
  python scripts/geocode_geonames.py --limit 200

  # One-time backfill (top 2000 by mention_count, includes NOT_FOUND/RETRY):
  python scripts/geocode_geonames.py --limit 2000 --backfill

  # Dry-run validation:
  python scripts/geocode_geonames.py --dry-run --limit 20

  # Specific entity types:
  python scripts/geocode_geonames.py --types GPE --limit 500 --backfill
        """
    )
    parser.add_argument('--limit', type=int, default=200,
                        help='Max entities to process (default: 200)')
    parser.add_argument('--backfill', action='store_true',
                        help='Include NOT_FOUND + RETRY statuses (in addition to PENDING)')
    parser.add_argument('--types', nargs='+', default=None,
                        metavar='TYPE',
                        help='Entity types to process (default: GPE LOC FAC)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Resolve but do not write to database')

    args = parser.parse_args()

    entity_types = args.types or list(ENTITY_TYPES_GEO)
    # Validate types
    invalid = [t for t in entity_types if t not in {'GPE', 'LOC', 'FAC', 'ORG', 'PERSON'}]
    if invalid:
        parser.error(f"Invalid entity types: {invalid}. Valid: GPE, LOC, FAC, ORG, PERSON")

    run_geocoding(
        limit=args.limit,
        backfill=args.backfill,
        entity_types=entity_types,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
