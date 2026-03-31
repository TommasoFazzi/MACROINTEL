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
python -m src.ingestion.pipeline              # 1. Ingest RSS feeds
python scripts/fetch_daily_market_data.py     # 2. Fetch market data (optional, continue_on_failure)
python scripts/process_nlp.py                 # 3. NLP processing
python scripts/load_to_database.py            # 4. Load to database
python scripts/process_narratives.py          # 5. Narrative processing (storylines + graph)
python scripts/generate_report.py             # 6. Generate LLM report
python scripts/refresh_map_data.py            # 7. Refresh map cache + recompute intelligence scores (post-pipeline)

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

# Daily status check
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
1. **Ingestion** (`src/ingestion/`): Async RSS parsing via aiohttp (parallel feed fetching + concurrent content extraction), full-text extraction (Trafilatura primary → **Scrapling curl_cffi/StealthyFetcher** for WAF/Cloudflare sites → Newspaper3k fallback → Cloudscraper), 2-phase deduplication (hash + content), **keyword blocklist filter** (off-topic rejection at ingestion), PDF auto-detection via pymupdf4llm
2. **NLP** (`src/nlp/`): spaCy multilingual NER (`xx_ent_wiki_sm`), semantic chunking (500-word sliding window), embeddings (`paraphrase-multilingual-MiniLM-L12-v2`, 384-dim), **LLM relevance classification** (Gemini-based scope filter)
3. **Storage** (`src/storage/database.py`): PostgreSQL + pgvector with HNSW indexing, connection pooling (psycopg2 SimpleConnectionPool)
4. **Narrative Engine** (`src/nlp/narrative_processor.py`): HDBSCAN micro-clustering of orphan events, embedding-based matching to existing storylines, LLM summary evolution (Gemini 2.0 Flash), TF-IDF weighted Jaccard entity-overlap graph edges (uses `entity_idf` materialized view), momentum scoring with decay, **post-clustering validation filter** (regex-based off-topic archival). Community detection via `scripts/compute_communities.py` (Louvain algorithm).
5. **Report Generation** (`src/llm/`): Google Gemini 2.5 Flash, 2-stage RAG (vector search → cross-encoder reranking with ms-marco-MiniLM), **narrative storyline context** (top 10 storylines injected as XML), **JIT ontological context** (anomaly screener → OntologyManager → focused macro theory injection), trade signal extraction, "Strategic Storyline Tracker" section
6. **HITL** (`src/hitl/`, `Home.py`): Streamlit dashboard for review, editing, rating, feedback loop
7. **Automation** (`scripts/daily_pipeline.py`): 6 core steps (ingestion → market_data → nlp_processing → load_to_database → **narrative_processing** → generate_report) + 2 conditional steps (weekly_report on Sundays → monthly_recap after 4 weekly reports); scheduled via **GitHub Actions** on Hetzner

### Key Modules by Size/Complexity

