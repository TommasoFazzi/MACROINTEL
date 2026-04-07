"""
OracleOrchestrator — Oracle Agentic Engine (v3).

Architecture: Native Gemini Function Calling with an iterative agentic loop.

Flow:
    User Query
        → start_chat(history=session_history)
        → [LLM decides which tool(s) to call based on SOPs]
        → execute tool → return compressed result as FunctionResponse
        → [repeat up to MAX_AGENTIC_ITERATIONS]
        → LLM produces final text answer

Key improvements over Oracle 2.0 (static pipeline):
- No pre-planned tool execution: LLM chooses tools dynamically based on query + results
- Chain-of-Thought via mandatory `rationale` field in every tool call
- Automatic fallback: if RAG returns empty, LLM can try sql_query without manual intervention
- Native conversation history: session messages serialized as Gemini Content[]
- UI filters injected into first message (not system prompt) → system prompt is static/cacheable

Preserved from Oracle 2.0:
- 5-layer SQL safety in SQLTool
- Time-weighted decay config (passed via system prompt SOPs)
- Source authority integration in RAGTool
- Session management with TTL cleanup (2h)
- Per-session caching (SQL result cache 5min)
- BYOK support (serialized via _byok_lock)
- oracle_query_log logging
- Anti-hallucination guard for all-empty results
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
from .schemas import QueryIntent
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
MAX_AGENTIC_ITERATIONS = 4       # Max tool-call rounds before forcing synthesis

# Module-level lock for BYOK genai.configure() calls.
# Phase 1 (single user): acceptable — serializes BYOK requests, latency is fine.
# Phase 2 (multi-user): migrate to google-genai new SDK (google.genai.Client(api_key=key))
# which supports per-client isolation without global state mutation. Remove lock at that point.
_byok_lock = threading.Lock()


class OracleOrchestrator:
    """
    Oracle Agentic Engine — processes user queries using native Gemini Function Calling.

    Usage:
        orchestrator = get_oracle_orchestrator_singleton()
        result = orchestrator.process_query(query="...", session_id="abc")
    """

    def __init__(self, db: DatabaseManager, llm):
        self.db = db
        self.llm = llm  # Kept for potential auxiliary LLM calls / backward compat

        # Tool registry
        self.tool_registry = ToolRegistry()
        self._register_tools()

        # Build function declarations once from registered tools (class-level, no db needed)
        self._function_declarations = self.tool_registry.get_function_declarations()
        logger.info(f"Built {len(self._function_declarations)} function declarations for Gemini")

        # Build static system prompt (no per-request state — UI filters go in first message)
        self._system_prompt = self._build_system_prompt()

        # Build the agentic model with tools and system instruction
        # Created once in __init__ — BYOK requests create a fresh instance inside _byok_lock
        self.agentic_model = self._create_agentic_model(llm_key=None)

        # Session storage: {session_id: (ConversationContext, last_active_ts)}
        self._sessions: Dict[str, Tuple[ConversationContext, datetime]] = {}
        self._session_lock = threading.Lock()

        # SQL result cache (5min TTL)
        self._sql_result_cache: TTLCache = TTLCache(maxsize=500, ttl=300)

        # Background cleanup daemon
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_expired_sessions, daemon=True
        )
        self._cleanup_thread.start()

        logger.info("OracleOrchestrator (agentic) initialized")

    # ── Tool registration ──────────────────────────────────────────────────────

    def _register_tools(self):
        for tool_class in (
            RAGTool, SQLTool, AggregationTool, GraphTool, MarketTool,
            TickerThemesTool, ReportCompareTool, ReferenceTool, SpatialTool,
        ):
            self.tool_registry.register(tool_class, db=self.db, llm=self.llm)
        logger.info(f"Registered tools: {self.tool_registry.registered_names()}")

    # ── Agentic model factory ──────────────────────────────────────────────────

    def _create_agentic_model(self, llm_key: Optional[str] = None):
        """Create a GenerativeModel with tools and system instruction.

        Args:
            llm_key: If provided, this key is active via genai.configure() — used for BYOK.
                     If None, uses the globally configured key (default).
        """
        _ = llm_key  # key is already active in genai global config when this is called
        return genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            tools=[genai.protos.Tool(function_declarations=self._function_declarations)],
            system_instruction=self._system_prompt,
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=8192,
                temperature=0.4,
                top_p=0.95,
            ),
        )

    # ── System prompt with SOPs ────────────────────────────────────────────────

    def _build_system_prompt(self) -> str:
        """Build the static system instructions with Standard Operating Procedures (SOPs).

        The SOPs encode the routing intelligence previously in QueryRouter + QueryAnalyzer:
        - Path selection by query type (factual/analytical/overview/market/etc.)
        - Time decay K values per path
        - Temporal filter extraction rules
        - Output format requirements
        """
        return """Sei The Oracle, analista senior di intelligence geopolitica e finanziaria.

