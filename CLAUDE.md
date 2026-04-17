# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This project uses both Python (backend/pipelines) and TypeScript (web-platform). Key directories contain context.md files describing their purpose. Read relevant context.md files before making changes in a directory.

INTELLIGENCE_ITA is an end-to-end geopolitical intelligence news analysis platform. It ingests 33+ RSS feeds, processes articles with NLP (spaCy + sentence-transformers), stores them in PostgreSQL with pgvector for semantic search, generates intelligence reports via Google Gemini LLM with RAG, and provides a human-in-the-loop review dashboard. A **Narrative Engine** tracks storylines across articles using HDBSCAN clustering, LLM-driven summary evolution, and a graph of inter-storyline relationships. A Next.js web platform serves as the public-facing frontend with a force-directed graph visualization of the narrative network.

## Module Index

| Module | Size | Detail |
|--------|------|--------|
| `src/llm/report_generator.py` | ~3385 lines | [src/llm/context.md](src/llm/context.md) |
| `src/storage/database.py` | ~2708 lines | [src/storage/context.md](src/storage/context.md) |
| `src/nlp/narrative_processor.py` | ~1517 lines | [src/nlp/context.md](src/nlp/context.md) |
| `src/integrations/openbb_service.py` | ~1026 lines | [src/integrations/context.md](src/integrations/context.md) |
| `src/llm/oracle_orchestrator.py` | ~566 lines | [src/llm/context.md](src/llm/context.md) |
| `src/nlp/processing.py` | ~603 lines | [src/nlp/context.md](src/nlp/context.md) |
| `src/api/` | routers + main | [src/api/context.md](src/api/context.md) |
| `scripts/` | pipeline + tooling | [scripts/context.md](scripts/context.md) |
| `web-platform/` | Next.js frontend | [web-platform/context.md](web-platform/context.md) |
| `migrations/` | 42 SQL files | [migrations/context.md](migrations/context.md) |

## Architecture Diagrams

Visual documentation in [`docs/architecture/`](docs/architecture/) — Mermaid, renders on GitHub:

| File | Content |
|------|---------|
| [00_overview.md](docs/architecture/00_overview.md) | C4 L1 System Context + L2 Container Diagram |
| [01_pipeline.md](docs/architecture/01_pipeline.md) | Daily pipeline data flow + report generation |
| [02_narrative_engine.md](docs/architecture/02_narrative_engine.md) | Narrative Engine 6-stage flow + state machine |
| [03_oracle.md](docs/architecture/03_oracle.md) | Oracle 2.0 agentic loop + tool routing |
| [04_database.md](docs/architecture/04_database.md) | ER diagrams: core, narrative, macro, knowledge base |
| [05_frontend.md](docs/architecture/05_frontend.md) | Next.js route map + component tree + SWR flow |
| [06_api.md](docs/architecture/06_api.md) | API endpoint map + rate limits + response shapes |
| [07_module_deps.md](docs/architecture/07_module_deps.md) | Python inter-module dependency graph + singletons |

## Key Commands

```bash
# Pipeline — 7 steps in order (run from repo root)
python -m src.ingestion.pipeline              # 1. Ingest RSS feeds
python scripts/fetch_daily_market_data.py     # 2. Fetch market data
python scripts/process_nlp.py                 # 3. NLP processing
python scripts/load_to_database.py            # 4. Load to database
python scripts/process_narratives.py          # 5. Narrative processing
python scripts/generate_report.py             # 6. Generate LLM report
python scripts/refresh_map_data.py            # 7. Refresh map + intelligence scores

# Full automated pipeline
python scripts/daily_pipeline.py

# Report generation flags (non-derivable)
python scripts/generate_report.py --days 3 --macro-first --skip-article-signals

# HITL Dashboard
streamlit run Home.py

# FastAPI backend
uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000

# Eval markers (CI-specific — not standard pytest)
pytest tests/evals/ -m eval_fast   # mocked model, runs on every PR
pytest tests/evals/ -m eval_slow   # real Gemini model, nightly only
```

## Configuration Files

| File | Purpose |
|------|---------|
| `config/feeds.yaml` | 33 RSS feed definitions with categories |
| `config/top_50_tickers.yaml` | Geopolitical market movers with NER aliases |
| `config/entity_blocklist.yaml` | Noise filtering for extracted entities |
| `config/asset_theory_library.yaml` | 35 indicator ontologies + causal correlation maps |
| `config/macro_convergences.yaml` | Convergence pattern definitions for Strategic Intelligence Layer |
| `config/sc_sector_map.yaml` | Supply chain sector mappings |
| `config/iran_static_data.json` | Static Iran geopolitical reference data |
| `config/pdf_sources.yaml` | PDF intelligence sources auto-detected via pymupdf4llm |
| `.env` / `.env.example` | Runtime secrets and settings |

