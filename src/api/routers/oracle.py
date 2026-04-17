"""Oracle 2.0 API router — POST /api/v1/oracle/chat + GET /api/v1/oracle/health."""

import asyncio
import logging
import os
from datetime import datetime, time as dt_time

from fastapi import APIRouter, Depends, HTTPException, Request

from ..limiter import limiter
from ..oracle_auth import UserContext, verify_oracle_user
from ..schemas.oracle import OracleChatRequest, OracleChatResponse
from ...llm.oracle_orchestrator import get_oracle_orchestrator_singleton

_ORACLE_ADMIN_KEY = os.getenv("ORACLE_ADMIN_KEY")


def _oracle_rate_limit(request: Request) -> str:
    """Admin key bypasses the public rate limit."""
    if _ORACLE_ADMIN_KEY and request.headers.get("X-API-Key") == _ORACLE_ADMIN_KEY:
        return "10000/day"
    return "5/day"

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/oracle", tags=["Oracle"])


@router.post("/chat")
@limiter.limit(_oracle_rate_limit)
async def oracle_chat(
    request: Request,
    body: OracleChatRequest,
    user: UserContext = Depends(verify_oracle_user),
):
    """
    Oracle 2.0 chat endpoint.

    Processes a natural language intelligence query through:
    1. Agentic tool loop (RAGTool, SQLTool, AggregationTool, GraphTool, MarketTool, ...)
    2. LLM synthesis (Claude Sonnet 4.6)
    3. Response with sources and query_plan metadata

    Auth: API key whitelist (ORACLE_MODE=private).
    Rate: 5 req/day per IP.
    BREAKING CHANGE (2026-04-17): gemini_api_key BYOK removed.
    Oracle now uses server-side ANTHROPIC_API_KEY exclusively.
    """
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
