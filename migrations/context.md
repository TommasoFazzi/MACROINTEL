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

## Applied in Production

Migrations applied to the Hetzner production database (as of 2026-03-24):
- 001 through 019: Applied
- 020 through 025: Applied (confirmed via memory: 018, 019, 024)

## Execution Order

```
001 → 002 → 003 → 004 → 005 → 006 → 007 → 008 → 009 → 010
  → 011 → 012 → 013 → 014 → 015 → 016 → 017 → 018 → 019
  → 020 → 021 → 022 → 023 → 024 → 025
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
- **External**: PostgreSQL 14+ (tested on 17 in production with pgvector:pg17), pgvector extension
