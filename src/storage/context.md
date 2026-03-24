# Storage Context

## Purpose
PostgreSQL database layer with pgvector extension for the RAG (Retrieval-Augmented Generation) system. Handles connection pooling, schema initialization, article/chunk storage, vector similarity search, HITL report management, and **narrative storyline persistence** (storylines, graph edges, article-storyline links).

## Architecture Role
Central persistence layer between the processing pipeline and intelligence generation. All NLP-processed articles flow here for storage, and the LLM modules retrieve context via semantic search. Also stores generated reports, human feedback, **narrative storylines with momentum scoring**, and **inter-storyline graph edges**.

## Key Files

- `database.py` - `DatabaseManager` class (~2445 lines)

  **Connection Management:**
  - `SimpleConnectionPool` (min=1, max=10 connections)
  - `get_connection()` context manager with auto-commit/rollback
  - `register_vector()` for pgvector type handling

  **Schema Initialization (`init_db()`):**
  - `articles` table - Full articles with NLP metadata, embeddings (384-dim)
  - `chunks` table - RAG chunks with embeddings for semantic search
  - `reports` table - LLM-generated intelligence reports
  - `report_feedback` table - Human corrections and ratings
  - `entities` table - Named entities with geocoding (for Intelligence Map)
  - **`storylines` table** - Narrative threads: title, summary, embedding, momentum_score, narrative_status
  - **`storyline_edges` table** - Graph edges: source/target storyline, Jaccard weight, relation_type
  - **`article_storylines` table** - Junction: article_id ↔ storyline_id, relevance_score
  - HNSW indexes for fast approximate nearest neighbor search

  **Source Cache (migration 024):**
  - `self._source_cache` — dict `{feed_name: (source_id, domain)}`, loaded lazily on first `save_article()` call
  - `_load_source_cache()` — queries `intelligence_sources.feed_names` once per session; gracefully no-ops if migration not applied

  **Core Operations:**
  - `save_article()` / `batch_save()` - Store articles with content-hash deduplication; auto-populates `source_id` and `domain` via source cache
  - `semantic_search()` - Vector similarity search on chunks with filters
  - `full_text_search()` - PostgreSQL `ts_query` for keyword search
  - `hybrid_search()` - Combines vector + keyword with RRF fusion
  - `save_report()` / `update_report()` - Report lifecycle management
  - `save_feedback()` / `get_report_feedback()` - HITL feedback storage

  **Specialized Methods:**
  - `get_all_article_embeddings(days, exclude_assigned)` - Returns articles with embeddings for storyline clustering; `exclude_assigned=True` skips articles already in `article_storylines` (used by `NarrativeProcessor.process_daily_batch`)
  - `get_entities_with_coordinates()` - GeoJSON output for map (legacy; new map router uses `get_entities_for_map`)
  - `get_entities_for_map(limit, entity_types, days, min_mentions, min_score, search)` - GeoJSON FeatureCollection with enriched properties (intelligence_score, storyline_count, top_storyline, community_id); supports filtering by type, recency, score, search
  - `get_entity_detail_with_storylines(entity_id)` - Full entity detail with related articles and storylines (traverses entity_mentions → articles → article_storylines)
  - `get_entity_arcs(min_score, limit)` - GeoJSON LineStrings for entity pairs sharing storylines; both endpoints must have intelligence_score ≥ min_score
  - `get_map_stats()` - Live HUD stats: total entities, geocoded count, active storylines, type breakdown
  - `compute_intelligence_scores()` - Updates `intelligence_score` on `entities` table using `mv_entity_storyline_bridge`; uses a `scores` CTE to avoid PostgreSQL UPDATE-FROM self-join limitation
  - `update_report_embedding(report_id, embedding)` - Updates `summary_vector` for a report
  - `semantic_search_reports()` - Search reports by embedding (for Oracle)
  - `get_reports_by_date_range()` - For weekly meta-analysis

## Database Views (Narrative Engine)

| View | Purpose |
|------|---------|
| `v_active_storylines` | Active storylines ordered by momentum_score DESC, with article_count, community_id |
| `v_storyline_graph` | Edges between active storylines, includes source/target titles |
| `entity_idf` (materialized) | TF-IDF inverse document frequency weights for entities; used by graph builder for weighted Jaccard. Refreshed by migration 016 cleanup and `REFRESH MATERIALIZED VIEW entity_idf`. |
| `mv_entity_storyline_bridge` (materialized) | Pre-aggregates per-entity: storyline count, max momentum, bridge score. Used by `compute_intelligence_scores()` for fast bulk updates to `entities.intelligence_score`. Created by migration 019. |

These views are consumed by both the report generator (top 10 storylines for narrative context) and the API (`/api/v1/stories/graph`).

**Important:** `DatabaseManager` does NOT have `get_active_storylines()` or `get_storyline_graph()` methods. The API router (`src/api/routers/stories.py`) queries these views directly via raw SQL. The NarrativeProcessor also reads/writes storyline data via raw SQL within its own stage methods, not through DatabaseManager helper methods.

## Dependencies

- **Internal**: `src/utils/logger`
- **External**:
  - `psycopg2` - PostgreSQL adapter
  - `psycopg2.pool.SimpleConnectionPool` - Connection pooling
  - `pgvector.psycopg2` - Vector type registration
  - `psycopg2.extras.Json` - JSONB handling

## Data Flow

- **Input**:
  - Processed articles with NLP data and embeddings from `src/nlp/`
  - Query embeddings for semantic search from `src/llm/`
  - Generated reports from `src/llm/report_generator.py`
  - Human feedback from `src/hitl/`
  - **Storyline data from `src/nlp/narrative_processor.py`** (storylines, edges, article links)

- **Output**:
  - Retrieved chunks for RAG context
  - Article metadata and statistics
  - Reports for review/editing
  - GeoJSON entities for Intelligence Map
  - **Storyline graph data for API** (nodes, edges, detail)
  - **Narrative context for report generation** (top storylines by momentum)
  - Feedback data for prompt improvement

## Key Tables

| Table | Purpose |
|-------|---------|
| **`intelligence_sources`** | Anagrafica fonti: name, domain, source_type, authority_score (1-5), llm_context, feed_names[], has_rss (migration 024) |
| `articles` | Full articles, embeddings, NLP metadata, entities (JSONB); `source_id` FK → intelligence_sources, `domain` denormalizzato (migration 024) |
| `chunks` | 500-word chunks with 384-dim embeddings for RAG |
| `reports` | Generated intelligence reports (draft/final/status) |
| `report_feedback` | Human corrections, ratings, comments |
| `entities` | Named entities with coordinates for map; includes `intelligence_score` column (migration 019) |
| **`storylines`** | Narrative threads: title, summary, embedding, momentum_score, narrative_status (emerging/active/stabilized/archived), **community_id** (Louvain community assignment from `scripts/compute_communities.py`) |
| **`storyline_edges`** | Graph edges: source_story_id → target_story_id, TF-IDF weighted Jaccard weight, relation_type |
| **`article_storylines`** | Junction table: article_id ↔ storyline_id, relevance_score |
| `market_data` | OHLCV time series from Yahoo Finance |
| `ticker_mappings` | Entity → Stock ticker mappings |
