"""Romania Vertical API router.

Endpoints:
    GET /api/v1/romania/macro              — 5 RO macro indicators (latest + 90-day series)
    GET /api/v1/romania/briefings          — List saved Romania reports (?type=daily|weekly&limit=N)
    GET /api/v1/romania/briefings/{id}     — Single Romania report content + metadata
"""
import logging
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ..limiter import limiter
from ...storage.database import DatabaseManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/romania", tags=["Romania"])

_cache: dict = {}
_CACHE_TTL = 300  # 5 minutes


def _get_cached(key: str) -> Optional[dict]:
    entry = _cache.get(key)
    if entry and time.time() < entry["expires_at"]:
        return entry["data"]
    return None


def _set_cached(key: str, data: dict, ttl: int = _CACHE_TTL) -> None:
    _cache[key] = {"data": data, "expires_at": time.time() + ttl}


def get_db() -> DatabaseManager:
    return DatabaseManager()


_RO_INDICATOR_KEYS = ["BNR_RATE", "RO_CPI_YOY", "EUR_RON", "RO_DEFICIT_GDP", "RO_10Y_YIELD"]
_RO_INDICATOR_LABELS = {
    "BNR_RATE": "BNR Policy Rate",
    "RO_CPI_YOY": "CPI YoY",
    "EUR_RON": "EUR/RON",
    "RO_DEFICIT_GDP": "Deficit/PIL",
    "RO_10Y_YIELD": "10Y RON Yield",
}


# ---------------------------------------------------------------------------
# GET /api/v1/romania/macro
# ---------------------------------------------------------------------------
@router.get("/macro")
@limiter.limit("30/minute")
async def get_romania_macro(request: Request):
    """
    Return latest values and 90-day series for 5 Romanian macro indicators.
    """
    cache_key = "romania_macro"
    cached = _get_cached(cache_key)
    if cached:
        return JSONResponse(content=cached)

    db = get_db()
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT indicator_key, value, unit, date
                    FROM macro_indicators
                    WHERE country_code = 'RO'
                      AND indicator_key = ANY(%s)
                      AND date >= CURRENT_DATE - INTERVAL '90 days'
                    ORDER BY indicator_key, date DESC
                """, [_RO_INDICATOR_KEYS])
                rows = cur.fetchall()
    except Exception as e:
        logger.error(f"[Romania macro] DB error: {e}")
        raise HTTPException(status_code=500, detail="Database error")
    finally:
        db.close()

    # Organise into indicator buckets
    indicators: dict = {}
    for key, value, unit, date in rows:
        if key not in indicators:
            indicators[key] = {
                "key": key,
                "label": _RO_INDICATOR_LABELS.get(key, key),
                "unit": unit,
                "latest": None,
                "series": [],
            }
        entry = {"date": date.isoformat() if hasattr(date, "isoformat") else str(date), "value": float(value) if value is not None else None}
        if indicators[key]["latest"] is None:
            indicators[key]["latest"] = entry
        indicators[key]["series"].append(entry)

    result = {
        "indicators": list(indicators.values()),
        "count": len(indicators),
    }
    _set_cached(cache_key, result)
    return JSONResponse(content=result)


# ---------------------------------------------------------------------------
# GET /api/v1/romania/briefings
# ---------------------------------------------------------------------------
@router.get("/briefings")
@limiter.limit("30/minute")
async def list_romania_briefings(
    request: Request,
    type: Optional[str] = Query(default=None, description="Filter by type: 'daily' or 'weekly'"),
    limit: int = Query(default=20, ge=1, le=100, description="Max briefings to return"),
):
    """
    List saved Romania briefings, newest first.
    Filters by report_type = 'romania-daily' or 'romania-weekly'.
    """
    if type and type not in ("daily", "weekly"):
        raise HTTPException(status_code=400, detail="type must be 'daily' or 'weekly'")

    cache_key = f"romania_briefings|{type}|{limit}"
    cached = _get_cached(cache_key)
    if cached:
        return JSONResponse(content=cached)

    report_type_filter = f"romania-{type}" if type else None

    db = get_db()
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                if report_type_filter:
                    cur.execute("""
                        SELECT id, report_date, report_type, metadata, status,
                               LEFT(draft_content, 500) AS excerpt
                        FROM reports
                        WHERE report_type = %s
                        ORDER BY report_date DESC, id DESC
                        LIMIT %s
                    """, [report_type_filter, limit])
                else:
                    cur.execute("""
                        SELECT id, report_date, report_type, metadata, status,
                               LEFT(draft_content, 500) AS excerpt
                        FROM reports
                        WHERE report_type LIKE 'romania-%%'
                        ORDER BY report_date DESC, id DESC
                        LIMIT %s
                    """, [limit])
                rows = cur.fetchall()
    except Exception as e:
        logger.error(f"[Romania briefings] DB error: {e}")
        raise HTTPException(status_code=500, detail="Database error")
    finally:
        db.close()

    briefings = []
    for report_id, report_date, report_type, metadata, status, excerpt in rows:
        briefings.append({
            "id": report_id,
            "date": report_date.isoformat() if hasattr(report_date, "isoformat") else str(report_date),
            "report_type": report_type,
            "status": status,
            "excerpt": (excerpt or "").strip()[:400],
            "metadata": metadata if isinstance(metadata, dict) else {},
        })

    result = {"briefings": briefings, "count": len(briefings)}
    _set_cached(cache_key, result)
    return JSONResponse(content=result)


# ---------------------------------------------------------------------------
# GET /api/v1/romania/briefings/{id}
# ---------------------------------------------------------------------------
@router.get("/briefings/{report_id}")
@limiter.limit("30/minute")
async def get_romania_briefing(request: Request, report_id: int):
    """Return full content and metadata for a single Romania briefing."""
    cache_key = f"romania_briefing|{report_id}"
    cached = _get_cached(cache_key)
    if cached:
        return JSONResponse(content=cached)

    db = get_db()
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, report_date, report_type, metadata, status,
                           draft_content, final_content
                    FROM reports
                    WHERE id = %s AND report_type LIKE 'romania-%%'
                """, [report_id])
                row = cur.fetchone()
    except Exception as e:
        logger.error(f"[Romania briefing/{report_id}] DB error: {e}")
        raise HTTPException(status_code=500, detail="Database error")
    finally:
        db.close()

    if not row:
        raise HTTPException(status_code=404, detail="Briefing not found")

    rid, report_date, report_type, metadata, status, draft, final = row
    content = final or draft or ""

    result = {
        "id": rid,
        "date": report_date.isoformat() if hasattr(report_date, "isoformat") else str(report_date),
        "report_type": report_type,
        "status": status,
        "content": content,
        "metadata": metadata if isinstance(metadata, dict) else {},
    }
    _set_cached(cache_key, result, ttl=600)
    return JSONResponse(content=result)
