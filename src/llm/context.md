# LLM Context

## Purpose
Large Language Model integration layer for intelligence report generation, RAG-based Q&A, and query analysis. Uses a **5-tier native factory pattern** (`LLMFactory`) spanning Google Gemini, Anthropic Claude, and OpenAI-compatible providers (DeepSeek, Mistral). Reports are enriched with **narrative storyline context** from the Narrative Engine.

## LLM Factory (llm_factory.py)

`LLMFactory.get(tier)` reads `config/llm_routing.yaml` and returns a typed `BaseLLMClient`:

| Tier | Model | Provider | Call Sites |
|------|-------|----------|-----------|
| T1 | Gemini 3.1 Pro (`gemini-3.1-pro-preview`) | google-generativeai | macro_analysis, strategic_report, report_compare |
| T2 | Claude Sonnet 4.6 | anthropic | Oracle agentic loop + synthesis |
| T3 | DeepSeek V3.2 | openai-compatible | structured_analysis, macro_signals, article_signals |
| T4a | Gemini 2.5 Flash-Lite | google-generativeai | query_analyzer |
| T4b | Mistral Codestral 2 | openai-compatible | sql_generation (query_router) |
| T5 | Gemini 2.5 Flash-Lite | google-generativeai | relevance_filter, bullet_generator, report_title, communities |

`GeminiClient.generate_content_raw(prompt, generation_config)` — compatibility shim for `report_generator.py` call sites that pass raw `generation_config` dicts. Remove once full migration to `generate()` is complete.

**T3 JSON schema tradeoff**: DeepSeek V3.2 via OpenAI-compatible API has no `response_schema` enforcement. Mitigation: schema injected as JSON example in prompt + single Pydantic retry via `OpenAICompatibleClient.generate_with_schema_retry()`. Monitor `ValidationError` rate in logs (target < 5%).

**T3 censorship risk — TODO: test and monitor**: DeepSeek V3.2 is trained by a Chinese company and may refuse, truncate, or produce politically filtered output on topics sensitive to the Chinese government (Taiwan sovereignty, Xinjiang/Tibet, Tiananmen, CCP leadership, Hong Kong protests, China-Russia cooperation, PLA military operations). This platform processes exactly this kind of geopolitical content. Observed behaviors to watch for:
- Silent truncation: response ends abruptly without error, Pydantic validation passes but signal fields are empty
- Soft refusal: model returns a generic "I cannot provide analysis on this topic" that passes JSON schema but has zero intelligence value
- Selective omission: China-related signals systematically absent from `extract_article_signals_with_context()` and `generate_structured_analysis()` output

**Required validation before relying on T3 for production**: Run a targeted eval on a batch of China/Taiwan/HK articles and compare structured signal output against a ground-truth baseline (manually annotated or Claude Sonnet output). If refusal rate or omission rate exceeds 5% on sensitive geopolitical topics, migrate T3 to an uncensored alternative (e.g., Mistral Large, Claude Haiku 4.5, or self-hosted Qwen). Log the comparison in `tests/evals/` as `eval_t3_censorship`.