## TOOL DISPONIBILI
Hai 9 strumenti specializzati. Prima di ogni chiamata, compila SEMPRE il campo `rationale` con il tuo ragionamento esplicito — questo migliora la qualità della risposta successiva.

## STANDARD OPERATING PROCEDURES (SOP)

### PATH FACTUAL — "Cosa è successo a...?", notizie, eventi, dichiarazioni recenti
- Strumento: `rag_search` con mode="both", top_k=10
- Estrai `start_date`/`end_date` dalla query (es. "ultimi 7 giorni" → start_date=oggi-7gg)
- GPE: estrai entità geografiche → `filters.gpe_filter` (in inglese: "China", "Taiwan")
- `filters.time_decay_k` = 0.03 (notizie fresche, decay aggressivo)
- FALLBACK: se rag_search restituisce 0 risultati, prova con filtri più ampi (rimuovi gpe_filter o allarga le date di ±30gg)

### PATH ANALYTICAL — "Quanti...", conteggi, trend, distribuzioni, statistiche
- Strumento principale: `sql_query` con GROUP BY
- `aggregation` per statistiche predefinite (trend_over_time, top_n, distribution, statistics)
- FALLBACK OBBLIGATORIO: se sql_query restituisce 0 righe, usa `rag_search` come secondo tentativo per trovare contesto qualitativo sul topic, così puoi almeno dichiarare cosa è presente nel DB
- NON ripetere la stessa sql_query con i medesimi parametri se ha già restituito 0

### PATH OVERVIEW — "Panorama geopolitico di...", "Situazione generale", country analysis
- Strumenti: `rag_search` (mode="both", filters.search_type="vector", top_k=15) poi `graph_navigation`
- `filters.time_decay_k` = 0.005 (includi contesto storico, decay minimo)
- Usa "vector" search per evitare AND-matching FTS su query multi-termine

### PATH MARKET — Segnali trading, macro, opportunità investimento
- Strumenti: `market_analysis` (analysis_type="signals_filter") poi `rag_search` (mode="strategic")
- `filters.time_decay_k` = 0.04 (mercati ultrasensibili al tempo)

### PATH REFERENCE — Profili paese, sanzioni, previsioni IMF, flussi commerciali
- Strumento: `reference_lookup`
- ISO3 per country_profile/macro_forecast/trade_flow (es. "CHN", "DEU", "IRN")
- ISO2 per sanctions_by_country (es. "RU", "IR")
- `filters.time_decay_k` = 0.001 (dati strutturati statici)

### PATH NARRATIVE — Evoluzione storyline, connessioni narrative
- Strumenti: `rag_search` poi `graph_navigation` (operation="storyline_cluster" se non hai ID)
- `filters.time_decay_k` = 0.02

### PATH TICKER — Analisi ticker, temi di mercato per azioni
- Strumenti: `ticker_themes` poi `rag_search` (mode="strategic", top_k=5)
- `filters.time_decay_k` = 0.03

