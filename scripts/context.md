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

### Diagnostics / Analysis
- `diagnose_clustering_signal.py` - **Read-only** clustering signal diagnostics (OpenSpec `redesign-narrative-clustering-signal`, protocollo S1–S7). Caratterizza **entrambi** i layer: community (hubness, silhouette live vs k-means, whitening, co-association 4-way + consenso EAC con taglio max-lifetime, cross-space ARI/NMI) e storyline / fix B (frammentazione, match-replay con dip-test→antimodo KDE `τ*`, proxy di coerenza non-circolare in spazio `summary_vector`). Validazione proxy-only a triangolo (stabilità/separazione/frammentazione), silhouette segnalato in-space. Scrive JSON + figure PNG opzionali in `artifacts/`, **mai** sul DB. Deterministico (seed fissi). Deps: `diptest` (S6), `matplotlib` (figure; `--no-viz` per saltarle). Run su PROD: `docker compose -p app exec backend python scripts/diagnose_clustering_signal.py --no-viz`.

### Pipeline Execution
- `daily_pipeline.py` - **Orchestrator**: runs full pipeline in one command
  - **Core steps** (always run unless filtered): ingestion → nlp_processing → load_to_database → narrative_processing → community_detection (Louvain) → **theme_clustering_shadow** (k-means-on-embedding shadow, `continue_on_failure=True`, never writes live `community_id`) → entity_extraction → geocoding → refresh_map_data → generate_report → generate_romania_report → send_report_email (market_data step removed — now handled by evening workflow)
  - **Conditional steps** (run after core pipeline, if not `--skip-weekly`): weekly_report (Sundays only) → monthly_recap (after 4 weekly reports since last recap)
  - Default `generate_report` command: `python scripts/generate_report.py --macro-first --skip-article-signals`
  - `--dry-run` - Validate without executing
  - `--step N` - Run only step N (1-6, core steps only)
  - `--from-step N` - Start from step N (1-6)
  - `--verbose` - Enable DEBUG logging
  - `--skip-weekly` - Skip weekly/monthly conditional steps even on Sunday
  - **Auto weekly**: Runs on Sundays (after main pipeline succeeds)
  - **Auto monthly**: Runs after 4 weekly reports since last recap (DB-counted)
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
- `send_report_email.py` - **Email dispatch**: fetches today's reports from DB, converts markdown → HTML → PDF (weasyprint), sends a single HTML email with both PDF attachments to all recipients in `config/report_recipients.yaml` via Brevo SMTP.
  - `--dry-run` - Render without sending (prints preview to log)
  - `--date YYYY-MM-DD` - Target date (default: today)
  - Env vars: `BREVO_SMTP_HOST/PORT/USER/PASS`, `BREVO_FROM_EMAIL/NAME`, `REPORT_GLOBAL_URL`, `REPORT_ROMANIA_URL`
  - Called as step 11 in `daily_pipeline.py` (`continue_on_failure=True`)
- `generate_report.py` - Generate daily intelligence reports (now includes Storyline Tracker section)
  - `--macro-first` flag for serialized pipeline with trade signals
  - `--report-type {global,romania-daily,romania-weekly}` (default: `global`) — Romania variants bypass macro-first pipeline, use Italian system prompts, apply Romania storyline scoring. `--days` defaults to 1 for romania-daily and 7 for romania-weekly unless explicitly overridden.
- `generate_weekly_report.py` - Generate weekly aggregated meta-analysis
- `generate_recap_report.py` - Generate recap reports for date ranges