**Required env vars**: `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, `MISTRAL_API_KEY`.

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
  - **Phase 3 — Convergence + SC**: After `build_jit_context()`, calls `match_convergences()` and `build_sc_signals_context()` from `src/macro/`. Results encapsulated in `SimpleNamespace p3` and returned via `_phase3` key in result dict.
  - **`_get_macro_metadata()`**: Reads `macro_indicator_metadata` from DB, returns `{key: {staleness_days, expected_frequency, is_stale, reliability, last_updated}}`. Used by Phase 3 for staleness-aware convergence scoring.
  - **Phase 4 — Macro Analysis LLM call #1**: `_generate_macro_analysis_v2(macro_context_raw, jit_context_block, active_convergences, sc_signals, sc_prompt_block, metadata, target_date)` — calls `gemini-2.5-flash`, validates against `MacroAnalysisResultV2` (7 Literal-constrained regime labels), persists to `macro_regime_history` via `get_macro_regime_persistence_singleton()`. Called from `generate_report()` using `_phase3` data. Returns `{'success': True, 'result': validated.model_dump()}` or `{'success': False, 'error': ...}`.
  - **Phase 5 — Strategic Report LLM call #2 (active)**: `_generate_strategic_report(macro_analysis_json, articles, storylines_xml, target_date, data_quality_flags)` — assembles prompt via `build_strategic_intelligence_prompt()`, fetches 60-day regime history XML, calls `gemini-2.5-flash` with `system_instruction`. In `generate_report()`: if v2 analysis succeeded (`use_strategic_v2=True`), uses this path and produces 7-section strategic report; otherwise falls back silently to v1 5-section prompt. 4 module-level helpers: `_format_regime_history_xml`, `_build_data_quality_flags`, `_adapt_articles_for_strategic_prompt`, `_linkify_citations`. `metadata['macro_analysis']['strategic_v2']` tracks which path was used.

- `query_analyzer.py` - Pre-search filter extraction
  - `QueryAnalyzer` class - Extracts structured filters from natural language
  - Temporal constraints, entity filters, category filters
  - Uses Gemini Flash for low latency (<500ms)

- `oracle_orchestrator.py` - **Oracle 2.0 agentic coordinator** (v4 — Claude Sonnet 4.6)
  - `OracleOrchestrator` class — Entry point for all Oracle 2.0 queries
  - **Architecture**: Anthropic Messages API with iterative `tool_use`/`tool_result` loop (max 4 iterations). State managed via explicit messages list (not ChatSession). History via `ctx.to_messages_history()`.
  - `_build_system_prompt()` — encodes 9 Standard Operating Procedures (PATH FACTUAL / ANALYTICAL / OVERVIEW / MARKET / REFERENCE / NARRATIVE / TICKER / SPATIAL / COMPARATIVE) with intent-based time decay K values
  - `_process_agentic()` — builds messages list from history, calls `ClaudeClient.generate_with_tools()`, loops on `tool_use` blocks; appends `_serialize_content(response.content)` as assistant message; tool results as user message with `tool_result` blocks
  - `_serialize_content()` — converts Anthropic `TextBlock`/`ToolUseBlock` objects to plain dicts for messages history
  - `_make_fn_response(tool_use_id, content)` — returns `{"type": "tool_result", "tool_use_id": ..., "content": ...}` dict
  - `_extract_text_from_response(response)` — extracts text from `response.content` blocks where `block.type == "text"`
  - Forced synthesis on max-iterations: calls `ClaudeClient.generate()` directly (no tool_choice manipulation)
  - **BYOK removed**: Oracle uses server-side `ANTHROPIC_API_KEY` exclusively (breaking change 2026-04-17)
  - Session management with TTL cleanup daemon thread (2h TTL, 10min cleanup interval)
  - `TTLCache` for SQL results (5min) preserved
  - Logs queries to `oracle_query_log` with `intent="agentic"`, `complexity="dynamic"`
  - `get_oracle_orchestrator_singleton()` — lazy thread-safe singleton; no longer requires `GEMINI_API_KEY`

- `query_router.py` - **Legacy config constants** (refactored — no longer used in production)
  - `QueryRouter` class removed; all routing logic migrated to SOPs in orchestrator system prompt
  - Remaining: `INTENT_EXAMPLES`, `SQL_EXAMPLES` / `_SQL_EXAMPLES` (alias), keyword sets (`ANALYTICAL_KEYWORDS`, `OVERVIEW_KEYWORDS`, etc.) — kept for eval script backward-compat
  - SQL few-shot examples migrated into `SQLTool.description` for LLM access via function declaration

- `conversation_memory.py` - **Oracle 2.0 conversation context**
  - `ConversationContext` class — deque buffer (maxlen=10), entity tracking, follow-up detection
  - `to_messages_history()` — serializes to `[{"role": "user"|"assistant", "content": str}]` plain dicts compatible with Anthropic and OpenAI APIs. Assistant messages truncated to 2000 chars.
  - `to_gemini_history()` — legacy serializer to `genai.protos.Content[]` (kept for backward-compat until Oracle 1.0 fully removed)
  - In-memory only, TTL managed by OracleOrchestrator cleanup thread

- `tools/` - **Oracle 2.0 tool registry**
  - `base.py` - `BaseTool` ABC + `ToolResult` Pydantic model
    - `format_for_history()` — compresses tool output to max `HISTORY_MAX_CHARS` chars for chat history (default 8000; overridden to 50000 in RAGTool)
    - `to_anthropic_tool()` — classmethod returns `{"name": ..., "description": ..., "input_schema": cls.parameters}` dict for Anthropic `messages.create(tools=[...])`
    - `to_function_declaration()` — legacy classmethod for Gemini `genai.protos.FunctionDeclaration` (kept for Oracle 1.0 backward-compat)
    - `_json_schema_to_genai_schema()` — legacy Gemini schema converter (kept for Oracle 1.0)
    - All tools have mandatory `rationale` as first parameter (CoT forcing; empirical +20-35% SQL accuracy on Spider/BIRD)
  - `registry.py` - `ToolRegistry` with lazy instantiation
    - `get_anthropic_tools()` — returns list of Anthropic tool dicts for all registered tools (used by OracleOrchestrator)
    - `get_function_declarations()` — legacy: returns `genai.protos.FunctionDeclaration` list (Gemini format)
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
  - **Phase 4 schemas**: `MacroAnalysisResultV2` + nested (`RiskRegimeV2`, `ActiveConvergenceItemV2`, `KeyDivergenceItemV2`, `SCSignalItemV2`, `DashboardItemV2`). `RiskRegimeV2.label` is `Literal`-constrained to 7 values — prevents LLM label drift.
  - **Oracle 2.0 schemas**: `QueryIntent` (enum, used for logging), `QueryComplexity`, `ExecutionStep`, `QueryPlan` (retained for Oracle 1.0 compat and logging)

## Dependencies

- **Internal**: `src/storage/database`, `src/nlp/processing`, `src/utils/logger`, `src/finance/`
- **External**:
  - `google-generativeai` — T1 (Gemini 3.1 Pro), T4a/T5 (Flash-Lite), narrative_processor exception (2.5 Flash)
  - `anthropic>=0.40` — T2 (Claude Sonnet 4.6) — Oracle agentic loop
  - `openai>=1.35` — T3 (DeepSeek V3.2, base_url=api.deepseek.com), T4b (Mistral Codestral 2, base_url=api.mistral.ai)
  - `pydantic` — Structured output validation
  - `sentence-transformers` — Embeddings and Cross-Encoder reranking
  - `numpy` — Vector operations
  - `sqlparse` — Safe SQL parsing for SQLTool (token-level keyword detection)
  - `cachetools` — TTLCache for SQL/embedding caching

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
