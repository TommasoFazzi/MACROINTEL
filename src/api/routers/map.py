"""Intelligence Map API router.

Dedicated router for all map-related endpoints. Replaces inline
endpoints that were previously in main.py.

Endpoints:
    GET /api/v1/map/entities           — GeoJSON FeatureCollection (cached, gzipped)
    GET /api/v1/map/entities/{id}      — Entity detail + related articles + related storylines
    GET /api/v1/map/arcs               — GeoJSON LineStrings for entity co-occurrence
    GET /api/v1/map/stats              — Live stats for HUD overlay
"""
import hashlib
import json
import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import ORJSONResponse

from ..auth import verify_api_key
from ..limiter import limiter
from ..schemas.map import (
    EntityCollection,
    EntityDetail,
    EntityArticle,
    EntityStoryline,
    MapStats,
)
from ...storage.database import DatabaseManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/map", tags=["Map"])


# ---------------------------------------------------------------------------
# In-memory cache — invalidated by TTL or explicit POST /cache/invalidate
# Map data changes at most once per day (after the daily pipeline).
# ---------------------------------------------------------------------------
_entity_cache: dict = {}
_CACHE_TTL = 300  # 5 minutes


def _cache_key(
    limit: int,
    entity_types: Optional[str],
    days: Optional[int],
    min_mentions: Optional[int],
    min_score: Optional[float],
    search: Optional[str],
) -> str:
    """Deterministic cache key from filter params."""
    raw = f"{limit}|{entity_types}|{days}|{min_mentions}|{min_score}|{search}"
    return hashlib.md5(raw.encode()).hexdigest()


def _get_cached(key: str) -> Optional[dict]:
    entry = _entity_cache.get(key)
    if entry and time.time() < entry["expires_at"]:
        return entry["data"]
    return None


def _set_cached(key: str, data: dict, ttl: int = _CACHE_TTL) -> None:
    _entity_cache[key] = {"data": data, "expires_at": time.time() + ttl}


def get_db() -> DatabaseManager:
    return DatabaseManager()


