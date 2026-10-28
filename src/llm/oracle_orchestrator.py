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
import json
import os
import re
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
from .rag_evidence import digest_rag_result
from .tools.rag_tool import DEFAULT_DECAY_K, RAGTool
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
MAX_TOTAL_TOOL_CALLS = 6         # Hard cap on total tool calls across all iterations (rate limit protection)
_SUMMARY_THRESHOLD = 1500        # chars; below this, tool results go into history verbatim
# The system prompt asks for a <DOCUMENT>...</DOCUMENT> wrapper to structure the answer;
# react-markdown escapes raw HTML, so left in it shows as literal text in the UI.
_DOCUMENT_TAG_RE = re.compile(r"</?DOCUMENT(?:O)?>")


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

        # Compression client (T5 — Flash-Lite, summarizes tool results for chat history)
        self._t5_client = LLMFactory.get("t5")

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
        return """You are The Oracle, a senior geopolitical and financial intelligence analyst.

## LANGUAGE
- Write the final answer in the language of the user's question (Italian question → Italian answer; English question → English answer). Keep the section headings of the answer format in that language too.
- Always write tool arguments in English, whatever the language of the question: the `query` of `rag_search`, `gpe_filter` values, SQL, and `rationale`. The document corpus is mostly English, so an English query retrieves far better than an Italian one. Translate the topic of the question into a concise English search query.
- Source titles, quotations and proper names stay in their original language.

## AVAILABLE TOOLS
You have 9 specialised tools. Before every call, ALWAYS fill the `rationale` field with your explicit reasoning — this improves the quality of the next answer.

## GPE RULE — GEOGRAPHIC ENTITY EXTRACTION (applies to ALL paths using rag_search)

When the query names countries or geographic areas, set `filters.gpe_filter` following these rules:

**Case A — Countries explicitly named without a regional qualifier**
E.g. "Russia and China in the Pacific" → gpe_filter=["Russia", "China", "Pacific Ocean"]
→ Narrow filter: include only the named countries + the geographic theatre.

**Case B — Query with a regional qualifier** ("European", "NATO", "Mediterranean", "Asian", etc.)
E.g. "European fleets in the Hormuz crisis, in particular Italian, French and British ones"
→ gpe_filter=["Italy", "France", "United Kingdom", "Strait of Hormuz", "Iran"] as the core, BUT let the RAG also retrieve results on other countries of the same region (Spain, Greece, Germany, etc.) that appear in the documents — do not exclude them up front.
→ Rule of thumb: for queries with a regional qualifier, use a higher top_k (15-20) to capture regional variety.

**Gradual fallback** if rag_search with gpe_filter returns 0 results:
a. First attempt: remove the theatre entities (keep only the subject countries)
b. Second attempt: remove gpe_filter entirely and widen the dates by ±30 days
Do NOT jump straight to step (b).

## STANDARD OPERATING PROCEDURES (SOP)

### PATH FACTUAL — "What happened in...?", news, events, recent statements
- Tool: `rag_search` with mode="both", top_k=10
- Extract `start_date`/`end_date` from the query (e.g. "last 7 days" → start_date=today-7d)
- GPE: see GPE RULE above → set `filters.gpe_filter`
- `filters.time_decay_k` = 0.03 (fresh news, aggressive decay)
- FALLBACK: if rag_search returns 0 results, apply the gradual GPE fallback, then widen the dates

### PATH ANALYTICAL — "How many...", counts, trends, distributions, statistics
- Main tool: `sql_query` with GROUP BY
- `aggregation` for predefined statistics (trend_over_time, top_n, distribution, statistics)
- MANDATORY FALLBACK: if sql_query returns 0 rows, use `rag_search` as a second attempt to find qualitative context on the topic, so that you can at least state what is present in the DB
- Do NOT repeat the same sql_query with identical parameters if it already returned 0

### PATH OVERVIEW — "Geopolitical overview of...", "General situation", country analysis
- Tools: `rag_search` (mode="both", filters.search_type="vector", top_k=15) then `graph_navigation`
- `filters.time_decay_k` = 0.005 (include historical context, minimal decay)
- GPE: see GPE RULE above → set `filters.gpe_filter` with the countries/areas explicitly mentioned
- Use "vector" search to avoid FTS AND-matching on multi-term queries

### PATH MARKET — Trading signals, macro, investment opportunities
- Tools: `market_analysis` (analysis_type="signals_filter") then `rag_search` (mode="strategic")
- `filters.time_decay_k` = 0.04 (markets are extremely time-sensitive)

### PATH REFERENCE — Country profiles, sanctions, IMF forecasts, trade flows
- Tool: `reference_lookup`
- ISO3 for country_profile/macro_forecast/trade_flow (e.g. "CHN", "DEU", "IRN")
- ISO2 for sanctions_by_country (e.g. "RU", "IR")
- `filters.time_decay_k` = 0.001 (static structured data)

### PATH NARRATIVE — Storyline evolution, narrative connections
- Tools: `rag_search` then `graph_navigation` (operation="storyline_cluster" if you have no ID)
- `filters.time_decay_k` = 0.02

### PATH TICKER — Ticker analysis, market themes for stocks
- Tools: `ticker_themes` then `rag_search` (mode="strategic", top_k=5)
- `filters.time_decay_k` = 0.03

### PATH SPATIAL — Geospatial analysis, infrastructure near an area, local conflicts
MANDATORY TRIGGER: ALWAYS use `spatial_query` as the first call if the query contains:
- "km", "radius", "within X km", "epicentre", "distance" (or the Italian "raggio", "entro X km", "nel raggio di", "epicentro", "distanza")
- "energy assets near", "strategic infrastructure near", "ports/airports/pipelines near"
- "local conflicts", "hotspot", "conflict zone" + a specific geographic reference
FLOW: `spatial_query` (with center_iso3 of the target country) → then `rag_search` for narrative context
Do NOT use sql_query for spatial queries — sql_query has no geospatial capabilities.

### PATH COMPARATIVE — Comparison of entities/periods, "vs", "how has it changed"
- Tools: `rag_search` (top_k=10-15) then `aggregation` (aggregation_type="top_n")
- `filters.time_decay_k` = 0.015

## TEMPORAL RULES
- Always extract dates from the query → put them in `filters.start_date`/`filters.end_date` (ISO YYYY-MM-DD)
- "Last 7 days" → start_date = today - 7d. "Since September" → start_date = current year-09-01.
- "Yesterday" → start_date = end_date = yesterday. "This week" → start_date = last Monday.
- For historical queries: low time_decay_k (0.005-0.015). For recent news: high (0.03-0.04).

## UNIVERSAL FALLBACK RULE
If the first tool returns empty/insufficient data:
1. Do NOT repeat the same tool with the same parameters
2. Try an alternative tool (e.g. sql→rag, rag→sql, spatial→rag)
3. If every attempt fails, honestly summarise what you did not find and why

## FINAL ANSWER FORMAT
When you have gathered enough information, answer in this format (translate the headings into the answer language):

<DOCUMENT>
## Executive Summary
[2-3 sentences with the key points]

## Detailed Analysis
[Dense paragraphs with specific data, cited according to the SOURCE RULE — CITE OR OMIT.]

## Strategic Implications
[Implications for decision-makers]
</DOCUMENT>

If the tools return no relevant documents, still use the <DOCUMENT> format and state this following the ABSENCE RULE.

## SOURCE RULE — CITE OR OMIT
Each block in the rag_search results starts with the header `[N] TYPE — source — date — title`: N is the source number in the SOURCE REGISTRY.
- Every concrete claim (who did what, a number, a name, a place, a date) MUST cite the `[N]` of the block that contains it. Example: "Troops crossed the border on 3 March [2]."
- If no block supports a specific detail, OMIT the detail: keep the general statement or drop it. Never assign an `[N]` to information that block does not contain.
- Respect the strength of the source: "according to rumours", "plans to", "is reportedly considering" must not become "announced" or "confirmed".
- Analysis, inferences and scenarios (what the facts mean, where they may lead) are exempt from citation, but must not introduce new concrete facts. When an inference goes beyond the sources, say so explicitly ("Analytical assessment: …").
- If a source already contains an analysis or a recommendation, attribute it to the source (outlet, title and `[N]`) instead of presenting it as your own.

## ABSENCE RULE
- You may state that the database holds no material on an aspect ONLY if no block returned by the tools is relevant to that aspect.
- If relevant blocks exist, do NOT declare absence: name them (outlet, title, `[N]`) and use them, even if they cover only part of the question. You may indicate which specific aspects remain uncovered.

## ANTI-HALLUCINATION RULES
- Base the answer EXCLUSIVELY on the data returned by the tools.
- For every country, entity or KPI you mention in the answer, an explicit source must exist in the tool results. Countries found in the RAG documents are legitimate even if not named in the query. Countries added from your own knowledge (not in the tool results) are NOT.
- If structured data (reference_lookup/sql_query) and RAG show different values for the same KPI, report both: "Structured data [source]: X | Narrative context [source]: Y — possible time lag."
- Do NOT generate, infer or hallucinate information absent from the tool results. In particular: do NOT add countries or actors that appear in no tool result, even if the connection seems logical or plausible.
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
        source_numbers: Dict[Tuple[str, Any], int] = {}  # (type, id) -> global [N]
        rag_calls: List[Dict[str, Any]] = []              # per-call evidence for the query log
        iterations_done = 0
        total_tool_calls = 0

        # Initial API call
        try:
            response = self._claude_client.generate_with_tools(
                messages=messages,
                tools=self._anthropic_tools,
                system=self._system_for_api,
                temperature=0.4,
                max_tokens=6000,
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
                            self._make_fn_response(
                                tool_use_id,
                                self._summarize_for_history(tool_name, tool.format_for_history(cached)),
                            )
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

                tool = self.tool_registry.get_tool(tool_name)
                if tool_name == "rag_search":
                    # Attributed digest with global [N] — never the free-text summarizer,
                    # which drops titles/sources and breaks citation numbering.
                    history_content = self._rag_tool_result(
                        tool, result, tool_args, sources, source_numbers, rag_calls,
                    )
                else:
                    # Summarize result for chat history (T5 if too long, verbatim otherwise)
                    history_content = self._summarize_for_history(tool_name, tool.format_for_history(result))
                tool_result_blocks.append(self._make_fn_response(tool_use_id, history_content))

            # Append tool results as a user message; include numbered source registry
            # as a text block so the LLM can cite [N] in its synthesis.
            if tool_result_blocks:
                content: List[Dict] = list(tool_result_blocks)
                if sources:
                    content.append({"type": "text", "text": self._build_source_registry(sources)})
                messages.append({"role": "user", "content": content})

            # Hard cap: tools already executed and results appended — now check if we should
            # force synthesis instead of continuing. This ordering ensures every tool_use block
            # always has a corresponding tool_result (Anthropic API requirement).
            total_tool_calls += len(tool_use_blocks)
            if total_tool_calls >= MAX_TOTAL_TOOL_CALLS:
                messages.append({
                    "role": "user",
                    "content": f"You have reached the limit of {MAX_TOTAL_TOOL_CALLS} searches. Synthesise now with the information gathered.",
                })
                try:
                    response = self._claude_client.generate_with_tools(
                        messages=messages,
                        tools=[],  # Empty tools forces text-only response
                        system=self._system_for_api,
                        temperature=0.4,
                        max_tokens=6000,
                    )
                except Exception as e:
                    logger.error(f"Claude API call failed on forced synthesis: {e}")
                break

            # Next API call
            try:
                response = self._claude_client.generate_with_tools(
                    messages=messages,
                    tools=self._anthropic_tools,
                    system=self._system_for_api,
                    temperature=0.4,
                    max_tokens=6000,
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
                    max_tokens=6000,
                )
            except Exception as e:
                logger.error(f"Forced synthesis failed: {e}")

        # ── Anti-hallucination: all tools failed/empty — only if LLM produced no answer ──
        # Guard fires ONLY when answer is empty: if LLM already synthesized a "no data"
        # response (e.g. in <DOCUMENT> format), we trust that over a canned message.
        if not answer and tool_results_log and all(
            not r.success or not r.data or (
                isinstance(r.data, dict) and not any(
                    bool(v) for v in r.data.values() if isinstance(v, (list, dict))
                )
            )
            for _, r in tool_results_log
        ):
            answer = (
                "I did not find enough information in the database to answer this query.\n"
                "Possible causes:\n"
                "- The requested time period has no indexed data\n"
                "- The geographic entity or category matches no content in the DB\n"
                "- The query is too specific — try rephrasing it more generally"
            )

        if not answer:
            answer = "I could not produce an answer based on the available data."
        answer = _DOCUMENT_TAG_RE.sub("", answer).strip()

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
            response=answer,
            rag_calls=rag_calls,
            sources=sources,
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

    def _rag_tool_result(
        self,
        tool: RAGTool,
        result: ToolResult,
        tool_args: Dict[str, Any],
        sources: List[Dict],
        source_numbers: Dict[Tuple[str, Any], int],
        rag_calls: List[Dict[str, Any]],
    ) -> str:
        """Register the documents Sonnet will read and build their attributed digest.

        Only documents rendered into the tool_result are registered, so every [N]
        in the SOURCE REGISTRY / sidebar is something Sonnet actually received. A
        document seen in an earlier call keeps its number.
        """
        if not result.success:
            return tool.format_for_history(result)

        docs = tool.select_context_documents(result.data or {})
        numbers: List[int] = []
        for doc in docs:
            n = source_numbers.get(doc.key)
            if n is None:
                sources.append(doc.to_source())
                n = len(sources)
                source_numbers[doc.key] = n
            numbers.append(n)

        digest = digest_rag_result(docs, numbers, self._t5_client)

        filters = tool_args.get("filters") or {}
        rag_calls.append({
            "query": tool_args.get("query"),
            # ui_filters inject datetime objects; keep the log row JSON-serializable
            "filters": json.loads(json.dumps(filters, default=str)),
            "time_decay_k": filters.get("time_decay_k", DEFAULT_DECAY_K),
            "n_chunks": sum(1 for d in docs if d.kind == "ARTICOLO"),
            "n_reports_rendered": sum(1 for d in docs if d.kind == "REPORT"),
            "digest_mode": digest.mode,
            "fallback_docs": digest.fallback_docs,
        })
        if digest.mode == "fallback":
            logger.warning(f"RAG digest fell back to snippets for {len(docs)} documents")
        return digest.text

    def _summarize_for_history(self, tool_name: str, content: str) -> str:
        """Compress a non-RAG tool result for chat history using T5 (Flash-Lite).

        `rag_search` results never come here — see _rag_tool_result.
        Only invoked when content exceeds _SUMMARY_THRESHOLD. Falls back to
        hard truncation if the LLM call fails, so the agentic loop never stalls.
        """
        if len(content) <= _SUMMARY_THRESHOLD:
            return content

        prompt = (
            f"The tool '{tool_name}' returned the following output:\n\n"
            f"{content}\n\n"
            "Write a concise summary (max 400 words) that preserves ALL of:\n"
            "- Numbers, percentages, monetary values, dates\n"
            "- Names of countries, people, organisations, tickers\n"
            "- Key findings and factual statements\n"
            "- Any errors or empty-result notices\n"
            "Do NOT add interpretation or commentary. Output only the summary."
        )
        try:
            return self._t5_client.generate(prompt=prompt, temperature=0.1, max_tokens=600)
        except Exception as e:
            logger.warning(f"Tool result summarization failed for '{tool_name}': {e} — falling back to truncation")
            cutoff = content[:_SUMMARY_THRESHOLD].rstrip()
            return cutoff + "\n[... truncated: summarization unavailable]"

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
    def _build_source_registry(sources: List[Dict]) -> str:
        """Build a numbered source registry injected into tool_result messages.

        Lets Claude cite [N] in the synthesis response with numbers that match
        the sidebar's sequential display order.
        """
        lines = ["SOURCE REGISTRY (cite with [N] in the final answer):"]
        for i, src in enumerate(sources, 1):
            title = src.get("title", "N/A")
            date = src.get("date_str", "")
            date_part = f" — {date}" if date else ""
            lines.append(f"[{i}] {title}{date_part}")
        return "\n".join(lines)

    @staticmethod
    def _build_forced_synthesis_prompt(query: str, tool_results: List[Tuple[str, ToolResult]]) -> str:
        """Prompt to force a final synthesis when max iterations hit without text answer."""
        results_block = "\n".join(
            f"[{name}]: {'SUCCESS' if r.success else 'FAILED: ' + str(r.error)}"
            for name, r in tool_results
        )
        return (
            f"You have run all the necessary tools. "
            f"Tools used: {results_block}\n\n"
            f"Now synthesise the final answer to the original question: «{query}»\n"
            f"Follow the <DOCUMENT> format defined in the system instructions, in the language of the question."
        )

    @staticmethod
    def _error_response(
        query: str, session_id: str, user_id: str, start_time: float, error: str
    ) -> Dict[str, Any]:
        """Minimal error response when the chat can't even start."""
        return {
            "answer": (
                "An error occurred while processing the query. "
                f"Technical detail: {error}"
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
        response: Optional[str] = None,
        rag_calls: Optional[List[Dict[str, Any]]] = None,
        sources: Optional[List[Dict]] = None,
    ):
        try:
            metadata: Dict[str, Any] = {
                "tools_used": tools_used,
                "iterations": iterations,
                "mode": "agentic_function_calling",
            }
            if user_id and user_id != session_id:
                metadata["user_id"] = user_id
            if response:
                metadata["response"] = response
            # Retrieval evidence: enough to reconstruct what Sonnet read without a replay
            if rag_calls:
                metadata["rag_calls"] = rag_calls
            if sources:
                metadata["sources"] = [
                    {"n": n, "type": s.get("type"), "id": s.get("id"),
                     "title": s.get("title"), "source": s.get("source")}
                    for n, s in enumerate(sources, 1)
                ]
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
