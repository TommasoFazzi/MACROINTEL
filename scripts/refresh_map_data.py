#!/usr/bin/env python3
"""
refresh_map_data.py — Refresh map data after the daily pipeline.

Steps:
  1. REFRESH MATERIALIZED VIEW CONCURRENTLY mv_entity_storyline_bridge
  2. Recompute intelligence_score for all entities
  3. Invalidate the map router's in-memory cache (POST /api/v1/map/cache/invalidate)

Called as step 9 in daily_pipeline.py, after narrative processing and geocoding.
Can also be run standalone for manual refreshes.
"""
import logging
import os
import sys

import requests

# Allow running from repo root or INTELLIGENCE_ITA/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.storage.database import DatabaseManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> int:
    """Run all refresh steps. Returns exit code (0 = success)."""
    logger.info("=== refresh_map_data: start ===")

    # ------------------------------------------------------------------ #
    # Step 1: Refresh entity↔storyline materialized view
    # ------------------------------------------------------------------ #
    try:
        db = DatabaseManager()
        db.refresh_entity_bridge()
        logger.info("mv_entity_storyline_bridge refreshed")
    except Exception as exc:
        logger.error(f"Failed to refresh entity bridge: {exc}")
        return 1

    # ------------------------------------------------------------------ #
    # Step 2: Recompute intelligence scores
    # ------------------------------------------------------------------ #
    try:
        n = db.compute_intelligence_scores()
        logger.info(f"{n} intelligence scores updated")
    except Exception as exc:
        logger.error(f"Failed to compute intelligence scores: {exc}")
        return 1

    # ------------------------------------------------------------------ #
    # Step 3: Invalidate map router cache (best-effort — API may be down)
    # ------------------------------------------------------------------ #
    api_url = os.environ.get("INTELLIGENCE_API_URL", "http://localhost:8000")
    api_key = os.environ.get("INTELLIGENCE_API_KEY", "")

    try:
        resp = requests.post(
            f"{api_url}/api/v1/map/cache/invalidate",
            headers={"X-API-Key": api_key},
            timeout=5,
        )
        logger.info(f"Map cache invalidated: HTTP {resp.status_code}")
    except requests.exceptions.ConnectionError:
        logger.info("API not running — cache invalidation skipped (will expire naturally)")
    except Exception as exc:
        logger.warning(f"Cache invalidation failed (non-fatal): {exc}")

    logger.info("=== refresh_map_data: done ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