# ---------------------------------------------------------------------------
# GET /api/v1/map/entities
# ---------------------------------------------------------------------------
@router.get("/entities")
@limiter.limit("30/minute")
async def get_entities(
    request: Request,
    limit: int = Query(default=5000, ge=1, le=10000, description="Max entities"),
    entity_type: Optional[str] = Query(
        default=None,
        description="Comma-separated entity types: GPE,ORG,PERSON,LOC,FAC",
    ),
    days: Optional[int] = Query(
        default=None, ge=1, le=365,
        description="Only entities seen in the last N days",
    ),
    min_mentions: Optional[int] = Query(
        default=None, ge=1,
        description="Minimum mention count threshold",
    ),
    min_score: Optional[float] = Query(
        default=None, ge=0.0, le=1.0,
        description="Minimum intelligence_score threshold (0–1)",
    ),
    search: Optional[str] = Query(
        default=None, max_length=100,
        description="Case-insensitive name search (ILIKE)",
    ),
    api_key: str = Depends(verify_api_key),
    db: DatabaseManager = Depends(get_db),
):
    """
    Returns a GeoJSON FeatureCollection of geocoded entities.

    Each feature includes enriched properties: intelligence_score,
    storyline_count, top_storyline, primary_community_id, hours_ago.

    Supports filtering by entity_type, recency (days), significance
    (min_mentions, min_score), and name search.  Response is cached for
    5 minutes and compressed via GZip middleware.
    """
    ck = _cache_key(limit, entity_type, days, min_mentions, min_score, search)
    cached = _get_cached(ck)
    if cached:
        return ORJSONResponse(
            content=cached,
            headers={
                "Cache-Control": f"public, max-age={_CACHE_TTL}",
                "X-Cache": "HIT",
            },
        )

    try:
        # Parse entity_type filter
        type_list = (
            [t.strip().upper() for t in entity_type.split(",") if t.strip()]
            if entity_type
            else None
        )

        geojson = db.get_entities_for_map(
            limit=limit,
            entity_types=type_list,
            days=days,
            min_mentions=min_mentions,
            min_score=min_score,
            search=search,
        )

        _set_cached(ck, geojson)

        logger.info(
            f"Map entities: {geojson['filtered_count']}/{geojson['total_count']} "
            f"(types={entity_type}, days={days}, min_mentions={min_mentions}, "
            f"min_score={min_score})"
        )

        return ORJSONResponse(
            content=geojson,
            headers={
                "Cache-Control": f"public, max-age={_CACHE_TTL}",
                "X-Cache": "MISS",
            },
        )

    except Exception as e:
        logger.error(f"Error fetching map entities: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# GET /api/v1/map/entities/{entity_id}
# ---------------------------------------------------------------------------
@router.get("/entities/{entity_id}")
@limiter.limit("60/minute")
async def get_entity_detail(
    request: Request,
    entity_id: int,
    api_key: str = Depends(verify_api_key),
    db: DatabaseManager = Depends(get_db),
):
    """
    Returns full entity detail including related articles AND related storylines.

    The storyline connection traverses:
    entity → entity_mentions → articles → article_storylines → storylines
    """
    try:
        entity = db.get_entity_detail_with_storylines(entity_id)
        if not entity:
            raise HTTPException(status_code=404, detail="Entity not found")
        return entity

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching entity {entity_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# GET /api/v1/map/arcs
# ---------------------------------------------------------------------------
@router.get("/arcs")
@limiter.limit("20/minute")
async def get_entity_arcs(
    request: Request,
    min_score: float = Query(
        default=0.3, ge=0.0, le=1.0,
        description="Minimum intelligence_score for both arc endpoints",
    ),
    limit: int = Query(
        default=300, ge=1, le=1000,
        description="Maximum number of arcs to return",
    ),
    api_key: str = Depends(verify_api_key),
    db: DatabaseManager = Depends(get_db),
):
    """
    Returns GeoJSON LineString features for entity pairs that share
    at least one active storyline.

    Used by the Intelligence Map arc/connection layer to visualise
    entity co-occurrence.  Only entity pairs where both endpoints have
    intelligence_score >= min_score are included.

    Each feature has properties: source_name, target_name,
    shared_storylines, max_momentum.
    """
    arcs_key = f"arcs|{min_score}|{limit}"
    cached = _get_cached(arcs_key)
    if cached:
        return ORJSONResponse(
            content=cached,
            headers={"Cache-Control": f"public, max-age={_CACHE_TTL}", "X-Cache": "HIT"},
        )

    try:
        geojson = db.get_entity_arcs(min_score=min_score, limit=limit)
        _set_cached(arcs_key, geojson)
        logger.info(
            f"Map arcs: {geojson['arc_count']} arcs "
            f"(min_score={min_score}, limit={limit})"
        )
        return ORJSONResponse(
            content=geojson,
            headers={"Cache-Control": f"public, max-age={_CACHE_TTL}", "X-Cache": "MISS"},
        )

    except Exception as e:
        logger.error(f"Error fetching entity arcs: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# GET /api/v1/map/stats
# ---------------------------------------------------------------------------
@router.get("/stats")
@limiter.limit("30/minute")
async def get_map_stats(
    request: Request,
    api_key: str = Depends(verify_api_key),
    db: DatabaseManager = Depends(get_db),
):
    """
    Returns live stats for the HUD overlay: total entities, geocoded count,
    active storylines, and entity type breakdown.
    """
    try:
        stats = db.get_map_stats()
        return stats
    except Exception as e:
        logger.error(f"Error fetching map stats: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# POST /api/v1/map/cache/invalidate
# ---------------------------------------------------------------------------
@router.post("/cache/invalidate")
@limiter.limit("10/minute")
async def invalidate_cache(
    request: Request,
    api_key: str = Depends(verify_api_key),
):
    """
    Explicitly invalidate the map entity cache.
    Called by refresh_map_data.py after the daily pipeline.
    """
    _entity_cache.clear()
    logger.info("Map entity cache invalidated")
    return {"status": "cache_invalidated", "timestamp": time.time()}
