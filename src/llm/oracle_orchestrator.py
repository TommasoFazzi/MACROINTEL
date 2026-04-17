"""
OracleOrchestrator — Oracle Agentic Engine (v4, Claude Sonnet 4.6).

Architecture: Anthropic Messages API with iterative tool use loop.

Flow:
    User Query
        → messages.create(system=SOPs, tools=..., messages=history+query)
        → [Claude decides which tool(s) to call based on SOPs]
        → execute tool → append tool_result blocks as user message
        → [repeat up to MAX_AGENTIC_ITERATIONS]
        → Claude produces final text answer (stop_reason=end_turn)

Key differences from v3 (Gemini):
- Tool definitions in Anthropic JSON schema format (not Gemini protobuf)
- Multi-turn state managed via messages list (not ChatSession)
- History serialized via to_messages_history() (not to_gemini_history())
- BYOK removed — Oracle uses server-side ANTHROPIC_API_KEY exclusively

Preserved from v3:
- 5-layer SQL safety in SQLTool
- Time-weighted decay config (passed via system prompt SOPs)
- Source authority integration in RAGTool
- Session management with TTL cleanup (2h)
- Per-session SQL result caching (5min)
- oracle_query_log logging
- Anti-hallucination guard for all-empty results
"""

import hashlib
import os
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from cachetools import TTLCache
from dotenv import load_dotenv
from pathlib import Path

from .conversation_memory import ConversationContext
from .llm_factory import LLMFactory, ClaudeClient
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


