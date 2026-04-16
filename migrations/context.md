# Migrations Context

## Purpose
SQL migration scripts that evolve the PostgreSQL database schema over time. Each migration is numbered sequentially. Migrations add new capabilities without losing existing data. The base schema is in `src/storage/database.py`; migrations add incremental enhancements.

## Architecture Role
Schema evolution layer that extends the core database as new features are added. **Migrations are applied manually** via `psql` or `load_to_database.py --init-only`. They must be run in order before using new functionality.

## Migration Reference

### Phase 2: Deduplication
- `001_add_content_hash.sql` — Adds `content_hash` (MD5) column to `articles` for content-based deduplication
- `001_add_content_hash_rollback.sql` — Removes the column

### Phase 3: Reporting
- `002_add_report_type.sql` — Adds `report_type` column (`daily`/`weekly`) to `reports` table
- `002_add_report_type_rollback.sql` — Removes the column

### Intelligence Map
- `003_add_entity_coordinates.sql` — Adds `latitude`, `longitude`, `geo_status` to `entities` table for geocoding
- `003_add_entity_coordinates_rollback.sql` — Removes coordinate columns

### Market Intelligence
- `004_add_market_intelligence_schema.sql` — Creates `ticker_mappings` and `market_data` tables; adds `ai_analysis` JSONB to `articles`
- `005_add_trade_signals.sql` — Adds trade signal storage and scoring tables

### RAG Enhancements
- `006_add_report_embeddings.sql` — Adds `content_embedding` vector column to `reports` for semantic search over historical reports
- `007_add_full_text_search.sql` — Adds `content_tsv` tsvector column and GIN index on `articles` for full-text keyword search (`ts_query`)

### Narrative Engine — Core Schema
- `008_add_storylines.sql` — Creates `storylines`, `article_storylines` tables; creates views `v_active_storylines`, `v_articles_with_storylines`
  - Dual embedding approach: `original_embedding` (snapshot) + `current_embedding` (drift tracking)
  - `momentum_score` float for activity decay
  - Initial `narrative_status` values: `emerging`, `active`
- `008_add_storylines_rollback.sql` — Drops storyline tables and views

### OpenBB / Financial
- `009_add_openbb_schema.sql` — Schema for OpenBB financial data: macro indicators, fundamentals caching tables
- `010_financial_intel_v2.sql` — Enhanced financial intelligence tables: `ticker_fundamentals`, `macro_indicators` improvements

### Audit & Oracle
- `011_add_audit_trail.sql` — Adds `audit_log` table for tracking article/report mutations
- `013_add_oracle_query_log.sql` (also `013_oracle_query_log.sql`) — Creates `oracle_query_log` table for logging Oracle 2.0 queries with intent, tools used, latency, session_id. `DatabaseManager.log_oracle_query()` silently no-ops if table doesn't exist.
- `014_oracle_users_stub.sql` — Oracle user management stub table (future multi-user support)

### Narrative Engine — Graph
- `012_narrative_graph.sql` — Creates `storyline_edges` table: `source_story_id`, `target_story_id`, `weight` (float), `relation_type` (default `"relates_to"`), timestamps
- `015_tfidf_graph_community.sql` — Three changes:
  1. Creates `entity_idf` materialized view (TF-IDF inverse document frequency for entities; used by NarrativeProcessor graph builder for weighted Jaccard)
  2. Adds `community_id` column to `storylines` (populated by `scripts/compute_communities.py` Louvain algorithm)
  3. Adds `last_graph_update` timestamp to `storylines`
- `016_graph_cleanup.sql` — Removes bidirectional edge duplicates (keeps higher-weight direction); archives stale edges (storylines archived >30 days); also runs `REFRESH MATERIALIZED VIEW entity_idf`
- `017_views_include_stabilized.sql` — Updates `v_active_storylines` and `v_storyline_graph` to include `narrative_status = 'stabilized'` (previously only `emerging` and `active`). **Required for stories API and narrative context to include stabilized storylines.**

