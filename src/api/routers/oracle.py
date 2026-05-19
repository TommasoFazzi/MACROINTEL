"""Oracle 2.0 API router — POST /api/v1/oracle/chat + GET /api/v1/oracle/health."""

import asyncio
import logging
import os
import secrets
from datetime import datetime, time as dt_time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi.util import get_remote_address

from ..limiter import limiter
from ..oracle_auth import UserContext, verify_oracle_user
from ..schemas.oracle import OracleChatRequest, OracleChatResponse
from ...llm.oracle_orchestrator import get_oracle_orchestrator_singleton

_ORACLE_ADMIN_KEY = os.getenv("ORACLE_ADMIN_KEY")
_ORACLE_WHITELIST_IPS = {
    ip.strip() for ip in os.getenv("ORACLE_WHITELIST_IPS", "").split(",") if ip.strip()
}


def _real_client_ip(request: Request) -> str:
    """Extract original client IP from X-Forwarded-For (set by nginx + frontend proxy).

    Falls back to direct peer address if XFF is missing — but in the production
    chain (browser → nginx → next.js → fastapi) XFF is always present.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return get_remote_address(request)


def _is_bypass(request: Request) -> bool:
    """Admin key OR whitelisted IP → skip all rate limits."""
    if _ORACLE_ADMIN_KEY and secrets.compare_digest(
        request.headers.get("X-API-Key", ""), _ORACLE_ADMIN_KEY
    ):
        return True
    return _real_client_ip(request) in _ORACLE_WHITELIST_IPS


def _oracle_per_ip_key(request: Request) -> Optional[str]:
    if _is_bypass(request):
        return None
    return _real_client_ip(request)


def _oracle_global_key(request: Request) -> Optional[str]:
    if _is_bypass(request):
        return None
    return "oracle:global"

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/oracle", tags=["Oracle"])


@router.post("/chat")
@limiter.limit("3/day", key_func=_oracle_per_ip_key)
@limiter.limit("15/day", key_func=_oracle_global_key)
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
