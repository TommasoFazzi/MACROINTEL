"""
OracleOrchestrator — Oracle 2.0 main coordinator.

Entry point for all Oracle 2.0 queries. Manages:
- Tool registry (RAGTool, SQLTool, AggregationTool, GraphTool, MarketTool)
- QueryRouter (intent classification, QueryPlan generation)
- ConversationMemory (per-session context with TTL cleanup)
- Caching (intent, SQL results, embeddings via TTLCache)
- LLM synthesis with retry/fallback
- Anti-hallucination guard for empty results
- BYOK (Bring Your Own Key): user-supplied Gemini API key for all LLM calls
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
from .query_analyzer import get_query_analyzer, merge_filters
from .query_router import QueryRouter
from .schemas import QueryIntent, QueryPlan
from .tools import ToolRegistry
from .tools.rag_tool import RAGTool
from .tools.sql_tool import SQLTool
from .tools.aggregation_tool import AggregationTool
from .tools.graph_tool import GraphTool
from .tools.market_tool import MarketTool
from .tools.ticker_themes_tool import TickerThemesTool
from .tools.report_compare_tool import ReportCompareTool
from .tools.reference_tool import ReferenceTool
from .tools.spatial_tool import SpatialTool
from .tools.base import ToolResult
from ..storage.database import DatabaseManager
from ..utils.logger import get_logger

load_dotenv(Path(__file__).parent.parent.parent / ".env")

logger = get_logger(__name__)

SESSION_TTL_SECONDS = 7200       # 2 hours
SESSION_CLEANUP_INTERVAL = 600   # 10 minutes

# Module-level lock for BYOK genai.configure() calls.
# Phase 1 (single user): acceptable — serializes BYOK requests, latency is fine.
# Phase 2 (multi-user): migrate to google-genai new SDK (google.genai.Client(api_key=key))
# which supports per-client isolation without global state mutation. Remove lock at that point.
_byok_lock = threading.Lock()


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

        # Default query router (uses self.llm — not used for BYOK requests)
        self.router = QueryRouter(llm)

        # Session storage: {session_id: (ConversationContext, last_active_ts)}
        self._sessions: Dict[str, Tuple[ConversationContext, datetime]] = {}
        self._session_lock = threading.Lock()

        # Caches
        self._intent_cache: TTLCache = TTLCache(maxsize=200, ttl=600)      # 10min
        self._sql_result_cache: TTLCache = TTLCache(maxsize=500, ttl=300)  # 5min
        self._embedding_cache: TTLCache = TTLCache(maxsize=1000, ttl=300)  # 5min

        # Background cleanup daemon
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_expired_sessions, daemon=True
        )
        self._cleanup_thread.start()

        logger.info("OracleOrchestrator initialized")

    # ── Tool registration ──────────────────────────────────────────────────────

    def _register_tools(self):
        for tool_class in (RAGTool, SQLTool, AggregationTool, GraphTool, MarketTool, TickerThemesTool, ReportCompareTool, ReferenceTool, SpatialTool):
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
        user_context: Optional[Any] = None,  # UserContext from oracle_auth; Any avoids circular import
    ) -> Dict[str, Any]:
        """
        Process a user query end-to-end.

        If user_context contains a gemini_api_key, all LLM calls (routing + synthesis)
        use that key (BYOK). Otherwise, uses the singleton's default LLM.
        """
        user_gemini_key = getattr(user_context, "gemini_api_key", None)
        user_id = getattr(user_context, "user_id", None) or session_id

        if user_gemini_key:
            return self._process_with_byok_key(
                query, session_id, ui_filters, user_gemini_key, user_id
            )
        return self._process_internal(
            query, session_id, ui_filters, llm=self.llm, user_id=user_id
        )

    def _process_with_byok_key(
        self,
        query: str,
        session_id: str,
        ui_filters: Optional[Dict],
        user_key: str,
        user_id: str,
    ) -> Dict[str, Any]:
        """
        Thread-safe BYOK: temporarily configure genai globally with the user's key,
        create a fresh LLM model instance, then restore the original key.

        NOTE Phase 1 (single user): lock serializes BYOK requests — latency is acceptable.
        NOTE Phase 2 (multi-user): migrate to google-genai new SDK (google.genai.Client)
        which supports per-client isolation without this global state mutation. Remove lock.
        """
        with _byok_lock:
            orig_key = os.getenv("GEMINI_API_KEY", "")
            genai.configure(api_key=user_key, transport="rest")
            try:
                user_llm = genai.GenerativeModel("gemini-2.5-flash")
                return self._process_internal(
                    query, session_id, ui_filters, llm=user_llm, user_id=user_id
                )
            finally:
                genai.configure(api_key=orig_key, transport="rest")

    def _process_internal(
        self,
        query: str,
        session_id: str,
        ui_filters: Optional[Dict],
        llm: Any,
        user_id: str,
    ) -> Dict[str, Any]:
        """Core query processing pipeline. Uses the supplied llm for all LLM calls."""
        start_time = time.time()
        ctx = self._get_or_create_session(session_id)
        is_follow_up = ctx.detect_follow_up(query)

        filters = ui_filters or {}

        # ── Step 0: Extract structured filters from natural language ───────
        # QueryAnalyzer resolves "fine febbraio", "ultimi 7 giorni", GPE, categories.
        # UI filters (explicit date picker) always take precedence via merge_filters().
        semantic_query = None
        try:
            analyzer = get_query_analyzer()
            analysis = analyzer.analyze(query)
            if analysis["success"]:
                extracted = analysis["filters"]
                merged = merge_filters(
                    extracted,
                    ui_start_date=filters.get("start_date"),
                    ui_end_date=filters.get("end_date"),
                    ui_categories=filters.get("categories"),
                    ui_gpe_filter=filters.get("gpe_filter"),
                )
                for k in ("start_date", "end_date", "categories", "gpe_filter"):
                    if merged.get(k) is not None:
                        filters[k] = merged[k]
                if merged.get("query_for_embedding"):
                    semantic_query = merged["query_for_embedding"]
                logger.info(
                    f"QueryAnalyzer: start={merged.get('start_date')}, "
                    f"end={merged.get('end_date')}, gpe={merged.get('gpe_filter')}"
                )
        except Exception as e:
            logger.warning(f"QueryAnalyzer failed ({e}), proceeding without extracted filters")

        # Use a router scoped to the given llm (supports BYOK key for routing too)
        router = QueryRouter(llm) if llm is not self.llm else self.router

        # ── Step 1: Route ───────────────────────────────────────────────────
        try:
            query_plan = router.route(
                query, context=ctx.get_context_for_llm() if is_follow_up else None
            )
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

        # ── Step 1b: Time-weighted decay configuration ────────────────────
        # Set decay rate K based on detected intent (geopolitics-tuned defaults)
        from .tools.rag_tool import DEFAULT_DECAY_K
        _INTENT_DECAY_K = {
            QueryIntent.FACTUAL: 0.03,      # fresh news, aggressive decay
            QueryIntent.ANALYTICAL: 0.015,   # long-term trends, gentle decay
            QueryIntent.NARRATIVE: 0.02,     # balanced
            QueryIntent.MARKET: 0.04,        # markets are ultra time-sensitive
            QueryIntent.COMPARATIVE: 0.015,  # comparisons need history
            QueryIntent.TICKER: 0.03,        # ticker = recent
            QueryIntent.OVERVIEW: 0.005,     # panoramic — needs full history, half-life ~140 days
            QueryIntent.REFERENCE: 0.001,    # reference data is static — minimal decay
            QueryIntent.SPATIAL: 0.005,      # spatial data combined with recent events
        }
        if "time_decay_k" not in filters:
            filters["time_decay_k"] = _INTENT_DECAY_K.get(
                query_plan.intent, DEFAULT_DECAY_K
            )
        # Time-shifting: for queries with explicit end_date, decay is relative
        # to that date (not today). "Cosa successe a gennaio 2024?" penalizes
        # 2021 articles (far from window) without disabling decay.
        if filters.get("end_date") and "time_decay_reference" not in filters:
            filters["time_decay_reference"] = filters["end_date"]
            logger.info(
                f"Time decay reference shifted to end_date: {filters['end_date']}"
            )

        # ── Step 2: Execute tools ──────────────────────────────────────────
        # Apply semantic_query (stripped of temporal noise) to RAG steps
        if semantic_query:
            for step in query_plan.execution_steps:
                if step.tool_name == "rag_search" and "query" in step.parameters:
                    step.parameters["query"] = semantic_query

        tool_results: List[Tuple[str, ToolResult]] = []
        for step in query_plan.execution_steps:
            tool_name = step.tool_name
            params = dict(step.parameters)

            # Inject session filters into RAG search
            if tool_name == "rag_search" and filters:
                params.setdefault("filters", {})
                for k in ("start_date", "end_date", "categories", "gpe_filter",
                           "sources", "search_type", "time_decay_k", "time_decay_reference"):
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
        answer = self._check_empty_and_synthesize(
            query, query_plan, tool_results, ctx, is_follow_up, llm=llm
        )

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
            user_id=user_id,
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
        llm: Any = None,
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

        return self._synthesize(query, query_plan, tool_results, ctx, is_follow_up, llm=llm)

    def _synthesize(
        self,
        query: str,
        query_plan: QueryPlan,
        tool_results: List[Tuple[str, ToolResult]],
        ctx: ConversationContext,
        is_follow_up: bool,
        llm: Any = None,
    ) -> str:
        # Build tool results block
        results_block = ""
        for tool_name, result in tool_results:
            tool = self.tool_registry.get_tool(tool_name)
            formatted = tool.format_for_llm(result)
            results_block += f"\n[TOOL: {tool_name}]\n{formatted}\n"

        # Build numbered source list for inline citations
        # Sources are sorted by similarity descending — same order as prepare_sources()
        numbered_sources_block = ""
        for tn, tr in tool_results:
            if tn == "rag_search" and tr.success and tr.data:
                rag_instance = RAGTool(db=self.db)
                prepared = rag_instance.prepare_sources(
                    tr.data.get("reports", []),
                    tr.data.get("chunks", []),
                )
                if prepared:
                    lines = []
                    for i, s in enumerate(prepared, 1):
                        title = s.get("title", "N/A")
                        org = s.get("source", "")
                        date = s.get("date_str", "")
                        meta = " — ".join(filter(None, [org, date]))
                        lines.append(f"[{i}] {title}" + (f" ({meta})" if meta else ""))
                    numbered_sources_block = (
                        "\nFONTI INDICIZZATE (usa questi numeri per le citazioni):\n"
                        + "\n".join(lines[:20])
                        + "\n"
                    )
                break

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
{numbered_sources_block}
TASK: Sintetizza una risposta analitica completa basata ESCLUSIVAMENTE sui risultati degli strumenti sopra.

ISTRUZIONI:
1. **Analisi Profonda**: Paragrafi densi con dati specifici.
2. **Citazioni**: Cita le fonti inline usando la notazione numerica [1], [2], ecc., dove il numero corrisponde all'indice nella lista FONTI INDICIZZATE sopra. Usa le citazioni naturalmente nel testo.
3. **Struttura**: Sintesi Esecutiva → Analisi Dettagliata → Implicazioni Strategiche.
4. **Freshness**: Segnala dati strutturati con `data_year` o `vintage` antecedente di oltre 2 anni rispetto a oggi come potenzialmente obsoleti. Se l'utente chiede "l'ultimo dato" o "il più recente", privilegia i dati con la data più recente disponibile nei risultati.
5. **Linguaggio**: Formale, professionale, analitico.
6. **Credibilità Fonti**: I documenti includono `Autorevolezza: X.X/5.0`. Tier di riferimento (derivati dalla matrice di autorità del sistema): 5.0 = istituti primari (RAND, CSIS, RUSI, ECB, ecc.) · 4.0–4.5 = stampa specializzata affermata · 3.5 = media regionali · 3.0 = media di Stato/contesti sensibili. Regole: (a) se fonti di tier diverso riportano fatti contrastanti sullo stesso evento, esplicita il contrasto ("Fonte A riferisce X; Fonte B — autorevolezza inferiore — riporta Y") e privilegia il tier più alto nelle conclusioni; (b) se una fonte a bassa autorevolezza è l'unica a segnalare un evento critico, riportala comunque con scetticismo ("secondo [fonte], non ancora confermato da fonti primarie"); (c) non mostrare i punteggi numerici all'utente — incorpora il giudizio di credibilità nel ragionamento analitico.
7. **Chain-of-Verification — Conflitti tra fonti strutturate e RAG**: Se trovi lo stesso KPI quantitativo (PIL, inflazione, debito, popolazione, ecc.) sia in dati strutturati (TOOL: reference_lookup, sql_query con tabelle country_profiles/macro_forecasts/macro_indicators) sia nel contesto RAG (TOOL: rag_search) con valori diversi, NON scegliere silenziosamente uno solo. Scrivi esplicitamente: "Dato strutturato [fonte, anno]: X | Contesto narrativo [titolo articolo]: Y — possibile lag temporale o divergenza metodologica." Regola di priorità: (a) dati strutturati hanno precedenza per KPI quantitativi ufficiali (PIL, inflazione, debito/PIL, bilancia commerciale); (b) il contesto RAG ha priorità per sentiment, narrativa politica, sviluppi recenti (<30 giorni) e eventi non ancora riflessi nei dataset strutturati.

CRITICAL: If tool results are empty or contain no relevant data, EXPLICITLY STATE that no information was found. DO NOT generate, infer, or hallucinate content.

RISPOSTA DETTAGLIATA:"""

        try:
            result = self._synthesis_llm_call(prompt, llm=llm)
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
    def _synthesis_llm_call(self, prompt: str, llm: Any = None):
        model = llm if llm is not None else self.llm
        return model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=8192,
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
        user_id: Optional[str] = None,
    ):
        try:
            metadata: Dict[str, Any] = {"query_plan": query_plan.model_dump()}
            if user_id and user_id != session_id:
                metadata["user_id"] = user_id
            self.db.log_oracle_query(
                session_id=session_id,
                query=query,
                intent=query_plan.intent.value,
                complexity=query_plan.complexity.value,
                tools_used=query_plan.tools,
                execution_time=execution_time,
                success=success,
                metadata=metadata,
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