### Narrative Engine — Orphan Buffer & Intelligence Scores
- `018_orphan_buffer_pool.sql` — Creates `orphan_events` table for retry mechanism: events that couldn't be matched or clustered are stored here and retried on the next pipeline run (`NarrativeProcessor._retry_orphan_pool()`). Includes `expires_at` for 14-day TTL.
- `019_entity_storyline_bridge.sql` — Two changes:
  1. Creates `mv_entity_storyline_bridge` materialized view: pre-aggregates per-entity storyline count, max momentum, bridge score
  2. Adds `intelligence_score` float column to `entities` table (populated by `DatabaseManager.compute_intelligence_scores()` via the materialized view)

### Public Access & Community
- `020_waitlist.sql` — Creates `waitlist` table for early access registrations (email, created_at, status)
- `021_reports_public.sql` — Adds `is_public` boolean column to `reports` for public-facing access control; adds `published_at` timestamp
- `022_community_name.sql` — Adds `community_name` text column to `storylines`; used for Louvain community labeling in API responses

### Geo & Sources
- `023_geo_gazetteer.sql` — Creates `geo_gazetteer` reference table for geographic name lookup (country codes, canonical coordinates)
- `024_intelligence_sources.sql` — Creates `intelligence_sources` table (anagrafica fonti): `name`, `domain`, `source_type`, `authority_score` (1–5), `llm_context`, `feed_names[]`, `has_rss`. Adds `source_id` FK and `domain` denormalized text column to `articles`.
- `025_chunks_source_id.sql` — Adds `source_id` FK column to `chunks` table (mirrors `articles.source_id` for source-aware RAG retrieval)

### Knowledge Base Expansion (PostGIS & Reference Data)
- `026_postgis.sql` — Enables PostGIS extension (`CREATE EXTENSION IF NOT EXISTS postgis`). Prerequisite for all spatial tables.
  - `026_postgis_rollback.sql` — Drops PostGIS extension (CASCADE: drops all spatial tables)
- `027_country_profiles.sql` — Creates `country_profiles` reference table (ISO3 PK, macro data, governance score). Populated by `scripts/load_world_bank.py`.
- `028_country_boundaries.sql` — Creates `country_boundaries` table with `GEOMETRY(MultiPolygon, 4326)` + GIST index. Populated by `scripts/load_natural_earth.sh` (50m resolution via ephemeral GDAL container).
- `029_conflict_events.sql` — Creates `conflict_events` table with `GEOMETRY(Point, 4326)` + GIST/temporal indexes. Populated by `scripts/load_ucdp.py` (UCDP GED API v24.1).
- `030_sanctions_registry.sql` — Creates `sanctions_registry` table with GIN indexes on `countries[]` and `datasets[]`. Populated by `scripts/load_opensanctions.py` (FtM NDJSON).
- `031_strategic_infra.sql` — Creates `strategic_infrastructure` table with `Point` + `LineString` geometries, GIST indexes, and `infra_type` CHECK constraint (11 types). Populated by TeleGeography data.
- `032_macro_forecasts.sql` — Creates `macro_forecasts` table for IMF WEO forward-looking projections with vintage tracking. Populated by `scripts/load_imf_weo.py`.
- `033_trade_flow_indicators.sql` — Creates `trade_flow_indicators` table for bilateral trade data with commodity classification.
- `034_sanctions_view.sql` — Creates `v_sanctions_public` view over `sanctions_registry`. Strips PII fields from `properties` JSONB (`birthDate`, `birthPlace`, `address`, `idNumber`, `taxNumber`, `passportNumber`, `nationalId`, `registrationNumber`, `phone`, `email`) using JSONB `-` operator. Rationale: pseudo Row-Level Security without PostgreSQL roles — all Oracle 2.0 tools (`SQLTool`, `ReferenceTool`) must query this view instead of the base table. Base table remains writable by data loaders.

### Ontological Layer (no migration required)

