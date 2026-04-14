# Database Conventions

## Migration Naming

Files live in `migrations/`. Applied **manually** via psql (never auto-applied).

```
NNN_short_description.sql
```

- `NNN` = zero-padded 3-digit sequence (next is `036`)
- After creating a migration, document it in `migrations/context.md`
- To apply in production:
  ```bash
  docker compose -p app exec postgres psql -U intelligence_user -d intelligence_ita \
    -f /opt/intelligence-ita/repo/migrations/036_name.sql
  ```

**Current migration count:** 035 applied in production as of 2026-04-14.

## Primary Views (Use These, Not Raw Tables)

| View | Purpose | Use Instead Of |
|------|---------|----------------|
| `v_active_storylines` | `emerging`, `active`, `stabilized` storylines ordered by momentum DESC, includes `community_id` | `storylines` table directly |
| `v_storyline_graph` | Graph edges between non-archived storylines with titles | `storyline_edges` directly |
| `v_sanctions_public` | PII-sanitized sanctions data | `sanctions_registry` (NEVER use raw) |

## SQLTool Safety (Oracle 2.0)

`ALLOWED_TABLES` in `src/llm/tools/sql_tool.py` is a whitelist. When adding a new table/view:
1. Add it to `ALLOWED_TABLES`
2. Add example queries to `_SQL_EXAMPLES` in `src/llm/query_router.py`
3. If it contains PII → create a sanitized view first, add the view (not the raw table)

**Forbidden always:** `sanctions_registry` (raw) — use `v_sanctions_public`.

## Materialized Views — Refresh Required

| View | Refresh After |
|------|--------------|
| `entity_idf` | Bulk entity inserts / narrative processing |
| `mv_entity_storyline_bridge` | Narrative processing, storyline status changes |

```sql
REFRESH MATERIALIZED VIEW entity_idf;
REFRESH MATERIALIZED VIEW mv_entity_storyline_bridge;
```

`scripts/refresh_map_data.py` runs both automatically (pipeline step 9).

## Key Tables Reference

| Table | Purpose |
|-------|---------|
| `articles` | Ingested articles (text, embeddings, metadata) |
| `storylines` | Narrative threads (title, summary, embedding, momentum_score, narrative_status, community_id) |
| `storyline_edges` | TF-IDF Jaccard weighted graph edges (threshold 0.05) |
| `article_storylines` | Junction: article_id ↔ storyline_id + relevance_score |
| `macro_indicators` | Market/economic data time series |
| `macro_indicator_metadata` | Real data dates per indicator (migration 035) |
| `macro_regime_history` | 60-day regime snapshots (migration 035) |
| `entities` | Geocodable entities with `intelligence_score` (migration 019) |
| `reports` | Generated LLM reports |
| `oracle_query_log` | Oracle 2.0 query audit log (migration 013 — silently no-ops if missing) |

## UTF-8 / Encoding Safety

Web scraping produces invalid UTF-8 bytes that PostgreSQL rejects.

- `src/storage/database.py`: `_sanitize_text()` applied in `save_article()` — handles surrogates
- `src/nlp/narrative_processor.py`: `_evolve_narrative_summary()` has fallback query without `LEFT(full_text, 200)` snippet on encoding error

Any new code that saves text from external sources must call `_sanitize_text()` before writing.

## Connection Pooling

All DB access goes through `src/storage/database.py`. Never create raw psycopg2 connections directly in scripts — use the `DatabaseManager` class and its connection pool.
