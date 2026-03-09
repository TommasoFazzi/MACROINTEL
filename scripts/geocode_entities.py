#!/usr/bin/env python3
"""
Geocoding Service for Intelligence Map

Uses Photon (OSM-based, no strict rate limit).
Self-hosted Photon Docker is preferred in production (set PHOTON_URL env var).
Falls back to komoot.io public API when PHOTON_URL is not set.
Falls back to static cache for common geopolitical locations.
"""
import os
import sys
import time
import requests
from pathlib import Path
from typing import Optional, Dict, Tuple
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.storage.database import DatabaseManager
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Photon API configuration
# Use PHOTON_URL env var to point at self-hosted instance (e.g. http://photon:2322/api)
PHOTON_URL = os.environ.get("PHOTON_URL", "https://photon.komoot.io/api")
USER_AGENT = "INTEL_ITA_Intelligence_Map/2.0"
COURTESY_DELAY = 0.1  # 100ms courtesy delay between requests
# Italy-centric bias coordinates
BIAS_LAT = 41.9
BIAS_LON = 12.5

# Static cache for common locations (no API call needed)
STATIC_LOCATION_CACHE = {
    # Major countries
    'Taiwan': (23.6978, 120.9605),
    'China': (35.8617, 104.1954),
    'United States': (37.0902, -95.7129),
    'Russia': (61.5240, 105.3188),
    'Ukraine': (48.3794, 31.1656),
    'Israel': (31.0461, 34.8516),
    'Gaza': (31.3547, 34.3088),
    'Iran': (32.4279, 53.6880),
    'India': (20.5937, 78.9629),
    'Pakistan': (30.3753, 69.3451),
    'North Korea': (40.3399, 127.5101),
    'South Korea': (35.9078, 127.7669),
    'Japan': (36.2048, 138.2529),
    'Saudi Arabia': (23.8859, 45.0792),
    'Turkey': (38.9637, 35.2433),
    'France': (46.2276, 2.2137),
    'Germany': (51.1657, 10.4515),
    'United Kingdom': (55.3781, -3.4360),
    'Italy': (41.8719, 12.5674),
    'Spain': (40.4637, -3.7492),
    'Brazil': (-14.2350, -51.9253),
    'Mexico': (23.6345, -102.5528),
    'Canada': (56.1304, -106.3468),
    'Australia': (-25.2744, 133.7751),
    'Egypt': (26.8206, 30.8025),
    'Syria': (34.8021, 38.9968),
    'Iraq': (33.2232, 43.6793),
    'Afghanistan': (33.9391, 67.7100),
    'Poland': (51.9194, 19.1451),
    'Vietnam': (14.0583, 108.2772),
    'Thailand': (15.8700, 100.9925),
    'Philippines': (12.8797, 121.7740),
    'Indonesia': (-0.7893, 113.9213),
    'Myanmar': (21.9162, 95.9560),
    # Major cities
    'Beijing': (39.9042, 116.4074),
    'Washington': (38.9072, -77.0369),
    'Moscow': (55.7558, 37.6173),
    'Tel Aviv': (32.0853, 34.7818),
    'Tokyo': (35.6762, 139.6503),
    'London': (51.5074, -0.1278),
    'Paris': (48.8566, 2.3522),
    'Berlin': (52.5200, 13.4050),
    'Rome': (41.9028, 12.4964),
    'Madrid': (40.4168, -3.7038),
    'New York': (40.7128, -74.0060),
    'Los Angeles': (34.0522, -118.2437),
    'Hong Kong': (22.3193, 114.1694),
    'Singapore': (1.3521, 103.8198),
    'Dubai': (25.2048, 55.2708),
    'Istanbul': (41.0082, 28.9784),
    'Cairo': (30.0444, 31.2357),
    'Mumbai': (19.0760, 72.8777),
    'Delhi': (28.7041, 77.1025),
    'Seoul': (37.5665, 126.9780),
    'Taipei': (25.0330, 121.5654),
    'Baghdad': (33.3152, 44.3661),
    'Damascus': (33.5138, 36.2765),
    'Kyiv': (50.4501, 30.5234),
    'Jerusalem': (31.7683, 35.2137),
    'Tehran': (35.6892, 51.3890),
}