## Testing

Pytest markers defined in `pytest.ini`: `unit`, `integration`, `e2e`, `slow`, `eval_fast`, `eval_slow`.

| Marker | When | Model | Fail threshold |
|--------|------|-------|---------------|
| `eval_fast` | Every PR (`evals_fast.yml`) | Mocked — no API calls | Hard fail on logic errors |
| `eval_slow` | Nightly (`evals_nightly.yml`) | Real Gemini model | Configurable pass rate |

Tests mirror `src/` structure under `tests/`. Mock HTTP with `responses`, mock datetime with `freezegun`. Async methods use `AsyncMock` + `pytest-asyncio`.

## Environment Requirements

- **Python 3.12** (3.9+ minimum)
- **PostgreSQL 14+** with `pgvector` and `PostGIS` extensions
- **Node.js 16+** for `web-platform/`
- **spaCy model**: `python -m spacy download xx_ent_wiki_sm`
- **Docker Compose services**: `postgres`, `backend`, `frontend`, `nginx`, `photon` (optional, `--profile photon`)
- **Required env vars**: `DATABASE_URL`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, `MISTRAL_API_KEY`, `INTELLIGENCE_API_KEY`, `FRED_API_KEY`

## Key Technical Patterns

- **RAG with reranking:** Vector search retrieves top-20, cross-encoder reranks to top-10 for ~15-20% precision improvement
- **Trade signal pipeline:** Macro-first approach — report → context condensation → structured signal extraction → Pydantic validation (BULLISH/BEARISH/NEUTRAL/WATCHLIST)
- **Async ingestion:** Single `asyncio.run()` in `pipeline.run()` orchestrates both feed parsing (aiohttp + `TCPConnector(limit=20, limit_per_host=3)`) and content extraction (`asyncio.Semaphore(10)` + `asyncio.to_thread()` for sync libraries). Sync wrappers (`parse_all_feeds`, `extract_batch`) kept for standalone use only.
- **Deduplication:** 2-phase — in-memory hash(link+title) then database content hash, reducing articles by 20-25%
- **Embeddings:** `paraphrase-multilingual-MiniLM-L12-v2` for cross-language semantic similarity (Italian + English sources)
- **Schema validation:** Pydantic v2 models in `src/llm/schemas.py` for all LLM structured output
- **Time-weighted decay (Oracle 2.0):** `score * exp(-k * days)` post-retrieval in RAGTool (`src/llm/tools/rag_tool.py`). Over-fetch 3x to avoid Top-K bias. K dinamico per intent (FACTUAL=0.03, ANALYTICAL=0.015, MARKET=0.04). Time-shifting for historical queries (reference_date = end_date). Min floor 0.15 post-decay. Report Generator and Oracle 1.0 are NOT affected.
- **Chain-of-Verification CoVe (Oracle 2.0 synthesis):** When structured data (country_profiles, macro_forecasts, macro_indicators) and RAG context disagree on the same quantitative KPI, Oracle annotates both values: "Dato strutturato [fonte]: X | Contesto narrativo [fonte]: Y — possibile lag temporale". Structured data takes priority for official KPIs; RAG for sentiment and recent events (<30d). Configured in `oracle_orchestrator.py` `_synthesize()` prompt.
- **Few-Shot SQL Store (Oracle 2.0 QueryRouter):** `_SQL_EXAMPLES` dict in `query_router.py` injects 2-3 canonical query patterns per table for PostGIS (`conflict_events`), vintage subqueries (`macro_forecasts`), GIN array search (`v_sanctions_public`), etc. Activated when keywords in the user query match a table. Evidence: +30% schema adherence vs zero-shot (Spider/BIRD benchmarks).
- **v_sanctions_public — PII-sanitized view (migration 034):** All Oracle 2.0 tools use `v_sanctions_public`, not raw `sanctions_registry`. The view strips `birthDate`, `birthPlace`, `address`, `idNumber`, `taxNumber`, `passportNumber`, `nationalId`, `registrationNumber`, `phone`, `email` from the `properties` JSONB field. `SQLTool.ALLOWED_TABLES` and `ReferenceTool.SAFE_QUERIES` reference the view. The base table is writable by data loaders only.
- **Photon geocoder (self-hosted):** `scripts/geocode_geonames.py` runs a 4-step hybrid pipeline: GeoNames gazetteer → Gemini disambiguation → Photon reverse geocoding. Start with `docker compose --profile photon up -d photon`. Falls back to `https://photon.komoot.io/api` when `PHOTON_URL` is unset.

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

