"""
OracleOrchestrator — Oracle 2.0 main coordinator.

Entry point for all Oracle 2.0 queries. Manages:
- Tool registry (RAGTool, SQLTool, AggregationTool, GraphTool, MarketTool)
- QueryRouter (intent classification, QueryPlan generation)
- ConversationMemory (per-session context with TTL cleanup)
- Caching (intent, SQL results, embeddings via TTLCache)
- LLM synthesis with retry/fallback
- Anti-hallucination guard for empty results
- Logging to oracle_query_log table
"""

import hashlib
import os
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import google.generativeai as genai
import google.api_core.exceptions
from cachetools import TTLCache
from dotenv import load_dotenv
from pathlib import Path
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .conversation_memory import ConversationContext
from .query_router import QueryRouter
from .schemas import QueryIntent, QueryPlan
from .tools import ToolRegistry
from .tools.rag_tool import RAGTool
from .tools.sql_tool import SQLTool
from .tools.aggregation_tool import AggregationTool
from .tools.graph_tool import GraphTool
from .tools.market_tool import MarketTool
from .tools.base import ToolResult
from ..storage.database import DatabaseManager
from ..utils.logger import get_logger

load_dotenv(Path(__file__).parent.parent.parent / ".env")

logger = get_logger(__name__)

SESSION_TTL_SECONDS = 7200       # 2 hours
SESSION_CLEANUP_INTERVAL = 600   # 10 minutes