class GeocodingService:
    """Service for geocoding entity names to geographic coordinates"""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': USER_AGENT})
        self.last_request_time = 0
    
    def _respect_rate_limit(self):
        """Courtesy delay between API requests (Photon has no strict limit)."""
        elapsed = time.time() - self.last_request_time
        if elapsed < COURTESY_DELAY:
            time.sleep(COURTESY_DELAY - elapsed)
        self.last_request_time = time.time()

    def _check_cache(self, name: str) -> Optional[Tuple[float, float]]:
        """Check static cache for common locations"""
        # Exact match
        if name in STATIC_LOCATION_CACHE:
            logger.info(f"⚡ Cache hit: {name}")
            return STATIC_LOCATION_CACHE[name]

        # Case-insensitive match
        name_lower = name.lower()
        for cached_name, coords in STATIC_LOCATION_CACHE.items():
            if cached_name.lower() == name_lower:
                logger.info(f"⚡ Cache hit (case-insensitive): {name}")
                return coords

        return None
    
    def geocode_entity(
        self,
        name: str,
        entity_type: str
    ) -> Tuple[Optional[float], Optional[float], str]:
        """
        Geocode an entity name to coordinates using Photon.

        Args:
            name: Entity name (e.g., "Taiwan", "New York")
            entity_type: Entity type (GPE, LOC, FAC, ORG)

        Returns:
            Tuple of (latitude, longitude, status)
            status: 'FOUND', 'NOT_FOUND', or 'RETRY'
        """
        # Check cache first (no API call needed)
        cached_coords = self._check_cache(name)
        if cached_coords:
            return cached_coords[0], cached_coords[1], 'FOUND'

        # Courtesy delay for API calls
        self._respect_rate_limit()

        try:
            params = {
                'q': name,
                'limit': 3,
                'lang': 'en',
                'lat': BIAS_LAT,
                'lon': BIAS_LON,
            }

            # Add OSM tag filter based on entity type for better precision
            if entity_type == 'GPE':
                params['osm_tag'] = 'place'
            elif entity_type == 'LOC':
                params['osm_tag'] = 'natural'
            elif entity_type == 'FAC':
                params['osm_tag'] = 'building'

            response = self.session.get(PHOTON_URL, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()
            features = data.get('features', [])

            if features:
                # Photon returns GeoJSON: coordinates are [lng, lat]
                coords = features[0]['geometry']['coordinates']
                lng, lat = coords[0], coords[1]

                logger.info(f"✓ Geocoded '{name}' ({entity_type}): {lat}, {lng}")
                return lat, lng, 'FOUND'
            else:
                logger.warning(f"✗ No coordinates found for '{name}' ({entity_type})")
                return None, None, 'NOT_FOUND'

        except requests.exceptions.Timeout:
            logger.error(f"⚠ Timeout geocoding '{name}' - will retry later")
            return None, None, 'RETRY'

        except requests.exceptions.RequestException as e:
            logger.error(f"⚠ Error geocoding '{name}': {e} - will retry later")
            return None, None, 'RETRY'

        except Exception as e:
            logger.error(f"✗ Unexpected error geocoding '{name}': {e}")
            return None, None, 'NOT_FOUND'


def backfill_entity_coordinates(limit: int = None, entity_types: list = None, retry_failed: bool = False):
    """
    Backfill coordinates for existing entities.

    Args:
        limit: Maximum number of entities to process (None = all)
        entity_types: List of entity types to process (None = geographic types)
        retry_failed: If True, retry entities with RETRY status
    """
    if entity_types is None:
        # Default to geographic entity types (skip PERSON, ORG)
        entity_types = ['GPE', 'LOC', 'FAC']

    db = DatabaseManager()
    geocoder = GeocodingService()

    # Get pending entities
    logger.info(f"Fetching entities to geocode (types: {entity_types})...")

    with db.get_connection() as conn:
        with conn.cursor() as cur:
            # Build query based on retry_failed flag
            if retry_failed:
                status_condition = "geo_status IN ('PENDING', 'RETRY')"
            else:
                status_condition = "geo_status = 'PENDING'"

            query = f"""
                SELECT id, name, entity_type, mention_count
                FROM entities
                WHERE {status_condition}
                  AND entity_type = ANY(%s)
                ORDER BY mention_count DESC
            """

            if limit:
                query += f" LIMIT {limit}"

            cur.execute(query, (entity_types,))
            entities = cur.fetchall()

    logger.info(f"Found {len(entities)} entities to geocode")

    if not entities:
        logger.info("No entities to geocode!")
        return

    # Calculate estimated time (Photon is ~10x faster than Nominatim)
    cache_hits_estimate = sum(1 for _, name, _, _ in entities if name in STATIC_LOCATION_CACHE)
    api_calls = len(entities) - cache_hits_estimate
    estimated_seconds = api_calls * COURTESY_DELAY
    estimated_minutes = estimated_seconds / 60

    logger.info(f"📊 Estimated time: {estimated_minutes:.1f} minutes ({api_calls} API calls via Photon)")
    logger.info(f"  ⚡ Cache hits (instant): ~{cache_hits_estimate}")
    logger.info(f"  🌐 API calls (1 req/sec): ~{api_calls}")
    logger.info("")

    # Process each entity
    stats = {'found': 0, 'not_found': 0, 'retry': 0, 'cache_hits': 0}
    start_time = time.time()

    for idx, (entity_id, name, entity_type, mention_count) in enumerate(entities, 1):
        # Progress indicator with ETA
        percent = (idx / len(entities)) * 100
        elapsed = time.time() - start_time

        if idx > 1:
            eta_seconds = (elapsed / (idx - 1)) * (len(entities) - idx)
            eta_minutes = eta_seconds / 60
            logger.info(f"[{idx}/{len(entities)} | {percent:.1f}%] ETA: {eta_minutes:.1f}min | Geocoding: {name} ({entity_type}, mentions: {mention_count})")
        else:
            logger.info(f"[{idx}/{len(entities)} | {percent:.1f}%] Geocoding: {name} ({entity_type}, mentions: {mention_count})")

        # Check if cached (for stats)
        is_cached = name in STATIC_LOCATION_CACHE or any(name.lower() == c.lower() for c in STATIC_LOCATION_CACHE.keys())

        lat, lng, status = geocoder.geocode_entity(name, entity_type)

        if is_cached and status == 'FOUND':
            stats['cache_hits'] += 1

        # Update database immediately (incremental progress)
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                if status == 'FOUND':
                    cur.execute("""
                        UPDATE entities
                        SET latitude = %s,
                            longitude = %s,
                            geo_status = %s,
                            geocoded_at = %s
                        WHERE id = %s
                    """, (lat, lng, status, datetime.now(), entity_id))
                    stats['found'] += 1
                else:
                    cur.execute("""
                        UPDATE entities
                        SET geo_status = %s,
                            geocoded_at = %s
                        WHERE id = %s
                    """, (status, datetime.now(), entity_id))
                    stats[status.lower()] += 1

        # Progress checkpoint every 50 entities
        if idx % 50 == 0:
            logger.info(f"✓ Checkpoint: {stats['found']} found, {stats['not_found']} not found, {stats['retry']} retry, {stats['cache_hits']} from cache")

    # Print summary
    total_time = time.time() - start_time
    logger.info("\n" + "="*60)
    logger.info("Geocoding Summary:")
    logger.info(f"  ✓ Found:          {stats['found']}")
    logger.info(f"  ✗ Not Found:      {stats['not_found']}")
    logger.info(f"  ⚠ Retry:          {stats['retry']}")
    logger.info(f"  ⚡ Cache Hits:     {stats['cache_hits']}")
    logger.info(f"  ⏱ Total Time:     {total_time / 60:.1f} minutes")
    if len(entities) > 0:
        logger.info(f"  📊 Success Rate:  {(stats['found'] / len(entities) * 100):.1f}%")
    logger.info("="*60)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Geocode entities for Intelligence Map')
    parser.add_argument('--limit', type=int, help='Maximum number of entities to process')
    parser.add_argument('--test', action='store_true', help='Test mode: geocode only "Taiwan"')
    parser.add_argument('--types', nargs='+', default=['GPE', 'LOC', 'FAC'],
                       help='Entity types to geocode (default: GPE LOC FAC)')
    parser.add_argument('--retry', action='store_true',
                       help='Retry entities with RETRY status')
    parser.add_argument('--top', type=int,
                       help='Geocode only top N most-mentioned entities (priority mode)')

    args = parser.parse_args()

    if args.test:
        # Test mode
        logger.info("TEST MODE: Geocoding 'Taiwan'...")
        geocoder = GeocodingService()
        lat, lng, status = geocoder.geocode_entity("Taiwan", "GPE")

        if status == 'FOUND':
            logger.info(f"✓ Test successful! Taiwan: {lat}, {lng}")
        else:
            logger.error(f"✗ Test failed! Status: {status}")
    else:
        # Production mode
        effective_limit = args.top if args.top else args.limit
        backfill_entity_coordinates(
            limit=effective_limit,
            entity_types=args.types,
            retry_failed=args.retry
        )