class OracleOrchestrator:
    """
    Oracle Agentic Engine — processes user queries using Anthropic native tool use.

    Usage:
        orchestrator = get_oracle_orchestrator_singleton()
        result = orchestrator.process_query(query="...", session_id="abc")
    """

    def __init__(self, db: DatabaseManager):
        self.db = db

        # Tool registry
        self.tool_registry = ToolRegistry()
        self._register_tools()

        # Build Anthropic tool definitions once from registered tools (class-level, no db needed)
        self._anthropic_tools = self.tool_registry.get_anthropic_tools()
        logger.info(f"Built {len(self._anthropic_tools)} Anthropic tool definitions")

        # Build static system prompt (no per-request state — UI filters go in first message)
        self._system_prompt = self._build_system_prompt()

        # Prompt caching: if ANTHROPIC_PROMPT_CACHING=true, wrap system prompt as a
        # cache_control block so Anthropic caches the static ~3000-token prefix.
        # Falls back gracefully to plain string if env var is absent or false.
        if os.environ.get("ANTHROPIC_PROMPT_CACHING", "").lower() == "true":
            self._system_for_api: str | list = [
                {"type": "text", "text": self._system_prompt, "cache_control": {"type": "ephemeral"}}
            ]
            logger.info("Anthropic prompt caching enabled for Oracle system prompt")
        else:
            self._system_for_api = self._system_prompt

        # Claude client (T2 — Claude Sonnet 4.6)
        self._claude_client: ClaudeClient = LLMFactory.get("t2")

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

        logger.info("OracleOrchestrator (Claude Sonnet 4.6) initialized")

    # ── Tool registration ──────────────────────────────────────────────────────

    def _register_tools(self):
        for tool_class in (
            RAGTool, SQLTool, AggregationTool, GraphTool, MarketTool,
            TickerThemesTool, ReportCompareTool, ReferenceTool, SpatialTool,
        ):
            self.tool_registry.register(tool_class, db=self.db)
        logger.info(f"Registered tools: {self.tool_registry.registered_names()}")

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
        """Process a user query end-to-end using the agentic tool use loop.

        Returns a dict with: answer, sources, query_plan, mode, metadata.
        """
        user_id = getattr(user_context, "user_id", None) or session_id
        return self._process_agentic(query, session_id, ui_filters, user_id=user_id)

    # ── Agentic loop (Anthropic) ───────────────────────────────────────────────

    def _process_agentic(
        self,
        query: str,
        session_id: str,
        ui_filters: Optional[Dict],
        user_id: str,
    ) -> Dict[str, Any]:
        """Core agentic loop: Anthropic messages API with tool_use/tool_result blocks."""
        start_time = time.time()
        ctx = self._get_or_create_session(session_id)

        # Build messages list: history + initial user message
        messages: List[Dict] = ctx.to_messages_history()
        initial_msg = self._build_initial_message(query, ui_filters)
        messages.append({"role": "user", "content": initial_msg})

        # State tracking
        tool_results_log: List[Tuple[str, ToolResult]] = []
        sources: List[Dict] = []
        iterations_done = 0

        # Initial API call
        try:
            response = self._claude_client.generate_with_tools(
                messages=messages,
                tools=self._anthropic_tools,
                system=self._system_for_api,
                temperature=0.4,
                top_p=0.95,
                max_tokens=8192,
            )
        except Exception as e:
            logger.error(f"Initial Claude API call failed: {e}")
            return self._error_response(query, session_id, user_id, start_time, str(e))

        # ── Agentic loop ────────────────────────────────────────────────────
        for iteration in range(MAX_AGENTIC_ITERATIONS):
            iterations_done = iteration + 1

            # Extract tool_use blocks from response
            tool_use_blocks = [
                block for block in response.content
                if getattr(block, "type", None) == "tool_use"
            ]

            if not tool_use_blocks:
                # No tool calls — Claude produced a final text answer
                break

            # Append Claude's assistant response (with tool_use blocks) to messages
            messages.append({
                "role": "assistant",
                "content": self._serialize_content(response.content),
            })

            # Execute tools and build tool_result content blocks
            tool_result_blocks: List[Dict] = []
            for block in tool_use_blocks:
                tool_name = block.name
                tool_args = dict(block.input) if block.input else {}
                tool_use_id = block.id

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
                        cached = self._sql_result_cache[cache_key]
                        tool_results_log.append((tool_name, cached))
                        tool = self.tool_registry.get_tool(tool_name)
                        tool_result_blocks.append(
                            self._make_fn_response(tool_use_id, tool.format_for_history(cached))
                        )
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

                tool_results_log.append((tool_name, result))

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
                tool_result_blocks.append(self._make_fn_response(tool_use_id, compressed))

            # Append tool results as a user message
            if tool_result_blocks:
                messages.append({"role": "user", "content": tool_result_blocks})

            # Next API call
            try:
                response = self._claude_client.generate_with_tools(
                    messages=messages,
                    tools=self._anthropic_tools,
                    system=self._system_for_api,
                    temperature=0.4,
                    top_p=0.95,
                    max_tokens=8192,
                )
            except Exception as e:
                logger.error(f"Claude API call failed on iteration {iterations_done}: {e}")
                break

        # ── Extract final text answer ─────────────────────────────────────
        answer = self._extract_text_from_response(response)

        # If loop exhausted without text answer, force a synthesis via generate()
        if not answer and tool_results_log:
            logger.warning(f"Agentic loop hit max {MAX_AGENTIC_ITERATIONS} iterations, forcing synthesis")
            try:
                forced_prompt = self._build_forced_synthesis_prompt(query, tool_results_log)
                answer = self._claude_client.generate(
                    prompt=forced_prompt,
                    system=self._system_for_api,
                    temperature=0.4,
                    max_tokens=8192,
                )
            except Exception as e:
                logger.error(f"Forced synthesis failed: {e}")

        # ── Anti-hallucination: all tools failed/empty — only if LLM produced no answer ──
        # Guard fires ONLY when answer is empty: if LLM already synthesized a "no data"
        # response (e.g. in <DOCUMENTO> format), we trust that over a canned message.
        if not answer and tool_results_log and all(
            not r.success or not r.data or (
                isinstance(r.data, dict) and not any(
                    bool(v) for v in r.data.values() if isinstance(v, (list, dict))
                )
            )
            for _, r in tool_results_log
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
        for tool_name, result in tool_results_log:
            if result.success and result.data and isinstance(result.data, dict):
                entities = result.data.get("gpe_filter") or []
                if entities:
                    ctx.track_entities(entities)

        # ── Log to oracle_query_log ───────────────────────────────────────
        execution_time = time.time() - start_time
        tools_used = [t for t, _ in tool_results_log]
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
    def _make_fn_response(tool_use_id: str, content: str) -> Dict:
        """Build an Anthropic tool_result content block dict."""
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content,
        }

    @staticmethod
    def _extract_text_from_response(response) -> str:
        """Extract concatenated text from all text blocks in an Anthropic response."""
        try:
            return "".join(
                block.text
                for block in response.content
                if getattr(block, "type", None) == "text" and block.text
            ).strip()
        except (AttributeError, TypeError):
            return ""

    @staticmethod
    def _serialize_content(content_blocks) -> List[Dict]:
        """Serialize Anthropic content blocks to plain dicts for messages history."""
        result = []
        for block in content_blocks:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                result.append({"type": "text", "text": block.text})
            elif block_type == "tool_use":
                result.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": dict(block.input) if block.input else {},
                })
        return result

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
                _orchestrator = OracleOrchestrator(db=db)
    return _orchestrator