### LLM Integration
- **f-string escaping in report_generator.py:** The LLM prompt uses f-strings. Variables like `{narrative_section}` must NOT be double-braced `{{}}` or they become literal text. Pre-compute variables before the f-string.
- **generate_content() hang:** With `transport='rest'`, calling `generate_content()` without `request_options={"timeout": N}` causes ~900s hang on network issues. Always specify timeout via factory config or `generate_content_raw()`. T5 timeout=15s, T1 timeout=120s.
- **5-tier LLM routing:** Use `LLMFactory.get(tier)` — never hardcode model names. Tiers: T1=Gemini 3.1 Pro (reports), T2=Claude Sonnet 4.6 (Oracle), T3=DeepSeek V3.2 (extraction), T4a=Flash-Lite (query_analyzer), T4b=Mistral Codestral (sql), T5=Flash-Lite (NLP bulk). Exception: `narrative_processor.py` uses `GeminiClient("gemini-2.5-flash", timeout=30)` directly — do NOT downgrade to Flash-Lite until quality eval passes.
- **Oracle 2.0 singleton:** `get_oracle_orchestrator_singleton()` in `oracle_orchestrator.py` is thread-safe (double-checked locking). The singleton holds 400MB embedding model and LLM connection — never re-initialize per request.
- **Oracle 6 intents (not 5):** `query_router.py` classifies into FACTUAL / ANALYTICAL / NARRATIVE / MARKET / COMPARATIVE / **OVERVIEW**. OVERVIEW uses very low time-decay (k=0.005) for panoramic queries that should return broad recent context. Using vector-only search (no FTS) to avoid AND-matching issues.
- **src/macro/ Phase 3 is log-only:** `match_convergences()` and `build_sc_signals_context()` are called inside `_generate_macro_analysis()` but their output is only logged — NOT injected into the prompt. This is intentional for independent validation before Phase 4 cutover.

### Data Encoding
- **spaCy model required:** `xx_ent_wiki_sm` must be installed (`python -m spacy download xx_ent_wiki_sm`). To test report_generator methods in isolation, bypass full constructor with `object.__new__(ReportGenerator)`.
- **UTF-8 surrogate bytes:** Web scraping can produce invalid UTF-8 bytes (e.g. truncated multibyte sequences) that PostgreSQL rejects. `database.py` has `_sanitize_text()` applied in `save_article()`. `narrative_processor.py` `_evolve_narrative_summary()` has a fallback query without `LEFT(full_text, 200)` snippet on encoding error.

### Database / Schema
- **DB views:** `v_active_storylines` and `v_storyline_graph` are the primary data sources for API and report narrative context.
- **`sanctions_registry` NOT in ALLOWED_TABLES:** Raw table is excluded from SQLTool and ReferenceTool. Use `v_sanctions_public` (migration 034 — PII-sanitized view). If you add a new tool or query that needs sanctions data, always reference the view.
- **oracle_query_log table:** Migration `013_oracle_query_log.sql`. If table doesn't exist, `log_oracle_query()` in `database.py` silently no-ops (non-critical).
- **Migrations are manual:** SQL files in `migrations/` applied via `psql` or `load_to_database.py --init-only`.
- **`macro_indicator_metadata` must exist before Phase 1 fetch runs:** Migration 035 creates the table. `_upsert_indicator_metadata()` will log an error and no-op if the table is missing — not a crash, but metadata won't accumulate. Apply migration 035 before the first Phase 1 pipeline run.