### PATH SPATIAL — Analisi geospaziale, infrastrutture vicino a un'area, conflitti locali
TRIGGER OBBLIGATORIO: usa SEMPRE `spatial_query` come prima chiamata se la query contiene:
- "km", "raggio", "entro X km", "nel raggio di", "epicentro", "distanza"
- "asset energetici vicino a", "infrastrutture strategiche vicino a", "porti/aeroporti/pipeline vicino a"
- "conflitti locali", "hotspot", "zona di conflitto" + riferimento geografico specifico
FLOW: `spatial_query` (con center_iso3 del paese target) → poi `rag_search` per contesto narrativo
NON usare sql_query per query spaziali — sql_query non ha capacità geospaziali.

### PATH COMPARATIVE — Confronto entità/periodi, "vs", "come è cambiato"
- Strumenti: `rag_search` (top_k=10-15) poi `aggregation` (aggregation_type="top_n")
- `filters.time_decay_k` = 0.015

## REGOLE TEMPORALI
- Estrai sempre le date dalla query → inserisci in `filters.start_date`/`filters.end_date` (ISO YYYY-MM-DD)
- "Ultimi 7 giorni" → start_date = oggi - 7gg. "Da settembre" → start_date = anno corrente-09-01.
- "Ieri" → start_date = end_date = ieri. "Questa settimana" → start_date = lunedì scorso.
- Per query storiche: time_decay_k basso (0.005-0.015). Per notizie recenti: alto (0.03-0.04).

## REGOLA FALLBACK UNIVERSALE
Se il primo strumento restituisce dati vuoti/insufficienti:
1. NON ripetere lo stesso strumento con gli stessi parametri
2. Prova uno strumento alternativo (es. sql→rag, rag→sql, spatial→rag)
3. Se tutti i tentativi falliscono, sintetizza onestamente cosa non hai trovato e perché

## FORMATO RISPOSTA FINALE
Quando hai raccolto abbastanza informazioni, rispondi con questo formato:

<DOCUMENTO>
## Sintesi Esecutiva
[2-3 frasi con i punti chiave]