The **OntologyManager** (`src/knowledge/ontology_manager.py`) loads `config/asset_theory_library.yaml` at application boot — singleton pattern, pure application-layer. Provides JIT theoretical context for the top anomalous macro indicators during report generation via `_generate_macro_analysis()`. No DB schema changes; reads existing `macro_indicators` table. Active indicators: 30 (removed TED_SPREAD, EPU_GLOBAL, USD_RUB in Phase 1; ALUMINUM→ALI=F daily, WHEAT→ZW=F daily, USD_GBP/USD_CNY→yfinance daily).

### Strategic Intelligence Layer — Phase 1 (2026-04-10)

- `035_macro_intelligence_layer.sql` — Creates two tables:
  1. `macro_indicator_metadata` — per-indicator data quality tracking: real data date (`last_updated`), `is_stale`, `staleness_days`, `expected_frequency`, `reliability`. Populated by `_upsert_indicator_metadata()` after every fetch. Fixes the NICKEL/monthly mislabeling bug (monthly FRED data was saved with fetch date, not real data date). Indexed: `(is_stale, expected_frequency)`, `(reliability)`.
  2. `macro_regime_history` — 60-day rolling macro regime history. Populated by `MacroRegimePersistence` singleton in Phase 4. Columns: `risk_regime`, `regime_confidence`, `active_convergence_ids[]`, `active_sc_sectors[]`, `macro_narrative`, `analysis_json` (full JSONB), `data_quality_snapshot`, `data_freshness_gap_days`. GIN indexes on arrays for Oracle 2.0 queries.

### Strategic Intelligence Layer — Phase 3 fix (2026-04-16)

- `036_add_previous_value_macro_indicators.sql` — Adds `previous_value NUMERIC(20,6)` column to `macro_indicators` + one-time backfill via correlated UPDATE. The Phase 3 screening function (`_get_macro_indicators_for_screening`) and `market_tool.py` both SELECT this column; it was never added to the schema, causing the entire v2 analysis path to bypass on every pipeline run since the merge. `_save_macro_indicator()` in `openbb_service.py` now populates this column at insert time via inline scalar subquery. Rollback: `ALTER TABLE macro_indicators DROP COLUMN IF EXISTS previous_value`.

## Applied in Production

Migrations applied to the Hetzner production database (as of 2026-03-24):
- 001 through 019: Applied
- 020 through 025: Applied (confirmed via memory: 018, 019, 024)
- 026 through 033: **Applied** (2026-03-31) — PostGIS 3.6 confirmed; all reference data loaded
- 034: **Not yet applied** — apply with: `docker compose -p app exec postgres psql -U intelligence_user -d intelligence_ita < migrations/034_sanctions_view.sql`
- 035: **Applied** (2026-04-14)
- 036: **Not yet applied** — apply after deploy: `docker compose -p app exec -T postgres psql -U intelligence_user -d intelligence_ita < migrations/036_add_previous_value_macro_indicators.sql`

## Execution Order

```
001 → 002 → 003 → 004 → 005 → 006 → 007 → 008 → 009 → 010
  → 011 → 012 → 013 → 014 → 015 → 016 → 017 → 018 → 019
  → 020 → 021 → 022 → 023 → 024 → 025
  → 026 (PostGIS — MUST rebuild container first)
  → 027 (no spatial dependency)
  → 028 → 029 → 031 (require PostGIS)
  → 030 (no spatial dependency)
  → 032 → 033 (no spatial dependency)
  → 034 (view only — no spatial dependency, requires 030 applied first)
  → 035 (Strategic Intelligence Layer — no external dependencies)
  → 036 (Strategic Intelligence Layer Phase 3 fix — no external dependencies)
```

Run a single migration:
```bash
psql -d intelligence_ita -f migrations/XXX_migration_name.sql
```

Rollback (only where rollback script exists):
```bash
psql -d intelligence_ita -f migrations/XXX_migration_name_rollback.sql
```

## Dependencies

- **Internal**: `src/storage/database.py` (base schema)
- **External**: PostgreSQL 14+ (tested on 17 in production with pgvector:pg17), pgvector extension, PostGIS 3 (required for migrations 026+)

