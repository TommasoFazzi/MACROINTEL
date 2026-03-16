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

- `query_analyzer.py` - Pre-search filter extraction
  - `QueryAnalyzer` class - Extracts structured filters from natural language
  - Temporal constraints, entity filters, category filters
  - Uses Gemini Flash for low latency (<500ms)

- `oracle_engine.py` - Oracle 1.0 hybrid RAG chat engine (backward-compatible)
  - `OracleEngine` class - Interactive Q&A, used by Streamlit HITL dashboard
  - Three search modes: `both`, `factual`, `strategic`
  - XML-like context injection for anti-hallucination

- `oracle_orchestrator.py` - **Oracle 2.0 main coordinator** (new)
  - `OracleOrchestrator` class - Entry point for all Oracle 2.0 queries
  - Manages: ToolRegistry (5 tools), QueryRouter, ConversationMemory, caching, LLM synthesis
  - Session management with TTL cleanup daemon thread (2h TTL, 10min cleanup interval)
  - `TTLCache` for intent (10min), SQL results (5min), embeddings (1h)
  - Anti-hallucination guard: returns structured "no data found" when all tools return empty
  - Logs queries to `oracle_query_log` DB table
  - `get_oracle_orchestrator_singleton()` — lazy thread-safe singleton

- `query_router.py` - **Oracle 2.0 query routing** (new)
  - `QueryRouter` class - Intent classification (Gemini 2.5 Flash, JSON mode) + QueryPlan
  - 5 intent types: FACTUAL, ANALYTICAL, NARRATIVE, MARKET, COMPARATIVE
  - 3 complexity levels: SIMPLE, MEDIUM, COMPLEX (rule-based heuristic)
  - Double-layer SQL injection defense: `_sanitize_user_query()` before LLM + SQLTool validates after
  - Intent cache via TTLCache (10min)

- `conversation_memory.py` - **Oracle 2.0 conversation context** (new)
  - `ConversationContext` class - deque buffer (maxlen=10), entity tracking, follow-up detection
  - In-memory only, TTL managed by OracleOrchestrator cleanup thread

- `tools/` - **Oracle 2.0 tool registry** (new package)
  - `base.py` - `BaseTool` ABC + `ToolResult` Pydantic model
  - `registry.py` - `ToolRegistry` with lazy instantiation
  - `rag_tool.py` - `RAGTool` - hybrid search with **time-weighted decay**: `score * exp(-k * days_old)` post-retrieval. Over-fetch 3x to avoid Top-K bias, min floor 0.15, K dinamico per intent (FACTUAL=0.03, MARKET=0.04, ANALYTICAL=0.015), time-shifting for historical queries (reference_date = end_date)
  - `sql_tool.py` - `SQLTool` - LLM-generated SQL with 5-layer safety (sqlparse, forbidden keywords, max 3 JOINs, LIMIT enforcement, EXPLAIN cost check ≤10000, 5s timeout)
  - `aggregation_tool.py` - `AggregationTool` - pre-parametrized stats (trend_over_time, top_n, distribution, statistics)
  - `graph_tool.py` - `GraphTool` - recursive CTE graph traversal on `storyline_edges`
  - `market_tool.py` - `MarketTool` - trade_signals and macro_indicators analysis
  - `ticker_themes_tool.py` - **`TickerThemesTool`** — Finds storylines correlated to a market ticker (Milestone B)
  - `report_compare_tool.py` - **`ReportCompareTool`** — Compares two reports via `compare_reports()` service, returns Gemini-synthesized delta (Milestone C)

- `schemas.py` - Pydantic schemas for structured LLM output
  - `IntelligenceReportMVP`, `IntelligenceReport`, `TradeSignal`, `ImpactScore`
  - `MacroCondensedContext`, `MacroDashboardItem`, `ExtractedFilters`
  - **Oracle 2.0 schemas**: `QueryIntent`, `QueryComplexity`, `ExecutionStep`, `QueryPlan`

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
