# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This project uses both Python (backend/pipelines) and TypeScript (web-platform). Key directories contain context.md files describing their purpose. Read relevant context.md files before making changes in a directory.

INTELLIGENCE_ITA is an end-to-end geopolitical intelligence news analysis platform. It ingests 33+ RSS feeds, processes articles with NLP (spaCy + sentence-transformers), stores them in PostgreSQL with pgvector for semantic search, generates intelligence reports via Google Gemini LLM with RAG, and provides a human-in-the-loop review dashboard. A **Narrative Engine** tracks storylines across articles using HDBSCAN clustering, LLM-driven summary evolution, and a graph of inter-storyline relationships. A Next.js web platform serves as the public-facing frontend with a force-directed graph visualization of the narrative network.

## Common Commands

### Python Backend

```bash
# All commands run from the inner INTELLIGENCE_ITA/ directory (where src/, scripts/, requirements.txt live)

# Run all tests
pytest tests/ -v

# Run specific test category
pytest tests/test_ingestion/ -v
pytest tests/test_nlp/ -v
pytest tests/test_storage/ -v
pytest tests/test_llm/ -v

# Run a single test file
pytest tests/test_llm/test_report_generator.py -v

# Run a single test function
pytest tests/test_llm/test_report_generator.py::test_function_name -v

# Run by marker
pytest -m unit
pytest -m "not slow"

# Coverage
pytest tests/ --cov=src --cov-report=html

# Linting (tools are in requirements-dev.txt, some commented out)
black src/ scripts/
flake8 src/ scripts/ --max-line-length=120
ruff check src/

# Run pipeline steps individually
python -m src.ingestion.pipeline          # 1. Ingest RSS feeds
python scripts/process_nlp.py             # 2. NLP processing
python scripts/load_to_database.py        # 3. Load to database
python scripts/process_narratives.py      # 4. Narrative processing (storylines + graph)
python scripts/generate_report.py         # 5. Generate LLM report

# Full automated pipeline
python scripts/daily_pipeline.py

# Report generation with options
python scripts/generate_report.py --days 3 --macro-first --skip-article-signals

# HITL Dashboard (Streamlit)
streamlit run Home.py

# FastAPI backend
uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000

# System health check
python scripts/check_setup.py

# Daily status check (also runs via launchd at 9:00 AM)
python scripts/pipeline_status_check.py
```

### Next.js Frontend (web-platform/)

```bash
cd web-platform
npm install
npm run dev       # Dev server at http://localhost:3000
npm run build     # Production build
npm run lint      # ESLint
```

## Architecture

### Data Pipeline Flow

```
RSS Feeds (33) → Ingestion → NLP Processing → PostgreSQL+pgvector → Narrative Engine → RAG+LLM → HITL Review
                                                                          ↓
                                                                   Storyline Graph → API → Frontend Visualization
```

**Seven phases:**
1. **Ingestion** (`src/ingestion/`): Async RSS parsing via aiohttp (parallel feed fetching + concurrent content extraction), full-text extraction (Trafilatura primary, Newspaper3k fallback, Cloudscraper for anti-bot sites), 2-phase deduplication (hash + content), **keyword blocklist filter** (off-topic rejection at ingestion)
2. **NLP** (`src/nlp/`): spaCy multilingual NER (`xx_ent_wiki_sm`), semantic chunking (500-word sliding window), embeddings (`paraphrase-multilingual-MiniLM-L12-v2`, 384-dim), **LLM relevance classification** (Gemini-based scope filter)
3. **Storage** (`src/storage/database.py`): PostgreSQL + pgvector with HNSW indexing, connection pooling (psycopg2 SimpleConnectionPool)
4. **Narrative Engine** (`src/nlp/narrative_processor.py`): HDBSCAN micro-clustering of orphan events, embedding-based matching to existing storylines, LLM summary evolution (Gemini), Jaccard entity-overlap graph edges, momentum scoring with decay, **post-clustering validation filter** (regex-based off-topic archival)
5. **Report Generation** (`src/llm/`): Google Gemini 2.5 Flash, 2-stage RAG (vector search → cross-encoder reranking with ms-marco-MiniLM), **narrative storyline context** (top 10 storylines injected as XML), trade signal extraction, "Strategic Storyline Tracker" section
6. **HITL** (`src/hitl/`, `Home.py`): Streamlit dashboard for review, editing, rating, feedback loop
7. **Automation** (`scripts/daily_pipeline.py`): 6 core steps (ingestion → market_data → nlp_processing → load_to_database → **narrative_processing** → generate_report) + 2 conditional steps (weekly_report on Sundays → monthly_recap after 4 weekly reports), launchd scheduling at 8:00 AM; `pipeline_status_check.py` runs at 9:00 AM

