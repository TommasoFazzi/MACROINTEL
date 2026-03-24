# Scripts Context

## Purpose
Automation and utility scripts for pipeline execution, data management, and maintenance tasks. Provides CLI tools for running the intelligence pipeline, backfilling data, cleaning entities, and generating reports.

## Architecture Role
Operational layer that orchestrates the core modules. Scripts tie together ingestion → NLP → database → LLM report generation.

**Primary orchestrator**: `daily_pipeline.py` executes 6 core steps sequentially (+ conditional weekly/monthly) with logging, error handling, and configurable fail-fast behavior. Supports manual execution and automated scheduling.

**Scheduling**: In production, the pipeline runs on Hetzner via **GitHub Actions** (`.github/workflows/pipeline.yml`, triggers daily at 8:00 UTC + manual). The macOS launchd plist files (`com.intelligence-ita.*.plist`) in this directory are **deprecated** — do not use them.

**Status checker**: `pipeline_status_check.py` checks whether the pipeline completed, inspects log files, queries the DB for the last report timestamp, and sends a notification with the result.

## Key Files

### Setup & Verification
- `check_setup.py` - Verify system configuration (Python, env, DB, spaCy, models)

### Pipeline Execution
- `daily_pipeline.py` - **Orchestrator**: runs full pipeline in one command
  - **6 core steps** (always run unless filtered): 1.ingestion → 2.market_data → 3.nlp_processing → 4.load_to_database → **5.narrative_processing** → 6.generate_report
  - **Conditional steps** (run after core pipeline, if not `--skip-weekly`): weekly_report (Sundays only) → monthly_recap (after 4 weekly reports since last recap)
  - Default `generate_report` command: `python scripts/generate_report.py --macro-first --skip-article-signals`
  - `--dry-run` - Validate without executing
  - `--step N` - Run only step N (1-6, core steps only)
  - `--from-step N` - Start from step N (1-6)
  - `--verbose` - Enable DEBUG logging
  - `--skip-weekly` - Skip weekly/monthly conditional steps even on Sunday
  - **Auto weekly**: Runs on Sundays (after main pipeline succeeds)
  - **Auto monthly**: Runs after 4 weekly reports since last recap (DB-counted)
  - **market_data** has `continue_on_failure=True` (optional, non-blocking)
  - **narrative_processing** has `continue_on_failure=True` (report generated even if storylines fail)
  - Logs written to `logs/daily_pipeline_{run_id}.log`; old logs auto-cleaned after `PIPELINE_MAX_LOG_DAYS` (default 30)
  - Notifications: macOS `osascript`/`terminal-notifier` locally; SMTP email in production (if `SMTP_HOST` + `NOTIFY_EMAIL` env vars set)
- `pipeline_status_check.py` - **Daily status checker**: runs at 9:00 AM via launchd (separate plist)
  - Checks if pipeline processes are still running (`ps aux` scan for pipeline keywords)
  - Finds today's most recent log file and scans last 30 lines for success/error keywords
  - Queries `reports` table via psycopg2 for the most recent report timestamp
  - Sends macOS notification (`osascript`) with status summary (completed/errors/unknown + log time + last report)
  - Loads `.env` file automatically if present at project root
  - Sound alert on error or unknown state; silent on success
- `process_nlp.py` - Run NLP processing on ingested articles (includes Filtro 2: LLM relevance)
- `process_narratives.py` - **Narrative Engine CLI**: runs storyline clustering, matching, LLM evolution, graph updates
  - `--days N` - Look back N days for unassigned articles
  - `--dry-run` - Validate without DB writes
  - `--verbose` - Enable DEBUG logging
- `load_to_database.py` - Load processed articles to PostgreSQL
- `generate_report.py` - Generate daily intelligence reports (now includes Storyline Tracker section)
  - `--macro-first` flag for serialized pipeline with trade signals
- `generate_weekly_report.py` - Generate weekly aggregated meta-analysis
- `generate_recap_report.py` - Generate recap reports for date ranges

### Market Data
- `backfill_market_data.py` - Backfill Yahoo Finance OHLCV data
- `fetch_daily_market_data.py` - Daily market data fetch

### Entity Management
- `extract_entities.py` - Run NER extraction on articles
- `backfill_entities.py` - Backfill entity data for older articles
- `clean_entities.py` - Clean garbage entities using blocklist
- `deep_clean_entities.py` - Deep deduplication of entities
- `add_sample_entities.py` - Load sample entities for testing

### Geocoding
- `geocode_geonames.py` - **Primary geocoder**: 4-step hybrid pipeline
  1. GeoNames exact/ascii/alternate name lookup against `geo_gazetteer` table
  2. Gemini 2.0 Flash CoT for disambiguation when >1 match (→ clean_name, country_code, feature_type)
  3. Filtered GeoNames lookup using Gemini output
  4. Photon API fallback for locations not in GeoNames
  - CLI: `--limit N`, `--backfill`, `--types GPE LOC`, `--dry-run`
