# Database Schema

PostgreSQL 17 + pgvector (0.4.1) + PostGIS. 35 migrations applied as of 2026-04-14.

## Core Tables — ER Diagram

```mermaid
erDiagram
    intelligence_sources {
        int id PK
        text name
        text domain
        text source_type
        int authority_score "1-5"
        text llm_context
        text[] feed_names
    }

    articles {
        int id PK
        text link
        text title
        text full_text
        jsonb entities
        vector embedding "384-dim pgvector"
        text content_hash
        int source_id FK
        text domain
        text extraction_method
        bool is_long_document
        timestamptz published_at
        timestamptz ingested_at
    }

    chunks {
        int id PK
        int article_id FK
        text chunk_text
        vector embedding "384-dim pgvector"
        int chunk_index
        int source_id FK
    }

    entities {
        int id PK
        text name
        text type "GPE/ORG/PERSON/LOC/FAC"
        float latitude
        float longitude
        text geo_status
        float intelligence_score "0-1"
        int mention_count
    }

    reports {
        int id PK
        date report_date
        text report_type "daily/weekly/monthly"
        text status
        text draft_content
        text final_content
        vector summary_vector "384-dim"
        jsonb metadata "title, processing_time_ms, narrative_context"
    }

    trade_signals {
        int id PK
        int report_id FK
        text signal_type
        text instrument
        text direction "BULLISH/BEARISH/NEUTRAL/WATCHLIST"
        float confidence
        float intelligence_score "0-100"
    }

    articles ||--o{ chunks : "segmented into"
    articles }o--|| intelligence_sources : "sourced from"
    reports ||--o{ trade_signals : "generates"
```

---

## Narrative Engine Tables

```mermaid
erDiagram
    storylines {
        int id PK
        text title
        text summary
        text category
        vector embedding "384-dim"
        vector summary_vector "384-dim"
        float momentum_score "0-1 with decay"
        text narrative_status "emerging/active/stabilized/archived"
        int community_id
        text community_name
        date start_date
        timestamptz last_update
        int article_count
        jsonb key_entities
        int days_active
    }

    article_storylines {
        int article_id FK
        int storyline_id FK
        float relevance_score
    }

    storyline_edges {
        int source_story_id FK
        int target_story_id FK
        float weight "TF-IDF Jaccard ≥ 0.05"
        text relation_type
        timestamptz last_updated
    }

    orphan_events {
        int id PK
        vector event_embedding "384-dim"
        jsonb event_articles
        jsonb event_entities
        timestamptz expires_at "14-day TTL"
    }

    storylines ||--o{ article_storylines : "contains"
    storylines ||--o{ storyline_edges : "connects (source)"
    storylines ||--o{ storyline_edges : "connects (target)"
```

---

## Macro Intelligence Tables

```mermaid
erDiagram
    macro_indicators {
        text key PK "e.g. VIX, USD_CNH, BRENT"
        float value
        date data_date "real FRED/CME data date"
        float delta_pct
        text materiality
        text category "EQUITY/FX/COMMODITY/RATES/CREDIT"
        bool is_stale
        int staleness_days
    }

    macro_indicator_metadata {
        text key PK
        date last_updated
        text expected_frequency "daily/weekly/monthly"
        bool is_stale
        int staleness_days
        text reliability
        date data_date "migration 035"
    }

    macro_regime_history {
        int id PK
        date analysis_date
        text risk_regime "7 Literal values"
        float regime_confidence
        text[] active_convergence_ids
        text[] active_sc_sectors
        text macro_narrative
        jsonb analysis_json
        jsonb data_quality_snapshot
    }

    macro_indicators ||--|| macro_indicator_metadata : "tracked by"
```

---

## Knowledge Base Tables (Migrations 026+)

```mermaid
erDiagram
    country_profiles {
        text iso3 PK
        text name
        float gdp_usd
        text governance_score
        jsonb indicators
    }

    sanctions_registry {
        int id PK
        text entity_name
        text entity_type
        text[] programs
        jsonb properties "⚠️ PII — use v_sanctions_public"
        date listed_date
    }

    macro_forecasts {
        int id PK
        text iso3
        text indicator
        int forecast_year
        float value
        text vintage "IMF WEO edition"
        date loaded_at
    }

    conflict_events {
        int id PK
        geometry location "PostGIS Point"
        text country
        int year
        text dyad_name
        int deaths_total
    }

    geo_gazetteer {
        int geonameid PK
        text name
        float latitude
        float longitude
        text country_code
        text feature_class
        int population
    }
```

---

## Views & Materialized Views

| Name | Type | Filter | Primary Consumers |
|------|------|--------|------------------|
| `v_active_storylines` | View | status IN ('emerging','active','stabilized') ORDER BY momentum DESC | Narrative engine Stage 2, report generator, API /stories |
| `v_storyline_graph` | View | Edges between non-archived storylines + titles | API /stories/graph |
| `v_sanctions_public` | View | Strips PII columns from sanctions_registry | SQLTool, ReferenceTool (NEVER use raw table) |
| `entity_idf` | Materialized | IDF(entity) = log(N/df) across all storylines | Narrative engine Stage 5 (TF-IDF Jaccard) |
| `mv_entity_storyline_bridge` | Materialized | Per-entity: storyline_count, max_momentum, bridge_score | intelligence_score computation in refresh_map_data.py |

**Refresh cadence:** Both materialized views are refreshed by `scripts/refresh_map_data.py` (pipeline Step 9) after each run.

---

## Migration History

| Range | Key Changes |
|-------|------------|
| 001-007 | content_hash, report_type, PostGIS coordinates, market schema, trade_signals, report embeddings, FTS indexes |
| 008-012 | Storylines + narrative engine, OpenBB macro schema, financial intelligence v2, audit trail, narrative graph edges |
| 013-019 | oracle_query_log, users, waitlist, knowledge base (country_profiles, sanctions, forecasts, conflict_events), geo_gazetteer, intelligence_sources, mv_entity_storyline_bridge |
| 020-025 | Community detection (community_id), community_name, geo_gazetteer GIN index, GeoNames alternates, intelligence_sources v2, chunks.source_id |
| 026-034 | Structured intelligence (UCDP, OpenSanctions, IMF WEO, World Bank), v_sanctions_public PII view |
| 035 | macro_indicator_metadata + macro_regime_history (Strategic Intelligence Layer) |

**Next migration:** `036_*.sql`
