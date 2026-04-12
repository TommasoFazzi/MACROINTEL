# LLM Context

## Purpose
Large Language Model integration layer for intelligence report generation, RAG-based Q&A, and query analysis. Uses Google Gemini for text generation with structured output validation via Pydantic schemas. Reports are enriched with **narrative storyline context** from the Narrative Engine.

## Architecture Role
Intelligence synthesis layer that consumes context from the vector database and narrative graph, then produces human-readable reports. Sits between `src/storage/` (RAG retrieval), `src/nlp/narrative_processor.py` (storyline context), and `src/hitl/` (human review).

## Key Files

- `report_generator.py` - Daily/weekly report generation (~2700 lines)
  - `ReportGenerator` class - Main report generation engine
  - RAG pipeline: Query expansion → Semantic search → Reranking → LLM synthesis
  - **Narrative context** (`_get_narrative_context()`): Fetches top 10 storylines by momentum, their graph edges, and recent linked articles
  - **XML formatting** (`_format_narrative_xml()`): Formats storyline data as structured XML for the LLM prompt
  - Report structure includes 5 sections: Executive Summary, Key Developments, Trend Analysis, Investment Implications, **Strategic Storyline Tracker**
  - Cross-encoder reranking (`ms-marco-MiniLM-L-6-v2`) for precision
  - Trade signal extraction with ticker whitelist
  - Macro-first pipeline (`--macro-first` flag)
  - Output metadata includes `narrative_context` (storylines used, edges count)
  - **Citation linkification** (Phase 2): Converts `[Article N]` references in report to Markdown links `[Article N](url)` using article URLs from `recent_articles` list. Applied post-generation before header prepend.
  - **LLM title generation** (`_generate_report_title()`, `_extract_bluf_from_text()`): After report text is produced, calls `gemini-2.0-flash` with date + focus_areas + BLUF to generate a headline (max 80 chars). Stored in `metadata['title']`. Non-critical — falls back to `""` on failure.
  - **Phase 3 — Convergence + SC (log-only)**: After `build_jit_context()`, calls `match_convergences()` and `build_sc_signals_context()` from `src/macro/`. Results logged only — prompt not yet modified. Validates the Phase 3 pipeline independently before Phase 4 prompt integration.
  - **`_get_macro_metadata()`**: Reads `macro_indicator_metadata` from DB, returns `{key: {staleness_days, expected_frequency, is_stale, reliability, last_updated}}`. Used by Phase 3 for staleness-aware convergence scoring.

- `query_analyzer.py` - Pre-search filter extraction
  - `QueryAnalyzer` class - Extracts structured filters from natural language
  - Temporal constraints, entity filters, category filters
  - Uses Gemini Flash for low latency (<500ms)

- `oracle_engine.py` - Oracle 1.0 hybrid RAG chat engine (backward-compatible)
  - `OracleEngine` class - Interactive Q&A, used by Streamlit HITL dashboard
  - Three search modes: `both`, `factual`, `strategic`
  - XML-like context injection for anti-hallucination

- `oracle_orchestrator.py` - **Oracle 2.0 agentic coordinator** (Agentic rewrite)
  - `OracleOrchestrator` class — Entry point for all Oracle 2.0 queries
  - **Architecture**: Native Gemini Function Calling agentic loop (max 4 iterations) replaces the static QueryRouter → Tool Plan → Synthesis pipeline
  - `_create_agentic_model()` — creates `GenerativeModel` with all 9 tool `FunctionDeclaration` objects and system SOPs; called inside `_byok_lock` for BYOK users
  - `_build_system_prompt()` — encodes 9 Standard Operating Procedures (PATH FACTUAL / ANALYTICAL / OVERVIEW / MARKET / REFERENCE / NARRATIVE / TICKER / SPATIAL / COMPARATIVE) with intent-based time decay K values (FACTUAL=0.03, ANALYTICAL=0.015, NARRATIVE=0.02, MARKET=0.04, COMPARATIVE=0.015, TICKER=0.03, OVERVIEW=0.005, REFERENCE=0.001, SPATIAL=0.005). Includes explicit fallback rules (try rag_search after sql returns 0) and PATH SPATIAL trigger keywords ("km", "raggio", "epicentro", "infrastrutture vicino a")
  - `_process_agentic()` — runs `start_chat(history=serialized_session)` + iterative function call loop; each tool result compressed via `format_for_history()` before being added to history (RAGTool overrides to 50,000 chars); full data retained in `result.data` for source collection
  - **Anti-hallucination guard**: fires ONLY when `answer` is empty AND all tools returned empty data — does NOT override an LLM-synthesized response (fix: was unconditionally overwriting `answer`)
  - Session management with TTL cleanup daemon thread (2h TTL, 10min cleanup interval)
  - `TTLCache` for SQL results (5min) preserved; intent cache removed (routing handled by LLM)
  - BYOK: `_byok_lock` preserved; `_create_agentic_model()` called inside lock to create fresh model with user's key
  - Logs queries to `oracle_query_log` with `intent="agentic"`, `complexity="dynamic"`
  - `get_oracle_orchestrator_singleton()` — lazy thread-safe singleton

- `query_router.py` - **Legacy config constants** (refactored — no longer used in production)
  - `QueryRouter` class removed; all routing logic migrated to SOPs in orchestrator system prompt
  - Remaining: `INTENT_EXAMPLES`, `SQL_EXAMPLES` / `_SQL_EXAMPLES` (alias), keyword sets (`ANALYTICAL_KEYWORDS`, `OVERVIEW_KEYWORDS`, etc.) — kept for eval script backward-compat
  - SQL few-shot examples migrated into `SQLTool.description` for LLM access via function declaration