- `load_geonames.py` - **GeoNames database loader**: imports `allCountries.txt` + `alternateNames.txt` dump into `geo_gazetteer` table (~2–3M rows, feature classes A/P/H/L). Run once. ~10–15 min. Requires migration 023.
- `geocode_entities.py` - Legacy Photon geocoder (kept as fallback)
- `geocode_batch.py` - Batch geocoding utility
- `clean_geocoding.py` - Clean invalid geocoding data
- `refresh_map_data.py` - **Post-pipeline map refresh**: invalidates the map entity cache via `POST /api/v1/map/cache/invalidate` and re-runs `compute_intelligence_scores()`. Should be called after `process_narratives.py` to ensure the map reflects the latest storyline data.

### Embeddings & Search
- `backfill_report_embeddings.py` - Generate embeddings for existing reports

### Storylines / Narrative Engine
- `process_narratives.py` - **Primary**: Run NarrativeProcessor daily batch (HDBSCAN + LLM evolution + graph)
- `rebuild_graph_edges.py` - **Graph rebuild utility**: Drops all existing `storyline_edges`, then recomputes TF-IDF weighted Jaccard edges for all active storylines. **Critical**: loads IDF weights via `processor._load_entity_idf(cur)` and passes them to `_update_graph_connections(sid, idf_weights)` — without this, the fallback threshold is 0.30 instead of 0.05, resulting in ~90% fewer edges. Also includes Step 0: cleanup of stale edges involving storylines archived >30 days.
- `compute_communities.py` - **Community detection**: Louvain algorithm (python-louvain + networkx) on the storyline graph. Defaults: `min_weight=0.05`, `resolution=0.2`. Writes `community_id` to `storylines` table. After detection, calls Gemini 2.0 Flash to generate a descriptive `community_name` (e.g. "Hormuz Crisis", "Iran Regional Crisis") based on member storyline titles. CLI flags: `--min-weight`, `--resolution`, `--dry-run`.
- `migrate_community_names.py` - **Backfill**: Generates LLM community names for existing communities that don't have one yet. Safe to re-run (skips communities that already have a name).
- `migrate_storylines_to_en.py` - **Language migration**: Translates Italian storyline titles and summaries to English using Gemini. One-time use for legacy Italian content.
- `reclean_storyline_entities.py` - **Batch entity cleanup**: Iterates all non-archived storylines, applies `_is_garbage_entity()` + `_clean_entity()` sanitization to `key_entities`, updates DB in-place. Used for one-time retroactive cleanup of pre-existing garbage entities.
- `pipeline_manifest.py` - **Pipeline manifest tracking**: Records pipeline run metadata (start/end time, steps run, success/failure) to a JSON manifest file for monitoring and debugging.
- `batch_storyline_clustering.py` - Legacy: Run DBSCAN clustering for storylines
- `test_storyline_clustering.py` - Legacy: Test storyline clustering

### Quality Auditing
- `audit_entity_quality.py` - Audit entity data quality: checks for garbage entities, geocoding gaps, low-mention counts, and duplicate names. Outputs a quality report to stdout.

### Ticker Management
- `seed_tickers.py` - Seed ticker whitelist to database

### Dashboard & Scheduling
- `run_dashboard.sh` - Launch Streamlit dashboard
- `run_weekly_report.sh` - Cron script for weekly reports
- `com.intelligence-ita.daily-pipeline.plist` - **Deprecated** launchd config for 8:00 AM daily pipeline (macOS local only — replaced by GitHub Actions)
- `com.intelligence-ita.pipeline-status-check.plist` - **Deprecated** launchd config for 9:00 AM status check (macOS local only)

### Migrations
- `run_migration_003.py` - Run specific migration

## Dependencies

- **Internal**: All `src/` modules
- **External**: CLI tools (argparse), scheduling (cron)

## Data Flow

- **Input**:
  - `data/` - Ingested article JSON files
  - `config/` - YAML configurations
  - Database tables

- **Output**:
  - `reports/` - Generated intelligence reports
  - Updated database tables
  - Log files in `logs/`

## Common Usage

```bash
# Full pipeline (one command - recommended)
python scripts/daily_pipeline.py

# Full pipeline (step by step)
python -m src.ingestion.pipeline
python scripts/fetch_daily_market_data.py
python scripts/process_nlp.py
python scripts/load_to_database.py
python scripts/process_narratives.py --days 1
python scripts/generate_report.py --macro-first

# Refresh map data + intelligence scores (after narratives step)
python scripts/refresh_map_data.py

# Dry run (validate only)
python scripts/daily_pipeline.py --dry-run

# Resume from specific step
python scripts/daily_pipeline.py --from-step 3

# Weekly report
python scripts/generate_weekly_report.py

# Entity maintenance
python scripts/clean_entities.py
python scripts/geocode_entities.py
python scripts/audit_entity_quality.py

# Rebuild narrative graph edges (full reconstruction)
python scripts/rebuild_graph_edges.py

# Community detection
python scripts/compute_communities.py --min-weight 0.05 --resolution 0.2

# Check system
python scripts/check_setup.py
```

**Note**: Automated scheduling runs via GitHub Actions (`.github/workflows/pipeline.yml`), not launchd. The plist files in this directory are kept for reference only.
