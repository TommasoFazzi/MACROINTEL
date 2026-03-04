"""Oracle 2.0 API router — POST /api/v1/oracle/chat + GET /api/v1/oracle/health."""

import asyncio
import logging
import os
import re
from datetime import datetime, time as dt_time

from fastapi import APIRouter, Depends, HTTPException, Request

from ..limiter import limiter
from ..oracle_auth import UserContext, verify_oracle_user
from ..schemas.oracle import OracleChatRequest, OracleChatResponse
from ...llm.oracle_orchestrator import get_oracle_orchestrator_singleton

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/oracle", tags=["Oracle"])

ORACLE_REQUIRE_GEMINI_KEY = os.getenv("ORACLE_REQUIRE_GEMINI_KEY", "false").lower() == "true"

# Loose format check: starts with AIza, 30-50 alphanumeric/dash/underscore chars.
# Google issues keys of 39-42 chars in practice; 30-50 gives forward-compatibility headroom.
_GEMINI_KEY_RE = re.compile(r'^AIza[0-9A-Za-z\-_]{30,50}$')


def _validate_gemini_key_format(key: str) -> None:
    if not _GEMINI_KEY_RE.match(key):
        raise HTTPException(
            status_code=422,
            detail="Invalid Gemini API key format (expected AIza + 30-50 alphanumeric chars)",
        )


@router.post("/chat")
@limiter.limit("3/minute")
async def oracle_chat(
    request: Request,
    body: OracleChatRequest,
    user: UserContext = Depends(verify_oracle_user),
):
    """
    Oracle 2.0 chat endpoint.

    Processes a natural language intelligence query through:
    1. QueryRouter (intent classification + QueryPlan)
    2. Tool execution (RAGTool, SQLTool, AggregationTool, GraphTool, MarketTool)
    3. LLM synthesis
    4. Response with sources and query_plan metadata

    Auth: API key whitelist (ORACLE_MODE=private).
    Rate: 3 req/min per IP.
    BYOK: pass gemini_api_key in body to use your own Gemini key for all LLM calls.
    """
    # BYOK: validate format and attach to user context
    if ORACLE_REQUIRE_GEMINI_KEY and not body.gemini_api_key:
        raise HTTPException(status_code=422, detail="gemini_api_key required in body")
    if body.gemini_api_key:
        _validate_gemini_key_format(body.gemini_api_key)
        user.gemini_api_key = body.gemini_api_key

    try:
        orchestrator = get_oracle_orchestrator_singleton()
    except Exception as e:
        logger.error("OracleOrchestrator init failed: %s", e)
        raise HTTPException(status_code=503, detail="Oracle service unavailable")

    try:
        ui_filters = {
            "start_date": (
                datetime.combine(body.start_date, dt_time.min)
                if body.start_date else None
            ),
            "end_date": (
                datetime.combine(body.end_date, dt_time.max)
                if body.end_date else None
            ),
            "categories": body.categories,
            "gpe_filter": body.gpe_filter,
            "mode": body.mode,
            "search_type": body.search_type,
        }

        result = orchestrator.process_query(
            query=body.query,
            session_id=body.session_id,
            ui_filters=ui_filters,
            user_context=user,
        )

        if "error" in result.get("metadata", {}):
            raise HTTPException(status_code=503, detail="Oracle processing error")

        return {
            "success": True,
            "data": result,
            "generated_at": datetime.utcnow().isoformat(),
        }

    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="LLM timeout")
    except HTTPException:
        raise
    except Exception as e:
        err_str = str(e).lower()
        # BYOK key errors: surface as 402 so the frontend can show a specific banner
        if body.gemini_api_key and any(
            kw in err_str for kw in ("permission", "quota", "api_key", "api key", "invalid_argument")
        ):
            raise HTTPException(
                status_code=402,
                detail=f"Your Gemini API key error: {e}",
            )
        logger.error("Oracle chat error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/health")
async def oracle_health(user: UserContext = Depends(verify_oracle_user)):
    """Health check for Oracle 2.0 subsystem."""
    try:
        orchestrator = get_oracle_orchestrator_singleton()
        with orchestrator._session_lock:
            active_sessions = len(orchestrator._sessions)
        return {
            "healthy": True,
            "checks": {
                "active_sessions": active_sessions,
                "registry_tools": orchestrator.tool_registry.registered_names(),
            },
        }
    except Exception as e:
        return {"healthy": False, "error": str(e)}
