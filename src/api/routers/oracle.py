"""Oracle 2.0 API router — POST /api/v1/oracle/chat + GET /api/v1/oracle/health."""

import asyncio
import logging
from datetime import datetime, time as dt_time

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import verify_api_key
from ..schemas.oracle import OracleChatRequest, OracleChatResponse
from ...llm.oracle_orchestrator import OracleOrchestrator, get_oracle_orchestrator_singleton

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/oracle", tags=["Oracle"])


@router.post("/chat")
async def oracle_chat(
    request: Request,
    body: OracleChatRequest,
    api_key: str = Depends(verify_api_key),
):
    """
    Oracle 2.0 chat endpoint.

    Processes a natural language intelligence query through:
    1. QueryRouter (intent classification + QueryPlan)
    2. Tool execution (RAGTool, SQLTool, AggregationTool, GraphTool, MarketTool)
    3. LLM synthesis
    4. Response with sources and query_plan metadata

    Rate: 10 req/min per IP.
    """
    try:
        orchestrator = get_oracle_orchestrator_singleton()
    except Exception as e:
        logger.error(f"OracleOrchestrator init failed: {e}")
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
async def oracle_health(api_key: str = Depends(verify_api_key)):
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