class OracleOrchestrator:
    """
    Oracle 2.0 Orchestrator.

    Usage:
        orchestrator = get_oracle_orchestrator_singleton()
        result = orchestrator.process_query(query="...", session_id="abc")
    """

    def __init__(self, db: DatabaseManager, llm):
        self.db = db
        self.llm = llm

        # Tool registry
        self.tool_registry = ToolRegistry()
        self._register_tools()

        # Query router
        self.router = QueryRouter(llm)

        # Session storage: {session_id: (ConversationContext, last_active_ts)}
        self._sessions: Dict[str, Tuple[ConversationContext, datetime]] = {}
        self._session_lock = threading.Lock()

        # Caches
        self._intent_cache: TTLCache = TTLCache(maxsize=200, ttl=600)      # 10min
        self._sql_result_cache: TTLCache = TTLCache(maxsize=500, ttl=300)  # 5min
        self._embedding_cache: TTLCache = TTLCache(maxsize=1000, ttl=3600) # 1h

        # Background cleanup daemon
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_expired_sessions, daemon=True
        )
        self._cleanup_thread.start()

        logger.info("OracleOrchestrator initialized")

    # ── Tool registration ──────────────────────────────────────────────────────

    def _register_tools(self):
        for tool_class in (RAGTool, SQLTool, AggregationTool, GraphTool, MarketTool):
            self.tool_registry.register(tool_class, db=self.db, llm=self.llm)
        logger.info(f"Registered tools: {self.tool_registry.registered_names()}")

    # ── Session management ─────────────────────────────────────────────────────

    def _get_or_create_session(self, session_id: str) -> ConversationContext:
        with self._session_lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = (ConversationContext(session_id), datetime.now())
                logger.debug(f"New session: {session_id}")
            ctx, _ = self._sessions[session_id]
            self._sessions[session_id] = (ctx, datetime.now())  # refresh last_active
            return ctx

    def _cleanup_expired_sessions(self):
        while True:
            time.sleep(SESSION_CLEANUP_INTERVAL)
            now = datetime.now()
            with self._session_lock:
                expired = [
                    sid for sid, (ctx, last_active) in self._sessions.items()
                    if (now - last_active).total_seconds() > SESSION_TTL_SECONDS
                ]
                for sid in expired:
                    del self._sessions[sid]
            if expired:
                logger.info(f"Cleaned {len(expired)} expired Oracle sessions")

    # ── Main entry point ───────────────────────────────────────────────────────

    def process_query(
        self,
        query: str,
        session_id: str = "default",
        ui_filters: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Process a user query end-to-end:
        1. Get/create session context
        2. Route query → QueryPlan
        3. Execute tools
        4. Synthesize answer
        5. Log to oracle_query_log
        """
        start_time = time.time()
        ctx = self._get_or_create_session(session_id)
        is_follow_up = ctx.detect_follow_up(query)

        # Merge UI filters into router context
        filters = ui_filters or {}

        # ── Step 1: Route ───────────────────────────────────────────────────
        try:
            query_plan = self.router.route(query, context=ctx.get_context_for_llm() if is_follow_up else None)
        except Exception as e:
            logger.warning(f"QueryRouter failed ({e}), using FACTUAL fallback")
            from .schemas import QueryComplexity, ExecutionStep
            query_plan = QueryPlan(
                intent=QueryIntent.FACTUAL,
                complexity=QueryComplexity.SIMPLE,
                tools=["rag_search"],
                execution_steps=[ExecutionStep(
                    tool_name="rag_search",
                    parameters={"query": query, "mode": "both", "top_k": 10},
                )],
                estimated_time=5.0,
            )

        # ── Step 2: Execute tools ──────────────────────────────────────────
        tool_results: List[Tuple[str, ToolResult]] = []
        for step in query_plan.execution_steps:
            tool_name = step.tool_name
            params = dict(step.parameters)

            # Inject session filters into RAG search
            if tool_name == "rag_search" and filters:
                params.setdefault("filters", {})
                for k in ("start_date", "end_date", "categories", "gpe_filter", "sources", "search_type"):
                    if filters.get(k) is not None:
                        params["filters"][k] = filters[k]
                if filters.get("mode"):
                    params["mode"] = filters["mode"]
                if filters.get("search_type"):
                    params["filters"]["search_type"] = filters["search_type"]

            # SQL result caching
            cache_key = None
            if tool_name == "sql_query" and params.get("query"):
                cache_key = hashlib.md5(params["query"].encode()).hexdigest()
                if cache_key in self._sql_result_cache:
                    logger.debug(f"SQL cache hit for key {cache_key[:8]}")
                    tool_results.append((tool_name, self._sql_result_cache[cache_key]))
                    continue

            try:
                tool = self.tool_registry.get_tool(tool_name)
                result = tool.execute(**params)
            except Exception as e:
                logger.error(f"Tool {tool_name} failed: {e}")
                if step.is_critical:
                    result = ToolResult(success=False, data=None, error=str(e))
                else:
                    continue

            if cache_key and result.success:
                self._sql_result_cache[cache_key] = result

            tool_results.append((tool_name, result))

        # ── Step 3: Anti-hallucination empty results guard ─────────────────
        answer = self._check_empty_and_synthesize(query, query_plan, tool_results, ctx, is_follow_up)

        # ── Step 4: Collect sources (from RAGTool results) ─────────────────
        sources: List[Dict] = []
        for tool_name, result in tool_results:
            if tool_name == "rag_search" and result.success and result.data:
                rag_tool = RAGTool(db=self.db)
                sources = rag_tool.prepare_sources(
                    result.data.get("reports", []),
                    result.data.get("chunks", []),
                )
                break

        # ── Step 5: Update conversation memory ────────────────────────────
        ctx.add_message("user", query)
        ctx.add_message("assistant", answer, metadata={"query_plan": query_plan.model_dump()})
        ctx.last_query_plan = query_plan

        # Track entities from query plan
        if query_plan.execution_steps:
            for step in query_plan.execution_steps:
                entities = step.parameters.get("gpe_filter") or []
                if entities:
                    ctx.track_entities(entities)

        # ── Step 6: Log to oracle_query_log ───────────────────────────────
        execution_time = time.time() - start_time
        self._log_query(
            session_id=session_id,
            query=query,
            query_plan=query_plan,
            execution_time=execution_time,
            success=True,
        )

        return {
            "answer": answer,
            "sources": sources,
            "query_plan": query_plan.model_dump(),
            "mode": filters.get("mode", "both"),
            "metadata": {
                "query": query,
                "session_id": session_id,
                "is_follow_up": is_follow_up,
                "execution_time": round(execution_time, 2),
                "tools_executed": [t for t, _ in tool_results],
                "timestamp": datetime.now().isoformat(),
            },
        }

    # ── Synthesis ──────────────────────────────────────────────────────────────

    def _check_empty_and_synthesize(
        self,
        query: str,
        query_plan: QueryPlan,
        tool_results: List[Tuple[str, ToolResult]],
        ctx: ConversationContext,
        is_follow_up: bool,
    ) -> str:
        # Anti-hallucination: check if all results are empty
        all_empty = all(
            not r.success or not r.data or (
                isinstance(r.data, dict) and not any(
                    bool(v) for v in r.data.values() if isinstance(v, (list, dict))
                )
            )
            for _, r in tool_results
        ) if tool_results else True

        if all_empty:
            return (
                "Non ho trovato informazioni sufficienti nel database per rispondere "
                "a questa query. Possibili cause:\n"
                "- Il periodo temporale richiesto non ha dati indicizzati\n"
                "- L'entità geografica o categoria non corrisponde a contenuti nel DB\n"
                "- La query è troppo specifica — prova a riformularla in modo più generico"
            )

        return self._synthesize(query, query_plan, tool_results, ctx, is_follow_up)

    def _synthesize(
        self,
        query: str,
        query_plan: QueryPlan,
        tool_results: List[Tuple[str, ToolResult]],
        ctx: ConversationContext,
        is_follow_up: bool,
    ) -> str:
        # Build tool results block
        results_block = ""
        for tool_name, result in tool_results:
            tool = self.tool_registry.get_tool(tool_name)
            formatted = tool.format_for_llm(result)
            results_block += f"\n[TOOL: {tool_name}]\n{formatted}\n"

        # Conversation context for follow-ups
        conv_block = ""
        if is_follow_up and ctx.message_count > 0:
            conv_block = ctx.get_context_for_llm() + "\n\n"

        current_date = datetime.now().strftime("%d/%m/%Y")
        prompt = f"""{conv_block}Sei The Oracle, analista senior di intelligence geopolitica e finanziaria.
DATA ODIERNA: {current_date}

USER QUERY: {query}

TOOL RESULTS:
{results_block}

TASK: Sintetizza una risposta analitica completa basata ESCLUSIVAMENTE sui risultati degli strumenti sopra.

ISTRUZIONI:
1. **Analisi Profonda**: Paragrafi densi con dati specifici.
2. **Citazioni**: Ogni affermazione con fonte [Report #ID] o [Articolo: Titolo].
3. **Struttura**: Sintesi Esecutiva → Analisi Dettagliata → Implicazioni Strategiche.
4. **Freshness**: Segnala dati storici (>30 giorni).
5. **Linguaggio**: Formale, professionale, analitico.

CRITICAL: If tool results are empty or contain no relevant data, EXPLICITLY STATE that no information was found. DO NOT generate, infer, or hallucinate content.

RISPOSTA DETTAGLIATA:"""

        try:
            result = self._synthesis_llm_call(prompt)
            return result.text
        except Exception as e:
            logger.error(f"Synthesis LLM failed: {e}")
            # Fallback: raw tool results with disclaimer
            fallback = "Analisi parziale: impossibile completare la sintesi LLM. Dati grezzi:\n\n"
            fallback += results_block
            return fallback

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(min=2, max=10),
        retry=retry_if_exception_type((
            google.api_core.exceptions.DeadlineExceeded,
            google.api_core.exceptions.ServiceUnavailable,
        )),
    )
    def _synthesis_llm_call(self, prompt: str):
        return self.llm.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=4096,
                temperature=0.4,
                top_p=0.95,
            ),
            request_options={"timeout": 90},
        )

    # ── Logging ────────────────────────────────────────────────────────────────

    def _log_query(
        self,
        session_id: str,
        query: str,
        query_plan: QueryPlan,
        execution_time: float,
        success: bool,
    ):
        try:
            self.db.log_oracle_query(
                session_id=session_id,
                query=query,
                intent=query_plan.intent.value,
                complexity=query_plan.complexity.value,
                tools_used=query_plan.tools,
                execution_time=execution_time,
                success=success,
                metadata={"query_plan": query_plan.model_dump()},
            )
        except Exception as e:
            logger.debug(f"oracle_query_log insert failed (non-critical): {e}")


# ── Singleton ───────────────────────────────────────────────────────────────────

_orchestrator: Optional[OracleOrchestrator] = None
_orchestrator_lock = threading.Lock()


def get_oracle_orchestrator_singleton() -> OracleOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        with _orchestrator_lock:
            if _orchestrator is None:
                db = DatabaseManager()
                api_key = os.getenv("GEMINI_API_KEY", "").strip()
                if not api_key:
                    raise ValueError("GEMINI_API_KEY not found in environment")
                genai.configure(api_key=api_key, transport="rest")
                llm = genai.GenerativeModel("gemini-2.5-flash")
                _orchestrator = OracleOrchestrator(db=db, llm=llm)
    return _orchestrator