- `conversation_memory.py` - **Oracle 2.0 conversation context**
  - `ConversationContext` class — deque buffer (maxlen=10), entity tracking, follow-up detection
  - `to_gemini_history()` — serializes message deque to `genai.protos.Content[]` for `start_chat(history=[...])`. Assistant messages truncated to 2000 chars to prevent context overflow.
  - In-memory only, TTL managed by OracleOrchestrator cleanup thread

- `tools/` - **Oracle 2.0 tool registry**
  - `base.py` - `BaseTool` ABC + `ToolResult` Pydantic model
    - `format_for_history()` — compresses tool output to max `HISTORY_MAX_CHARS` chars for chat history (default 8000; overridden to 50000 in RAGTool)
    - `_json_schema_to_genai_schema()` — recursive classmethod converts JSON Schema dict → `genai.protos.Schema`
    - `to_function_declaration()` — classmethod generates `genai.protos.FunctionDeclaration` from class schema
    - All tools have mandatory `rationale` as first parameter (CoT forcing; empirical +20-35% SQL accuracy on Spider/BIRD)
  - `registry.py` - `ToolRegistry` with lazy instantiation
    - `get_function_declarations()` — returns list of `genai.protos.FunctionDeclaration` for all registered tools
  - `rag_tool.py` - `RAGTool` - hybrid search with **time-weighted decay**: `score * exp(-k * days_old)` post-retrieval. Over-fetch 3x to avoid Top-K bias, K dinamico per intent (FACTUAL=0.03, MARKET=0.04, ANALYTICAL=0.015), time-shifting for historical queries (reference_date = end_date). `HISTORY_MAX_CHARS=50000` (overrides base class 8000 — Gemini 2.5 Flash handles 1M tokens). **Context assembly**: chunks (articles) formatted FIRST, reports after with `_extract_report_excerpt()` (Executive Summary + most query-relevant section, max 5,000 chars per report). **Reranking pipeline**: RRF (multi-query) → cross-encoder → time-decay on `rerank_score` when cross-encoder ran → authority rerank (alpha=0.15). MIN_DECAYED_SCORE is informational only (no hard filter) to avoid discarding high cross-encoder / low similarity chunks.
  - `sql_tool.py` - `SQLTool` - LLM-generated SQL with 5-layer safety (sqlparse, forbidden keywords, max 3 JOINs, LIMIT enforcement, EXPLAIN cost check ≤10000, 5s timeout). `ALLOWED_TABLES` includes knowledge base expansion tables; uses `v_sanctions_public` (not raw `sanctions_registry`).
  - `aggregation_tool.py` - `AggregationTool` - pre-parametrized stats (trend_over_time, top_n, distribution, statistics)
  - `graph_tool.py` - `GraphTool` - recursive CTE graph traversal on `storyline_edges`
  - `market_tool.py` - `MarketTool` - trade_signals and macro_indicators analysis
  - `ticker_themes_tool.py` - **`TickerThemesTool`** — Finds storylines correlated to a market ticker (Milestone B)
  - `report_compare_tool.py` - **`ReportCompareTool`** — Compares two reports via `compare_reports()` service, returns Gemini-synthesized delta (Milestone C)
  - `reference_tool.py` - **`ReferenceTool`** — Hardcoded parameterized lookups for structured reference data. 8 lookup types: `country_profile`, `country_by_name`, `country_by_region`, `sanctions_search`, `sanctions_by_country`, `macro_forecast` (IMF WEO, latest vintage auto-selected), `macro_forecast_indicator` (cross-country comparison), `trade_flow`. All sanctions queries use `v_sanctions_public` (PII-sanitized). 10s statement timeout (vs 5s for SQLTool, needed for JOINs with country_profiles).
  - `spatial_tool.py` - **`SpatialTool`** — PostGIS spatial queries with pre-approved template whitelist. No LLM SQL generation. `SpatialQuerySpec` Pydantic model validates params (radius_km 1–2000, ALLOWED_INFRA_TYPES, ALLOWED_EVENT_TYPES). Bypasses SQLTool EXPLAIN cost check. 30s statement timeout. Returns layered results: infrastructure / conflict_events / country_boundaries.

- `schemas.py` - Pydantic schemas for structured LLM output
  - `IntelligenceReportMVP`, `IntelligenceReport`, `TradeSignal`, `ImpactScore`
  - `MacroCondensedContext`, `MacroDashboardItem`, `ExtractedFilters`
  - **Oracle 2.0 schemas**: `QueryIntent` (enum, used for logging), `QueryComplexity`, `ExecutionStep`, `QueryPlan` (retained for Oracle 1.0 compat and logging)

## Dependencies

- **Internal**: `src/storage/database`, `src/nlp/processing`, `src/utils/logger`, `src/finance/`
- **External**:
  - `google-generativeai` - Gemini API (gemini-2.5-flash)
  - `pydantic` - Structured output validation
  - `sentence-transformers` - Embeddings and Cross-Encoder reranking
  - `numpy` - Vector operations
  - `sqlparse` - Safe SQL parsing for SQLTool (token-level keyword detection)
  - `tenacity` - LLM retry with exponential backoff (2 attempts, 2–10s wait)
  - `cachetools` - TTLCache for intent/SQL/embedding caching

## Data Flow

- **Input**:
  - Recent articles from database (last 24h-7d)
  - RAG context from semantic search on chunks
  - **Narrative storyline context** from `v_active_storylines` and `v_storyline_graph`
  - Historical reports for Oracle context

- **Output**:
  - `reports/intelligence_report_{timestamp}.json` - Structured report
  - `reports/intelligence_report_{timestamp}.md` - Markdown report (now includes Storyline Tracker section)
  - `reports/WEEKLY_REPORT_{date}.md` - Weekly meta-analysis
  - Trade signals with intelligence scores