## Analisi Dettagliata
[Paragrafi densi con dati specifici. Cita le fonti inline usando [Titolo Fonte, Data] o [Report #ID].]

## Implicazioni Strategiche
[Implicazioni per decisori]
</DOCUMENTO>

Se i dati sono insufficienti o assenti, usa comunque il formato <DOCUMENTO> e dichiara onestamente cosa non è stato trovato, con possibili cause (periodo non indicizzato, topic assente nel DB, query troppo specifica).

## REGOLE ANTI-ALLUCINAZIONE
- Basa la risposta ESCLUSIVAMENTE sui dati restituiti dagli strumenti.
- Se dati strutturati (reference_lookup/sql_query) e RAG mostrano valori diversi per lo stesso KPI, riporta entrambi: "Dato strutturato [fonte]: X | Contesto narrativo [fonte]: Y — possibile lag temporale."
- NON generare, inferire o allucinare informazioni non presenti nei risultati degli strumenti.
"""

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
        user_context: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Process a user query end-to-end using the agentic function-calling loop.

        If user_context contains a gemini_api_key, all LLM calls use that key (BYOK).
        Returns a dict with: answer, sources, query_plan, mode, metadata.
        """
        user_gemini_key = getattr(user_context, "gemini_api_key", None)
        user_id = getattr(user_context, "user_id", None) or session_id

        if user_gemini_key:
            return self._process_with_byok_key(
                query, session_id, ui_filters, user_gemini_key, user_id
            )
        return self._process_agentic(
            query, session_id, ui_filters, model=self.agentic_model, user_id=user_id
        )

    def _process_with_byok_key(
        self,
        query: str,
        session_id: str,
        ui_filters: Optional[Dict],
        user_key: str,
        user_id: str,
    ) -> Dict[str, Any]:
        """Thread-safe BYOK: configure genai with user key, run agentic loop, restore key."""
        with _byok_lock:
            orig_key = os.getenv("GEMINI_API_KEY", "")
            genai.configure(api_key=user_key, transport="rest")
            try:
                byok_model = self._create_agentic_model(llm_key=user_key)
                return self._process_agentic(
                    query, session_id, ui_filters, model=byok_model, user_id=user_id
                )
            finally:
                genai.configure(api_key=orig_key, transport="rest")

    # ── Agentic loop ───────────────────────────────────────────────────────────

    def _process_agentic(
        self,
        query: str,
        session_id: str,
        ui_filters: Optional[Dict],
        model: Any,
        user_id: str,
    ) -> Dict[str, Any]:
        """Core agentic processing: start_chat → tool loop → final answer."""
        start_time = time.time()
        ctx = self._get_or_create_session(session_id)

        # Serialize session history for Gemini
        history = ctx.to_gemini_history()

        # Start chat with conversation history
        chat = model.start_chat(history=history)

        # Build initial message (query + UI filter hints)
        initial_msg = self._build_initial_message(query, ui_filters)

        # ── Agentic loop ────────────────────────────────────────────────────
        tool_results: List[Tuple[str, ToolResult]] = []
        sources: List[Dict] = []
        iterations_done = 0

        # Initial send_message with retry on MALFORMED_FUNCTION_CALL.
        # Gemini occasionally generates a malformed function call on first attempt;
        # retrying with a fresh session (no history) resolves it in most cases.
        _MAX_MALFORMED_RETRIES = 2
        response = None
        last_exc: Optional[Exception] = None
        for _attempt in range(1 + _MAX_MALFORMED_RETRIES):
            try:
                if _attempt > 0:
                    logger.warning(
                        f"MALFORMED_FUNCTION_CALL on attempt {_attempt}, retrying with fresh session"
                    )
                    chat = model.start_chat(history=[])
                response = chat.send_message(
                    initial_msg,
                    request_options={"timeout": 60},
                )
                break
            except Exception as e:
                last_exc = e
                if "MALFORMED_FUNCTION_CALL" not in str(e):
                    break  # Non-retryable error

        if response is None:
            logger.error(f"Initial send_message failed after {_attempt + 1} attempt(s): {last_exc}")
            return self._error_response(query, session_id, user_id, start_time, str(last_exc))

        for iteration in range(MAX_AGENTIC_ITERATIONS):
            iterations_done = iteration + 1

            # Extract function calls from response
            try:
                parts = response.candidates[0].content.parts
            except (IndexError, AttributeError) as e:
                logger.warning(f"Could not extract parts from response: {e}")
                break

            function_calls = [
                p.function_call for p in parts
                if hasattr(p, "function_call") and p.function_call.name
            ]

            if not function_calls:
                # No function calls — LLM produced a text answer
                break

            # Execute all function calls in this round
            function_responses = []
            for fc in function_calls:
                tool_name = fc.name
                tool_args = dict(fc.args) if fc.args else {}

                # Extract and log rationale (CoT forcing — not passed to Python execution)
                rationale = tool_args.pop("rationale", None)
                if rationale:
                    logger.info(f"[{tool_name}] rationale: {str(rationale)[:300]}")

                # Inject UI filters into rag_search calls (UI takes precedence)
                if tool_name == "rag_search" and ui_filters:
                    filters_in_args = tool_args.get("filters") or {}
                    for k in ("start_date", "end_date", "gpe_filter", "categories"):
                        if ui_filters.get(k) is not None and k not in filters_in_args:
                            filters_in_args[k] = ui_filters[k]
                    if filters_in_args:
                        tool_args["filters"] = filters_in_args

                # SQL result caching (5min TTL, keyed on query hash)
                cache_key = None
                if tool_name == "sql_query" and tool_args.get("query"):
                    cache_key = hashlib.md5(tool_args["query"].encode()).hexdigest()
                    if cache_key in self._sql_result_cache:
                        logger.debug(f"SQL cache hit for key {cache_key[:8]}")
                        result = self._sql_result_cache[cache_key]
                        tool_results.append((tool_name, result))
                        tool = self.tool_registry.get_tool(tool_name)
                        function_responses.append(self._make_fn_response(
                            tool_name, tool.format_for_history(result)
                        ))
                        continue

                # Execute tool
                try:
                    tool = self.tool_registry.get_tool(tool_name)
                    result = tool.execute(**tool_args)
                except Exception as e:
                    logger.error(f"Tool {tool_name} execution error: {e}")
                    result = ToolResult(success=False, data=None, error=str(e))

                # Cache successful SQL results
                if cache_key and result.success:
                    self._sql_result_cache[cache_key] = result

                tool_results.append((tool_name, result))

                # Collect sources from RAG tool for API response
                if tool_name == "rag_search" and result.success and result.data and not sources:
                    rag_instance = RAGTool(db=self.db)
                    sources = rag_instance.prepare_sources(
                        result.data.get("reports", []),
                        result.data.get("chunks", []),
                    )

                # Format compressed result for chat history
                tool = self.tool_registry.get_tool(tool_name)
                compressed = tool.format_for_history(result)
                function_responses.append(self._make_fn_response(tool_name, compressed))

            # Send all function responses back to LLM in a single message
            if function_responses:
                try:
                    msg_to_send = function_responses[0] if len(function_responses) == 1 else function_responses
                    response = chat.send_message(
                        msg_to_send,
                        request_options={"timeout": 90},
                    )
                except Exception as e:
                    logger.error(f"Failed to send function responses: {e}")
                    break

        # ── Extract final text answer ─────────────────────────────────────
        answer = self._extract_text_from_response(response)

        # If loop exhausted without text answer, force a synthesis
        if not answer and tool_results:
            logger.warning(f"Agentic loop hit max {MAX_AGENTIC_ITERATIONS} iterations, forcing synthesis")
            try:
                forced_prompt = self._build_forced_synthesis_prompt(query, tool_results)
                forced_response = chat.send_message(
                    forced_prompt,
                    request_options={"timeout": 90},
                )
                answer = self._extract_text_from_response(forced_response)
            except Exception as e:
                logger.error(f"Forced synthesis failed: {e}")

        # ── Anti-hallucination: all tools failed/empty — only if LLM produced no answer ──
        # Guard fires ONLY when answer is empty: if LLM already synthesized a "no data"
        # response (e.g. in <DOCUMENTO> format), we trust that over a canned message.
        if not answer and tool_results and all(
            not r.success or not r.data or (
                isinstance(r.data, dict) and not any(
                    bool(v) for v in r.data.values() if isinstance(v, (list, dict))
                )
            )
            for _, r in tool_results
        ):
            answer = (
                "Non ho trovato informazioni sufficienti nel database per rispondere a questa query.\n"
                "Possibili cause:\n"
                "- Il periodo temporale richiesto non ha dati indicizzati\n"
                "- L'entità geografica o categoria non corrisponde a contenuti nel DB\n"
                "- La query è troppo specifica — prova a riformularla in modo più generico"
            )

        if not answer:
            answer = "Non ho potuto elaborare una risposta basata sui dati disponibili."

        # ── Update conversation memory ────────────────────────────────────
        ctx.add_message("user", query)
        ctx.add_message("assistant", answer)

        # Track GPE entities mentioned across tool calls
        for tool_name, result in tool_results:
            if result.success and result.data and isinstance(result.data, dict):
                entities = result.data.get("gpe_filter") or []
                if entities:
                    ctx.track_entities(entities)

        # ── Log to oracle_query_log ───────────────────────────────────────
        execution_time = time.time() - start_time
        tools_used = [t for t, _ in tool_results]
        self._log_query(
            session_id=session_id,
            query=query,
            tools_used=tools_used,
            execution_time=execution_time,
            success=True,
            user_id=user_id,
            iterations=iterations_done,
        )

        return {
            "answer": answer,
            "sources": sources,
            "query_plan": {
                "intent": "agentic",
                "tools": tools_used,
                "iterations": iterations_done,
                "mode": "function_calling",
            },
            "mode": (ui_filters or {}).get("mode", "both"),
            "metadata": {
                "query": query,
                "session_id": session_id,
                "is_follow_up": ctx.detect_follow_up(query) if ctx.message_count > 2 else False,
                "execution_time": round(execution_time, 2),
                "tools_executed": tools_used,
                "timestamp": datetime.now().isoformat(),
                "iterations": iterations_done,
            },
        }

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _make_fn_response(name: str, content: str) -> Any:
        """Wrap a string result as a genai FunctionResponse Part."""
        return genai.protos.Part(
            function_response=genai.protos.FunctionResponse(
                name=name,
                response={"result": content},
            )
        )

    @staticmethod
    def _extract_text_from_response(response) -> str:
        """Extract concatenated text from all text Parts in a response."""
        try:
            parts = response.candidates[0].content.parts
            texts = [p.text for p in parts if hasattr(p, "text") and p.text]
            return "".join(texts).strip()
        except (IndexError, AttributeError):
            return ""

    @staticmethod
    def _build_initial_message(query: str, ui_filters: Optional[Dict]) -> str:
        """Build the first user message: current date + query + UI filter hints.

        The current date is always injected so the LLM can correctly compute
        relative temporal expressions ("ultimi 6 mesi", "ieri", "questa settimana").
        Without it, the LLM uses its training cutoff as reference date.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        msg = f"[TODAY = {today}]\n{query}"
        if ui_filters:
            hints = []
            if ui_filters.get("start_date"):
                hints.append(f"start_date={ui_filters['start_date']}")
            if ui_filters.get("end_date"):
                hints.append(f"end_date={ui_filters['end_date']}")
            if ui_filters.get("gpe_filter"):
                hints.append(f"gpe_filter={ui_filters['gpe_filter']}")
            if ui_filters.get("categories"):
                hints.append(f"categories={ui_filters['categories']}")
            if hints:
                msg += f"\n[UI Filters attivi: {', '.join(hints)}]"
        return msg

    @staticmethod
    def _build_forced_synthesis_prompt(query: str, tool_results: List[Tuple[str, ToolResult]]) -> str:
        """Prompt to force a final synthesis when max iterations hit without text answer."""
        results_block = "\n".join(
            f"[{name}]: {'SUCCESS' if r.success else 'FAILED: ' + str(r.error)}"
            for name, r in tool_results
        )
        return (
            f"Hai eseguito tutti gli strumenti necessari. "
            f"Strumenti usati: {results_block}\n\n"
            f"Ora sintetizza la risposta finale alla domanda originale: «{query}»\n"
            f"Segui il formato <DOCUMENTO> definito nelle istruzioni di sistema."
        )

    @staticmethod
    def _error_response(
        query: str, session_id: str, user_id: str, start_time: float, error: str
    ) -> Dict[str, Any]:
        """Minimal error response when the chat can't even start."""
        return {
            "answer": (
                "Si è verificato un errore durante l'elaborazione della query. "
                f"Dettaglio tecnico: {error}"
            ),
            "sources": [],
            "query_plan": {"intent": "error", "tools": [], "mode": "function_calling"},
            "mode": "both",
            "metadata": {
                "query": query,
                "session_id": session_id,
                "execution_time": round(time.time() - start_time, 2),
                "tools_executed": [],
                "timestamp": datetime.now().isoformat(),
                "error": error,
            },
        }

    # ── Logging ────────────────────────────────────────────────────────────────

    def _log_query(
        self,
        session_id: str,
        query: str,
        tools_used: List[str],
        execution_time: float,
        success: bool,
        user_id: Optional[str] = None,
        iterations: int = 0,
    ):
        try:
            metadata: Dict[str, Any] = {
                "tools_used": tools_used,
                "iterations": iterations,
                "mode": "agentic_function_calling",
            }
            if user_id and user_id != session_id:
                metadata["user_id"] = user_id
            self.db.log_oracle_query(
                session_id=session_id,
                query=query,
                intent="agentic",
                complexity="dynamic",
                tools_used=tools_used,
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