### Macro / Financial
- **NICKEL/monthly date bug (fixed in Phase 1):** Before the fix, FRED monthly data was saved with `target_date` (today) instead of the real FRED data date — causing false deltas. Fixed by `_fetch_indicator_openbb_fixed()` which extracts `data_date` from the FRED result. The new `macro_indicator_metadata` table (migration 035) tracks the real data date per indicator.
- **TED_SPREAD, EPU_GLOBAL, USD_RUB removed (Phase 1):** These three indicators are no longer fetched, stored, or referenced. Removed from `MACRO_INDICATORS`, `asset_theory_library.yaml`, and all cross-reference maps. Do not re-add without updating all three locations.
- **ALUMINUM/WHEAT source change (Phase 1):** Both switched from FRED monthly to daily CME futures (ALI=F / ZW=F via yfinance). USD_GBP and USD_CNY also switched from FRED daily to yfinance. If these tickers stop working, update `MACRO_INDICATORS` symbol and `fetch_category`, not `fred_series`.
- **Phase 3 convergence staleness weight (match_convergences.py):** Staleness weight logic is mandatory — without it, NICKEL (67d stale, monthly) contributes to `china_stress_global_slowdown` with full weight. Thresholds: `staleness > max_stale * 3` → weight=0.0 (ignored), `staleness > max_stale` → weight=0.5. But the ignored trigger still counts in the denominator (total_weight), keeping confidence honest.
- **OntologyManager singleton loads 3 YAMLs at boot (Phase 3):** `asset_theory_library.yaml` + `macro_convergences.yaml` + `sc_sector_map.yaml`. If any YAML is missing, the singleton logs a warning and continues with empty dict — no crash. The convergences and sc_map properties return `{}` silently.

### Geocoding
- **GeoNames geocoder requires `geo_gazetteer` table:** `scripts/geocode_geonames.py` needs migration 023 applied AND `scripts/load_geonames.py` run first (one-time, ~15 min). Without it, geocoding silently returns no results.

### Auth / Access
- **JWT middleware removed:** `middleware.ts` is now a no-op passthrough (empty matcher). All routes are public. `JWT_SECRET` and `ACCESS_CODES` env vars are no longer used by the frontend.

### Oracle
- **SQLTool safety layers:** sqlparse token-level detection → forbidden keywords → max 3 JOINs → LIMIT enforcement → EXPLAIN cost check (≤10000) → 5s `statement_timeout`. SQLTool's `_execute` wraps all in BaseTool.execute() which catches all exceptions.
- **community_name populated by compute_communities.py:** The `community_name` field (migration 022) is populated by Gemini inside `compute_communities.py`, not by `narrative_processor.py`. Must re-run `compute_communities.py` after large storyline changes to refresh names.

### Ingestion
- **Ingestion article extraction timeout & per-domain concurrency (2026-04-09):** Each article extraction is wrapped in `asyncio.wait_for(..., timeout=PER_ARTICLE_TIMEOUT)` with `PER_ARTICLE_TIMEOUT=30s`. This prevents indefinite hangs on Cloudflare challenges or unresponsive servers. Additionally, per-domain concurrency is limited to max 2 concurrent requests per domain (`DOMAIN_MAX_CONCURRENT=2`) to reduce anti-bot triggering. These fixes escape the fatal timeout issue when Times of Israel hits Cloudflare blocking.
- **Scrapling StealthyFetcher concurrency:** Uses Chromium headlessly — max 2 concurrent instances (`scrapling_stealth_semaphore = asyncio.Semaphore(2)`) to avoid OOM on GitHub Actions. Tier 2 domains: `rusi.org`. Tier 1 (curl_cffi, no browser): `understandingwar.org`, `chathamhouse.org`, `timesofisrael.com` (added 2026-04-09 for Cloudflare anti-bot bypass).
- **CI test config:** GitHub Actions test step needs `GEMINI_API_KEY: "ci-fake-key-for-unit-tests"` env var + `--ignore=tests/test_sprint2_full.py` (e2e test requiring real DB).

## Debugging

When debugging issues, distinguish between "script completed successfully" and "script is still running/stuck" by checking process status and recent log timestamps, not just log content. Use `ps aux | grep <script>` and compare log file modification times against wall clock time.

## General Rules

When investigating dates, pipeline runs, or report IDs, always verify the current date and day of week using the `date` command before making claims about timing.

## Domain Concepts

When the user asks about scores, intelligence scores, or scoring — they mean the scored output stored in the database (oracle_engine output, scored articles/reports in DB tables), NOT report files on disk. Check the database tables first, not the filesystem.

## Infrastructure Reference

**Server**: Hetzner CAX31 · 8 GB ARM64 · Deploy path: `/opt/intelligence-ita/repo` · Env: `.env.production`

Full ops reference (Docker Compose, SSH, DB, Nginx, GitHub Actions): **[docs/runbooks/production.md](docs/runbooks/production.md)**

**GitHub Actions workflows**: `deploy.yml` · `pipeline.yml` · `migrate.yml` · `evals_fast.yml` · `evals_nightly.yml` · `update-docs.yml`