### Key Modules by Size/Complexity

- `src/llm/report_generator.py` (~2700 lines) — Core LLM integration, RAG pipeline, trade signals, narrative storyline context
- `src/storage/database.py` (~1921 lines) — All PostgreSQL/pgvector operations
- `src/nlp/narrative_processor.py` (~940 lines) — **Narrative Engine**: HDBSCAN clustering, storyline matching, LLM evolution, graph edges, momentum decay, post-clustering validation
- `src/nlp/story_manager.py` (~970 lines) — Legacy storyline clustering (replaced by narrative_processor)
- `src/nlp/processing.py` (~610 lines) — NLP pipeline: cleaning, chunking, NER, embeddings
- `src/nlp/relevance_filter.py` — LLM-based article relevance classification (Gemini)
- `src/integrations/openbb_service.py` (~1026 lines) — OpenBB financial data integration
- `src/llm/oracle_engine.py` (~566 lines) — Oracle 1.0 RAG chat engine (backward-compat, used by Streamlit HITL)
- `src/llm/oracle_orchestrator.py` — **Oracle 2.0 main coordinator**: ToolRegistry + QueryRouter + ConversationMemory + caching + LLM synthesis + anti-hallucination; `get_oracle_orchestrator_singleton()` for FastAPI
- `src/llm/query_router.py` — Intent classification (Gemini 2.5 Flash) + QueryPlan generation; double-layer SQL injection defense
- `src/llm/tools/` — Tool package: RAGTool, SQLTool (5-layer safety), AggregationTool, GraphTool, MarketTool
- `src/api/main.py` + `src/api/auth.py` + `src/api/routers/` — FastAPI backend: X-API-Key auth (`secrets.compare_digest`), CORS (GET+POST), slowapi rate limiting, routers for dashboard/reports/stories/map/**oracle**

### Web Platform (Next.js)

Located in `web-platform/`. Uses Next.js 16 App Router, React 19, Tailwind CSS 4, Shadcn/ui (Radix), Mapbox GL for intelligence map, **react-force-graph-2d** for narrative graph visualization, SWR for data fetching, Framer Motion for animations.

**Routes:** `/` (landing), `/dashboard` (reports list), `/dashboard/report/[id]` (detail), `/map` (geospatial intelligence map), **`/stories` (narrative storyline graph)**, **`/oracle` (Oracle 2.0 chat)**

**API communication:** Frontend → FastAPI backend (`src/api/main.py`) with `X-API-Key` header authentication.

### API Endpoints

| Prefix | Router | Description |
|--------|--------|-------------|
| `/api/v1/dashboard/` | `routers/dashboard.py` | Stats, KPIs |
| `/api/v1/reports/` | `routers/reports.py` | Report list, detail |
| `/api/v1/stories/` | `routers/stories.py` | Storyline list, detail, **graph network** |
| `/api/v1/map/` | `main.py` | GeoJSON entities for map |

### Narrative Engine (3-Layer Filtering)

Off-topic content is filtered at 3 pipeline stages:
1. **Filtro 1** (`src/ingestion/pipeline.py`): Keyword blocklist at ingestion — rejects articles matching sports/entertainment/food keywords
2. **Filtro 2** (`src/nlp/relevance_filter.py`): LLM relevance classification — Gemini classifies articles as RELEVANT/NOT_RELEVANT to geopolitical scope
3. **Filtro 4** (`src/nlp/narrative_processor.py`): Post-clustering validation — archives storylines with no scope keywords AND matching off-topic patterns

### Database Schema (Narrative)

| Table | Purpose |
|-------|---------|
| `storylines` | Narrative threads: title, summary, embeddings, momentum_score, narrative_status |
| `storyline_edges` | Graph edges: source/target storyline, Jaccard weight, relation_type |
| `article_storylines` | Junction: article_id ↔ storyline_id, relevance_score |
| `v_active_storylines` | View: active storylines ordered by momentum DESC |
| `v_storyline_graph` | View: edges between active storylines with titles |

## Configuration

- `config/feeds.yaml` — 33 RSS feed definitions with categories (breaking_news, intelligence, tech_economy, etc.)
- `config/top_50_tickers.yaml` — Geopolitical market movers with aliases for NER matching
- `config/entity_blocklist.yaml` — Noise filtering for extracted entities
- `.env` — Database URL, API keys (Gemini, FRED), app settings (see `.env.example`)
- `migrations/` — 12+ incremental SQL migration files, applied manually via `psql` or through `load_to_database.py --init-only` (includes narrative engine schema: storylines, storyline_edges, views)

## Key Technical Patterns

- **RAG with reranking:** Vector search retrieves top-20, cross-encoder reranks to top-10 for ~15-20% precision improvement
- **Trade signal pipeline:** Macro-first approach — report → context condensation → structured signal extraction → Pydantic validation (BULLISH/BEARISH/NEUTRAL/WATCHLIST)
- **Async ingestion:** Single `asyncio.run()` in `pipeline.run()` orchestrates both feed parsing (aiohttp + `TCPConnector(limit=20, limit_per_host=3)`) and content extraction (`asyncio.Semaphore(10)` + `asyncio.to_thread()` for sync libraries). Sync wrappers (`parse_all_feeds`, `extract_batch`) kept for standalone use only.
- **Deduplication:** 2-phase — in-memory hash(link+title) then database content hash, reducing articles by 20-25%
- **Embeddings:** `paraphrase-multilingual-MiniLM-L12-v2` for cross-language semantic similarity (Italian + English sources)
- **Schema validation:** Pydantic v2 models in `src/llm/schemas.py` for all LLM structured output

## Testing

Pytest markers defined in `pytest.ini`: `unit`, `integration`, `e2e`, `slow`. Tests mirror `src/` structure under `tests/`. Mock HTTP with `responses` library, mock datetime with `freezegun`. Async methods tested with `AsyncMock`; use `pytest-asyncio` for async test support.

## Environment Requirements

- Python 3.9+ (developed on 3.12.3)
- PostgreSQL 14+ with pgvector extension
- Node.js 16+ (for web-platform)
- spaCy model: `python -m spacy download xx_ent_wiki_sm`
- Required env vars: `DATABASE_URL`, `GEMINI_API_KEY`

## Documentation

When updating documentation, always check for and update context.md files in subdirectories, not just top-level docs. Every module directory contains a context.md — these must stay in sync with code changes.

## Critical Pitfalls

- **f-string escaping in report_generator.py:** The LLM prompt uses f-strings. Variables like `{narrative_section}` must NOT be double-braced `{{}}` or they become literal text. Pre-compute variables before the f-string.
- **spaCy model required:** `xx_ent_wiki_sm` must be installed (`python -m spacy download xx_ent_wiki_sm`). To test report_generator methods in isolation, bypass full constructor with `object.__new__(ReportGenerator)`.
- **DB views:** `v_active_storylines` and `v_storyline_graph` are the primary data sources for API and report narrative context.
- **UTF-8 surrogate bytes:** Web scraping can produce invalid UTF-8 bytes (e.g. truncated multibyte sequences) that PostgreSQL rejects. `database.py` has `_sanitize_text()` applied in `save_article()`. `narrative_processor.py` `_evolve_narrative_summary()` has a fallback query without `LEFT(full_text, 200)` snippet on encoding error.
- **generate_content() hang:** With `transport='rest'`, calling `generate_content()` without `request_options={"timeout": N}` causes ~900s hang on network issues. Always specify timeout (30s for 2.0-flash, 60s for 2.5-flash).
- **Gemini model split:** NLP layer (`narrative_processor.py`, `relevance_filter.py`) → `gemini-2.0-flash` (speed-critical, structured tasks); LLM layer (`report_generator.py`, `query_analyzer.py`, `oracle_engine.py`, `oracle_orchestrator.py`, `query_router.py`) → `gemini-2.5-flash` (deep reasoning).
- **Oracle 2.0 singleton:** `get_oracle_orchestrator_singleton()` in `oracle_orchestrator.py` is thread-safe (double-checked locking). The singleton holds 400MB embedding model and LLM connection — never re-initialize per request.
- **SQLTool safety layers:** sqlparse token-level detection → forbidden keywords → max 3 JOINs → LIMIT enforcement → EXPLAIN cost check (≤10000) → 5s `statement_timeout`. SQLTool's `_execute` wraps all in BaseTool.execute() which catches all exceptions.
- **oracle_query_log table:** Migration `013_oracle_query_log.sql`. If table doesn't exist, `log_oracle_query()` in `database.py` silently no-ops (non-critical).
- **Migrations are manual:** SQL files in `migrations/` applied via `psql` or `load_to_database.py --init-only`.
- **CI test config:** GitHub Actions test step needs `GEMINI_API_KEY: "ci-fake-key-for-unit-tests"` env var + `--ignore=tests/test_sprint2_full.py` (e2e test requiring real DB).

## Debugging

When debugging issues, distinguish between "script completed successfully" and "script is still running/stuck" by checking process status and recent log timestamps, not just log content. Use `ps aux | grep <script>` and compare log file modification times against wall clock time.

## General Rules

When investigating dates, pipeline runs, or report IDs, always verify the current date and day of week using the `date` command before making claims about timing.

## Domain Concepts

When the user asks about scores, intelligence scores, or scoring — they mean the scored output stored in the database (oracle_engine output, scored articles/reports in DB tables), NOT report files on disk. Check the database tables first, not the filesystem.