- `src/llm/report_generator.py` (~2700 lines) — Core LLM integration, RAG pipeline, trade signals, narrative storyline context
- `src/storage/database.py` (~2445 lines) — All PostgreSQL/pgvector operations
- `src/nlp/narrative_processor.py` (~1498 lines) — **Narrative Engine**: HDBSCAN clustering, storyline matching, LLM evolution, TF-IDF weighted Jaccard graph edges (threshold 0.05), entity sanitization (`_is_garbage_entity`, `_clean_entity`), momentum decay, post-clustering validation. Graph candidate query includes `stabilized` storylines.
- `src/nlp/story_manager.py` — **DELETED** (legacy storyline clustering, fully replaced by narrative_processor)
- `src/nlp/processing.py` (~603 lines) — NLP pipeline: cleaning, chunking, NER, embeddings
- `src/nlp/relevance_filter.py` — LLM-based article relevance classification (Gemini 2.0 Flash)
- `scripts/compute_communities.py` (~198 lines) — Louvain community detection for storyline graph (python-louvain + networkx); saves community_id to storylines table. Defaults: `min_weight=0.05`, `resolution=0.2`. After detection calls Gemini to generate descriptive `community_name`.
- `scripts/geocode_geonames.py` — **Primary geocoder**: 4-step GeoNames + Gemini + Photon hybrid pipeline. Requires `geo_gazetteer` table (migration 023, populated by `load_geonames.py`).
- `scripts/load_geonames.py` — Loads GeoNames allCountries.txt + alternateNames.txt into `geo_gazetteer` (~2-3M rows, one-time ~15 min).
- `src/integrations/openbb_service.py` (~1026 lines) — OpenBB financial data integration (36 MACRO_INDICATORS incl. USD_CNH)
- `src/knowledge/ontology_manager.py` — **OntologyManager** singleton: loads `config/asset_theory_library.yaml` (35 indicator ontologies + causal correlation maps), `screen_anomalies()` identifies top movers by delta %, `build_jit_context()` assembles focused theory for LLM injection into `_generate_macro_analysis()`
- `src/llm/oracle_engine.py` (~566 lines) — Oracle 1.0 RAG chat engine (backward-compat, used by Streamlit HITL)
- `src/llm/oracle_orchestrator.py` — **Oracle 2.0 main coordinator**: ToolRegistry + QueryRouter + ConversationMemory + caching + LLM synthesis + anti-hallucination; `get_oracle_orchestrator_singleton()` for FastAPI
- `src/llm/query_router.py` — Intent classification (Gemini 2.5 Flash) + QueryPlan generation; double-layer SQL injection defense
- `src/llm/tools/` — Tool package: RAGTool (multi-query expansion + time-weighted decay), SQLTool (5-layer safety), AggregationTool, GraphTool, MarketTool, TickerThemesTool, ReportCompareTool, ReferenceTool, SpatialTool
- `src/api/main.py` + `src/api/auth.py` + `src/api/routers/` — FastAPI backend: X-API-Key auth (`secrets.compare_digest`), CORS (GET+POST), slowapi rate limiting, routers for dashboard/reports/stories/map/**oracle**

### Web Platform (Next.js)

Located in `web-platform/`. Uses Next.js 16 App Router, React 19, Tailwind CSS 4, Shadcn/ui (Radix), Mapbox GL for intelligence map, **react-force-graph-2d** for narrative graph visualization, SWR for data fetching, Framer Motion for animations.

**Routes:** `/` (landing), `/access` (JWT code entry), `/insights` (public briefings), `/dashboard` (reports list), `/dashboard/report/[id]` (detail), `/map` (geospatial intelligence map + Tier 3 layers), **`/stories` (narrative storyline graph)**, **`/oracle` (Oracle 2.0 chat)**

**Auth:** `middleware.ts` protects `/dashboard`, `/map`, `/stories`, `/oracle` with JWT (`macrointel_access` cookie). Access codes configured via `ACCESS_CODES` + `JWT_SECRET` env vars. Oracle BYOK enforced when `ORACLE_REQUIRE_GEMINI_KEY=true`.

**API communication:** Frontend → FastAPI backend (`src/api/main.py`) with `X-API-Key` header authentication (server-side proxy at `/api/proxy/[...path]`).

### API Endpoints

| Prefix | Router | Description |
|--------|--------|-------------|
| `/api/v1/dashboard/` | `routers/dashboard.py` | Stats, KPIs |
| `/api/v1/reports/` | `routers/reports.py` | Report list, detail, **`GET /compare?ids=A,B` — LLM delta analysis** |
| `/api/v1/stories/` | `routers/stories.py` | Storyline list, detail, **graph network**, **community listing**, **ego network** |
| `/api/v1/map/` | `routers/map.py` | GeoJSON entities, arcs, stats, cache invalidate |
| `/api/v1/oracle/` | `routers/oracle.py` | Oracle 2.0 chat (`POST /chat` rate limit 3/min, `GET /health`) |
| `/api/v1/insights/` | `routers/insights.py` | Public briefings list + detail by slug (no auth required) |
| `/api/v1/waitlist/` | `routers/waitlist.py` | Waitlist registration |

### Narrative Engine (3-Layer Filtering)

Off-topic content is filtered at 3 pipeline stages:
1. **Filtro 1** (`src/ingestion/pipeline.py`): Keyword blocklist at ingestion — rejects articles matching sports/entertainment/food keywords
2. **Filtro 2** (`src/nlp/relevance_filter.py`): LLM relevance classification — Gemini classifies articles as RELEVANT/NOT_RELEVANT to geopolitical scope
3. **Filtro 4** (`src/nlp/narrative_processor.py`): Post-clustering validation — archives storylines with no scope keywords AND matching off-topic patterns

### Database Schema (Narrative)

| Table | Purpose |
|-------|---------|
| `storylines` | Narrative threads: title, summary, embeddings, momentum_score, narrative_status, **community_id** |
| `storyline_edges` | Graph edges: source/target storyline, TF-IDF weighted Jaccard weight, relation_type |
| `article_storylines` | Junction: article_id ↔ storyline_id, relevance_score |
| `v_active_storylines` | View: `emerging`, `active`, **`stabilized`** storylines ordered by momentum DESC, includes community_id (migration 017) |
| `v_storyline_graph` | View: edges between `emerging`, `active`, **`stabilized`** storylines with titles (migration 017) |
| `entity_idf` | Materialized view: TF-IDF inverse document frequency for entity graph weighting |

## Configuration

- `config/feeds.yaml` — 33 RSS feed definitions with categories (breaking_news, intelligence, tech_economy, etc.)
- `config/top_50_tickers.yaml` — Geopolitical market movers with aliases for NER matching
- `config/entity_blocklist.yaml` — Noise filtering for extracted entities
- `.env` — Database URL, API keys (Gemini, FRED), app settings (see `.env.example`)
- `migrations/` — 19+ incremental SQL migration files, applied manually via `psql` or through `load_to_database.py --init-only` (includes narrative engine schema: storylines, storyline_edges, views, entity_idf materialized view, community_id column, graph cleanup, migration 017 adds `stabilized` to views, 018 orphan_events, 019 mv_entity_storyline_bridge + intelligence_score on entities)

## Key Technical Patterns

- **RAG with reranking:** Vector search retrieves top-20, cross-encoder reranks to top-10 for ~15-20% precision improvement
- **Trade signal pipeline:** Macro-first approach — report → context condensation → structured signal extraction → Pydantic validation (BULLISH/BEARISH/NEUTRAL/WATCHLIST)
- **Async ingestion:** Single `asyncio.run()` in `pipeline.run()` orchestrates both feed parsing (aiohttp + `TCPConnector(limit=20, limit_per_host=3)`) and content extraction (`asyncio.Semaphore(10)` + `asyncio.to_thread()` for sync libraries). Sync wrappers (`parse_all_feeds`, `extract_batch`) kept for standalone use only.
- **Deduplication:** 2-phase — in-memory hash(link+title) then database content hash, reducing articles by 20-25%
- **Embeddings:** `paraphrase-multilingual-MiniLM-L12-v2` for cross-language semantic similarity (Italian + English sources)
- **Schema validation:** Pydantic v2 models in `src/llm/schemas.py` for all LLM structured output
- **Time-weighted decay (Oracle 2.0):** `score * exp(-k * days)` post-retrieval in RAGTool (`src/llm/tools/rag_tool.py`). Over-fetch 3x to avoid Top-K bias. K dinamico per intent (FACTUAL=0.03, ANALYTICAL=0.015, MARKET=0.04). Time-shifting for historical queries (reference_date = end_date). Min floor 0.15 post-decay. Report Generator and Oracle 1.0 are NOT affected.

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

**After every task in this project**, before declaring done:
- Modified a module under `src/`? → update its `context.md`
- Added a new script to `scripts/`? → update `scripts/context.md`
- Added a migration? → update `migrations/context.md`
- Changed the API? → update `src/api/context.md`
- Changed the frontend? → update `web-platform/context.md` and/or `web-platform/components/IntelligenceMap/context.md`
- Changed architecture, env vars, or added a major feature? → update `CLAUDE.md` and `README.md`
- Added new env vars? → update `.env.example` and `.env.production.example`

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
- **Scrapling StealthyFetcher concurrency:** Uses Chromium headlessly — max 2 concurrent instances (`scrapling_stealth_semaphore = asyncio.Semaphore(2)`) to avoid OOM on GitHub Actions. Tier 2 domains: `rusi.org`. Tier 1 (curl_cffi, no browser): `understandingwar.org`, `chathamhouse.org`.
- **GeoNames geocoder requires `geo_gazetteer` table:** `scripts/geocode_geonames.py` needs migration 023 applied AND `scripts/load_geonames.py` run first (one-time, ~15 min). Without it, geocoding silently returns no results.
- **JWT middleware blocks all protected routes without `JWT_SECRET`:** If `JWT_SECRET` is not set, `middleware.ts` uses `__no_secret_configured__` as secret, causing all tokens to fail verification — all protected routes redirect to `/access`. Always set `JWT_SECRET` in production.
- **Oracle 6 intents (not 5):** `query_router.py` classifies into FACTUAL / ANALYTICAL / NARRATIVE / MARKET / COMPARATIVE / **OVERVIEW**. OVERVIEW uses very low time-decay (k=0.005) for panoramic queries that should return broad recent context. Using vector-only search (no FTS) to avoid AND-matching issues.
- **community_name populated by compute_communities.py:** The `community_name` field (migration 022) is populated by Gemini inside `compute_communities.py`, not by `narrative_processor.py`. Must re-run `compute_communities.py` after large storyline changes to refresh names.

## Debugging

When debugging issues, distinguish between "script completed successfully" and "script is still running/stuck" by checking process status and recent log timestamps, not just log content. Use `ps aux | grep <script>` and compare log file modification times against wall clock time.

## General Rules

When investigating dates, pipeline runs, or report IDs, always verify the current date and day of week using the `date` command before making claims about timing.

## Domain Concepts

When the user asks about scores, intelligence scores, or scoring — they mean the scored output stored in the database (oracle_engine output, scored articles/reports in DB tables), NOT report files on disk. Check the database tables first, not the filesystem.

## Infrastructure & Server Commands

### Production Environment
- **Server**: Hetzner CAX31 (8 GB ARM64, Ubuntu 22.04)
- **Deploy path**: `/opt/intelligence-ita/repo`
- **Env file (source of truth)**: `/opt/intelligence-ita/repo/.env.production` — ALWAYS use this with `--env-file`. Do NOT use `/opt/intelligence-ita/.env.production` (outside repo, has old pre-hack passwords).
- **Docker Compose project name**: `app` — always pass `-p app`
- **PostgreSQL user**: `intelligence_user`

### Docker — Key Commands

```bash
# All docker compose commands on server need:
docker compose -p app --env-file /opt/intelligence-ita/repo/.env.production <command>

# Status check
docker compose -p app ps

# View logs
docker compose -p app logs backend --tail 50 --follow
docker compose -p app logs frontend --tail 30

# Restart a specific service
docker compose -p app restart backend
docker compose -p app restart frontend

# Rebuild and restart (after code changes)
docker compose -p app up -d --build backend
docker compose -p app up -d --build frontend

# Full redeploy (pulls + rebuilds all)
docker compose -p app --env-file /opt/intelligence-ita/repo/.env.production up -d --build

# Execute commands inside a container
docker compose -p app exec backend python scripts/check_setup.py
docker compose -p app exec backend python scripts/process_narratives.py --days 1
docker compose -p app exec backend psql $DATABASE_URL -c "SELECT count(*) FROM articles;"

# Database backup
bash /opt/intelligence-ita/repo/deploy/backup-db.sh

# View running pipeline
docker compose -p app exec backend ps aux | grep python
```

### GitHub Actions

Workflows in `.github/workflows/`:

| Workflow | File | Trigger | What it does |
|----------|------|---------|-------------|
| **Deploy** | `deploy.yml` | Push to `main` | SSH to Hetzner → git pull → docker compose up --build |
| **Pipeline** | `pipeline.yml` | Daily 08:00 UTC + manual | Runs `daily_pipeline.py` inside backend container |
| **Migrate** | `migrate.yml` | Manual dispatch | Applies SQL migrations to production DB |

```bash
# Trigger pipeline manually (from local machine with gh CLI)
gh workflow run pipeline.yml

# Check pipeline run status
gh run list --workflow=pipeline.yml --limit 5
gh run view <run-id> --log

# Check deploy status
gh run list --workflow=deploy.yml --limit 3
```

Required GitHub Secrets (set in repo Settings → Secrets):

| Secret | Used by |
|--------|---------|
| `HETZNER_HOST` | deploy.yml — SSH target |
| `HETZNER_USER` | deploy.yml — SSH user |
| `HETZNER_SSH_KEY` | deploy.yml — private key |
| `GEMINI_API_KEY` | pipeline.yml — set to `ci-fake-key-for-unit-tests` for test step |
| `DEPLOY_ENV_FILE` | deploy.yml — contents of .env.production |

### Environment Files

```bash
# View current production env (on server)
cat /opt/intelligence-ita/repo/.env.production

# Edit production env
nano /opt/intelligence-ita/repo/.env.production
# Then restart backend to pick up changes:
docker compose -p app restart backend

# Key env vars to know:
# DATABASE_URL=postgresql://intelligence_user:...@postgres:5432/intelligence_ita
# GEMINI_API_KEY=...
# INTELLIGENCE_API_KEY=...  (API shared secret)
# JWT_SECRET=...            (frontend access tokens)
# ACCESS_CODES=...          (comma-separated valid access codes)
# ORACLE_REQUIRE_GEMINI_KEY=true
# POSTGRES_USER=intelligence_user
# ALLOWED_ORIGINS=https://macrointel.net,...
```

### Database — Direct Access

```bash
# Connect to production DB via Docker
docker compose -p app exec postgres psql -U intelligence_user -d intelligence_ita

# From outside container (on server)
psql postgresql://intelligence_user:<password>@localhost:5432/intelligence_ita

# Apply a migration
docker compose -p app exec postgres psql -U intelligence_user -d intelligence_ita \
  -f /opt/intelligence-ita/repo/migrations/025_chunks_source_id.sql

# Useful quick queries
SELECT count(*) FROM articles;
SELECT count(*) FROM storylines WHERE narrative_status != 'archived';
SELECT id, report_date, status FROM reports ORDER BY id DESC LIMIT 5;
REFRESH MATERIALIZED VIEW entity_idf;
REFRESH MATERIALIZED VIEW mv_entity_storyline_bridge;
```

### Nginx (Reverse Proxy)

```bash
# Reload nginx config (after deploy)
docker compose -p app exec nginx nginx -s reload

# Test nginx config
docker compose -p app exec nginx nginx -t

# View nginx logs
docker compose -p app logs nginx --tail 30
```

### SSH to Server

```bash
ssh <user>@<HETZNER_HOST>
cd /opt/intelligence-ita/repo

# Quick health check
docker compose -p app ps
docker compose -p app logs backend --tail 20
```