### Market Data
- `backfill_market_data.py` - Backfill Yahoo Finance OHLCV data
- `fetch_daily_market_data.py` - Market data fetch (global + Romania indicators). **Not called by daily_pipeline.py** — invoked by `.github/workflows/evening_market_fetch.yml` at 21:30 UTC Mon-Fri (after NYSE close). Calls `ensure_daily_macro_data()` (US/global, country_code='US') then `fetch_ro_indicators()` (Romania vertical, country_code='RO'). `--force` deletes only `country_code='US'` rows (RO data preserved). `_has_macro_data()` without country_code now filters by `country_code='US'` to prevent RO-only data from masking a failed US fetch. Morning reports read from the previous evening's DB row via `get_macro_context_text()` fallback. Manual backfill: `--date YYYY-MM-DD --force`.
- `fetch_romania_macro.py` - **Romania macro fetch**: calls `ensure_daily_macro_data()` (idempotent), then shows RO-specific indicator preview with staleness info. Flags: `--date YYYY-MM-DD`, `--force` (deletes existing RO rows for date before re-fetching). Used as standalone verification or pipeline pre-step.
- `backfill_sruuf.py` - **One-shot ticker switch recovery**: deletes all URANIUM rows (URA history), downloads SRUUF daily closes via yfinance (last 90 days), reinserts with correct `previous_value` chain, and flags today's report as `draft` so it doesn't enter the knowledge base. Idempotent. Run after any equity/ETF ticker substitution in `MACRO_INDICATORS`.
- `backfill_new_indicators_b2.py` - **B2 expansion backfill**: fetches 60 calendar days of history for TTF_GAS (yfinance `TTF=F`) and YIELD_CURVE_10Y_3M (FRED REST API `T10Y3M`) and inserts with correct `previous_value` chain. Run once after deploying the B2 indicator additions. Requires `DATABASE_URL` and `FRED_API_KEY`. Idempotent.
- `backfill_macro_history.py` - **Historical context backfill**: two-step script that seeds raw FRED daily history and computes the 7 derived columns added by migrations 038+039 (`ma_7d`, `ma_30d`, `std_30d`, `pct_change_7d`, `pct_change_30d`, `percentile_rank_30d`, `pct_change_12m`).
  - Flags: `--seed-fred` (step 1 only), `--compute` (step 2 only), `--indicator KEY` (limit to one key, repeatable), `--days N` (history window, default 90), `--dry-run`
  - Default (no flag): runs both steps
  - Step 1 (`--seed-fred`): iterates all FRED daily observations in 90-day window via `obb.economy.fred_series()`, inserts with `ON CONFLICT DO NOTHING`; monthly FRED skipped (too few observations)
  - Step 2 (`--compute`): issues per-key `UPDATE macro_indicators` with 7 correlated subqueries (LIMIT 7/30 + OFFSET 11 for pct_change_12m); commits per-key to avoid lock timeout. ~2200 UPDATEs < 5 min.
  - Requires migrations 038+039 applied before running. Safe to re-run (idempotent).

### Entity Management
- `extract_entities.py` - Run NER extraction on articles
- `backfill_entities.py` - Backfill entity data for older articles
- `clean_entities.py` - Clean garbage entities using blocklist
- `deep_clean_entities.py` - Deep deduplication of entities
- `add_sample_entities.py` - Load sample entities for testing

### Geocoding
- `geocode_geonames.py` - **Primary geocoder**: 4-step hybrid pipeline
  1. GeoNames exact/ascii/alternate name lookup against `geo_gazetteer` table
  2. Gemini (T4a, `LLMFactory.get("t4a")` — Gemini 2.5 Flash-Lite) CoT for disambiguation when >1 match (→ clean_name, country_code, feature_type)
  3. Filtered GeoNames lookup using Gemini output
  4. Photon API fallback for locations not in GeoNames
  - CLI: `--limit N`, `--backfill`, `--types GPE LOC`, `--dry-run`
  - **Fixed 2026-07-08:** previously hardcoded `genai.GenerativeModel('gemini-2.0-flash')` outside `LLMFactory` — Google deprecated the model, causing a cascade of 43 failed calls per run that silently fell back to Photon-only geocoding. Now routed through `LLMFactory.get("t4a")`.
- `load_geonames.py` - **GeoNames database loader**: imports `allCountries.txt` + `alternateNames.txt` dump into `geo_gazetteer` table (~2–3M rows, feature classes A/P/H/L). Run once. ~10–15 min. Requires migration 023.
- `geocode_entities.py` - Legacy Photon geocoder (kept as fallback)
- `geocode_batch.py` - Batch geocoding utility
- `clean_geocoding.py` - Clean invalid geocoding data
- `refresh_map_data.py` - **Post-pipeline map refresh**: invalidates the map entity cache via `POST /api/v1/map/cache/invalidate` and re-runs `compute_intelligence_scores()`. Should be called after `process_narratives.py` to ensure the map reflects the latest storyline data.

### Embeddings & Search
- `backfill_report_embeddings.py` - Generate embeddings for existing reports
- `backfill_report_titles.py` - **One-time backfill**: generates LLM titles (via `gemini-2.5-flash-lite`, bumped from deprecated `gemini-2.0-flash` on 2026-07-08) for reports where `metadata->>'title'` is NULL. CLI flag: `--dry-run`. Updates `metadata` JSONB in-place. New reports get titles automatically from `report_generator.py`.

### Storylines / Narrative Engine
- `process_narratives.py` - **Primary**: Run NarrativeProcessor daily batch (HDBSCAN + LLM evolution + graph)
- `rebuild_graph_edges.py` - **Graph rebuild utility**: Drops all existing `storyline_edges`, then recomputes TF-IDF weighted Jaccard edges for all active storylines. **Critical**: loads IDF weights via `processor._load_entity_idf(cur)` and passes them to `_update_graph_connections(sid, idf_weights)` — without this, the fallback threshold is 0.30 instead of 0.05, resulting in ~90% fewer edges. Also includes Step 0: cleanup of stale edges involving storylines archived >30 days.
- `compute_communities.py` - **Community detection**: Louvain algorithm (python-louvain + networkx) on the storyline graph. Defaults from `config/narrative_clustering.yaml` (`community.min_weight`, `community.resolution`). Writes `community_id` to `storylines` table. After detection, calls Gemini 2.0 Flash to generate a descriptive `community_name` (e.g. "Hormuz Crisis", "Iran Regional Crisis") based on member storyline titles. CLI flags: `--min-weight`, `--resolution`, `--max-name`, `--dry-run`, `--apply-sparsification`, `--compare-sparsification`.
  - **Phase 1C observability**: persists per-run metrics to `narrative_run_metrics` (silhouette, community_coherence_med, modularity, TCS via NMI on intersection with previous run, runtime_seconds) and per-storyline partition history to `storyline_community_history` for cross-run lineage tracking.
  - **Phase 1D disparity filter (shadow mode by default)**: helper `_disparity_filter_backbone()` implements Serrano-Boguñá-Vespignani (2009) backbone extraction — for each edge `(i,j)` with weight `w_ij`, the test `(1 - w_ij/s_i)^(k_i - 1) < alpha` is applied from both endpoints (union: edge survives if EITHER side passes). Degree-1 nodes are kept unconditionally (formula degenerates). If the backbone is empty (alpha too tight), falls back to `weight >= fallback_threshold`. Parameters from `config/narrative_clustering.yaml`'s `sparsification` section (`alpha=0.3`, `fallback_threshold=0.10`). Without `--apply-sparsification`, the backbone is computed and its size + weight percentiles persisted (`n_edges_post_filter`, `backbone_weight_p50`, `backbone_weight_p75`) but Louvain still runs on the full active+dedup edge set — zero behavior change vs Phase 1C. With `--apply-sparsification`, Louvain runs on the backbone (intended for Phase 1E promotion alongside Leiden+CPM).
  - **`--compare-sparsification` (diagnostic, no DB writes)**: runs Louvain twice on the same active edge set — once on the full graph, once on the disparity backbone — and prints a side-by-side comparison of `n_edges`, `n_communities`, `n_singletons`, `max_community_size`, `avg_community_size`, `modularity`, `silhouette`, `coherence_med`. Used to measure the impact of `--apply-sparsification` on production data before promoting it. Skips all DB writes (no `narrative_run_metrics`, no `storyline_community_history`, no `storylines.community_id` update, no LLM community naming).
  - **Phase 1E 4-way shadow comparison framework (Decision 22)**: each `community_detection` run computes 4 partitions as pure observation metrics — `louvain_full`, `louvain_backbone`, `leiden_full`, `leiden_backbone` — and persists them to `narrative_run_metrics.shadow_partitions JSONB` (migration 046). Only `louvain_full` is the partition actually applied to `storylines.community_id`; the other 3 are shadow. Helpers: `_run_leiden_cpm(all_ids, edges, γ)` (leidenalg + CPMVertexPartition, seed=42, deterministic; igraph built via `_build_igraph`), `_run_leiden_cpm_adaptive_sweep()` (per-run γ-sweep: `geomspace(0.1·median_w, 2·median_w, 8)` clamped into `community.resolution_sweep`, picks the winner via the Decision-22 gate `avg_community_size ∈ [80,240]` + `max_size_ratio ≤ 0.20` + `coherence_med ≥ coherence_median_min`, else **3-level fallback** (Fix 2 / `fix-clustering-singleton-bias`): (1) highest `coh_med_k5` among candidates that have at least one cluster with ≥5 members — debiases against micro-cluster median inflation; (2) `max(modularity)` tertiary fallback when no γ produces any ≥5-member cluster. The chosen `fallback_path` (`gate_passed` | `coh_med_k5` | `modularity_tertiary`) is persisted in the shadow_partitions entry alongside `coh_med` and `coh_med_k5` so the audit trail is complete. `_compute_quality_metrics` returns `(silhouette, coh_med, coh_med_k5)` and accepts `k_min=5`. `_run_louvain_and_score`/`_run_leiden_cpm_and_score` (unified JSON schema via `_score_dict`), `compute_shadow_partitions()` (orchestrates the 4). Gated by `community.shadow_comparison.enabled` (instant reversibility, Louvain-only when off); skipped on `--dry-run`; degrades to 2 Louvain partitions when leidenalg/igraph unavailable. `_persist_run_metrics` writes `shadow_partitions` via `psycopg2.extras.Json`, with graceful fallback (re-inserts base metrics) when migration 046 isn't applied. Flat dashboard view: `v_shadow_partitions_unnested`.
  - **Phase 1F singleton isolation**: helper `_isolated_node_ids(all_ids, edges)` finds storylines with `degree=0` in the edge set actually fed to community detection (full or backbone depending on `--apply-sparsification`). Those isolated storylines are pulled out of the partition right after Louvain (before renumber + quality metrics), so they receive `community_id = NULL` (via the existing "null out" UPDATE) instead of a one-off singleton cluster. This keeps `n_singletons`, silhouette, TCS, and partition history free of non-community noise. Reported as `n_isolated` in stats/output. Frontend renders `community_id === null` with `SINGLETON_COLOR` (`#6B7280`) in `web-platform/lib/communityColors.ts`.
- `theme_clustering.py` - **k-means-on-embedding champion + HDBSCAN challenger shadow** (`narrative-clustering-embedding-based`, successor to the Louvain/Leiden entity-graph path in `compute_communities.py`). Clusters `storylines.current_embedding` (384-dim) directly instead of the entity co-occurrence graph — validated on prod: k-means silhouette +0.171 (k=18) vs live Louvain −0.024 (Δ+0.195). Two-tier stability (design.md § Decision 2): **daily** path (`assign_storylines_nearest_centroid`) does nearest-centroid assignment against active centroids in `narrative_themes` (migration 045) — no re-fit, deterministic; **periodic** path (`refit_with_warm_start`, cadence `theme_clustering.refit_cadence_days` or drift-triggered) re-fits k-means initialized from the previous active centroids (warm-start, not `k-means++`) and runs Hungarian matching (`_hungarian_match_centroids`, `scipy.optimize.linear_sum_assignment`, cost=cosine distance) for cross-run lineage: matched centroids keep their `persistent_id` (`lifecycle_status='active'`), unmatched new centroids become `emerging`, orphaned old centroids become `dormant` (never deleted — can re-emerge). Storylines below `theme_clustering.outlier_threshold` cosine similarity to the nearest centroid get `community_id = NULL` (outlier bucket). HDBSCAN (`sklearn.cluster.HDBSCAN`, no external `hdbscan` package needed) runs as a **permanent challenger** at every periodic re-fit, writing exclusively to `storylines.community_id_shadow` — never promotable (no warm-start support, so it can't satisfy the stability requirement). Naming reuses `_name_community()` unchanged (`name_themes()` adapts the input to theme members instead of Louvain community members), propagating to both `narrative_themes.label` and `storylines.community_name`. Reuses (does not duplicate) `_fetch_storyline_embeddings`, `_vec_to_array`, `_score_dict`, `_persist_run_metrics` from `compute_communities.py`. Drift detection (`detect_drift()`) compares TCS/coherence_med/EPR against the rolling p50/30d baseline in `narrative_run_metrics`; k-retune (`sweep_k_for_best_silhouette`, sweeps `theme_clustering.k_sweep_range`) fires only after 2 consecutive drift-flagged re-fits (`count_consecutive_drift_signals`), never on an isolated one. **Shadow-period rollout**: entry point `run_theme_clustering(promoted: bool)` — `promoted=False` (default) writes k-means output to the temporary `storylines.community_id_kmeans_shadow` column (migration 045) while HDBSCAN keeps writing `community_id_shadow`; `promoted=True` (post-promotion, not yet flipped in `daily_pipeline.py`) writes directly to `storylines.community_id`. Promotion criterion is the validation triangle (stability/separation/fragmentation) measured via `scripts/diagnose_clustering_signal.py` (unmodified, read-only) over 2 consecutive re-fits — no rigid AND gate (lesson from the 4-way Louvain/Leiden shadow comparison: 0/71 passes). CLI: `python scripts/theme_clustering.py [--promoted] [--dry-run] [--max-name N]`. Config: `config/narrative_clustering.yaml::theme_clustering` (+ reinterpreted `community_lineage.composite_weights.weighted_jaccard` as member-overlap, not entity-Jaccard). **Wired into `daily_pipeline.py`** (2026-07-13) as the `theme_clustering_shadow` step, right after `community_detection` — runs unpromoted (no `--promoted` flag) every day, so the shadow period (tasks.md 8.3-8.4) is now actually accumulating data. Still zero risk to the live report: `continue_on_failure=True`, writes only to `community_id_kmeans_shadow`/`community_id_shadow`/`narrative_run_metrics`, never to the live `storylines.community_id` (still Louvain's, via `compute_communities.py`).
- `migrate_community_names.py` - **Backfill**: Generates LLM community names (`gemini-2.5-flash-lite`, bumped from deprecated `gemini-2.0-flash` on 2026-07-08) for existing communities that don't have one yet. Safe to re-run (skips communities that already have a name).
- `migrate_storylines_to_en.py` - **Language migration**: Translates Italian storyline titles and summaries to English using Gemini (`gemini-2.5-flash-lite`, bumped from deprecated `gemini-2.0-flash` on 2026-07-08). One-time use for legacy Italian content.
- `reclean_storyline_entities.py` - **Batch entity cleanup**: Iterates all non-archived storylines, applies `_is_garbage_entity()` + `_clean_entity()` sanitization to `key_entities`, updates DB in-place. Used for one-time retroactive cleanup of pre-existing garbage entities.
- `pipeline_manifest.py` - **Pipeline manifest tracking**: Records pipeline run metadata (start/end time, steps run, success/failure) to a JSON manifest file for monitoring and debugging.
- `batch_storyline_clustering.py` - Legacy: Run DBSCAN clustering for storylines
- `test_storyline_clustering.py` - Legacy: Test storyline clustering

### Quality Auditing
- `audit_entity_quality.py` - Audit entity data quality: checks for garbage entities, geocoding gaps, low-mention counts, and duplicate names. Outputs a quality report to stdout.

### Static Data Freshness
- `check_static_data_freshness.py` - **Upstream freshness checker**: queries the DB for current state of each static table and does lightweight upstream checks (no bulk downloads) to detect available updates. Covers IMF WEO, World Bank, UCDP, and OpenSanctions. CLI flag: `--no-db` (upstream only, no DB access). Requires `UCDP_API_TOKEN` for UCDP checks.

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
