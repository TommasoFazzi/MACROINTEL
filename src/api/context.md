# API — Context

## Purpose

This module is the FastAPI REST backend for the Intelligence ITA platform. It exposes structured JSON endpoints consumed by the Next.js frontend (`web-platform/`) and, for map data, potentially other clients. It provides four logical groups of endpoints: GeoJSON entity map data (`routers/map.py`), dashboard statistics, report listing/detail, and storyline narrative graph data.

## Architecture Role

The API sits at the output layer of the pipeline, downstream of all processing steps:

```
PostgreSQL (storage) → src/api/ → Next.js frontend (web-platform/)
```

It reads exclusively from the database — it performs no writes, no LLM calls, and no NLP operations. All CORS-restricted HTTP methods are limited to `GET` and `OPTIONS`. The frontend communicates with the backend via an internal API proxy at `/api/proxy/[...path]` to keep the API key out of the browser bundle.

Key database objects consumed:
- `entities`, `articles`, `entity_mentions` tables (map endpoints)
- `reports`, `report_feedback` tables (reports endpoints)
- `storylines`, `storyline_edges`, `article_storylines` tables (stories endpoints)
- `v_active_storylines`, `v_storyline_graph` views (stories graph endpoint)

## Key Files

| File | Description |
|------|-------------|
| `main.py` | FastAPI application entry point. Configures the app, CORS middleware, rate limiter, and includes all sub-routers (dashboard, reports, stories, map, oracle). |
| `routers/map.py` | Dedicated map router. Handles all `/api/v1/map/` endpoints with in-memory TTL cache (5 min). Uses `JSONResponse` (not ORJSONResponse). Endpoints: `GET /entities` (GeoJSON FeatureCollection with filters), `GET /entities/{id}` (entity detail + storylines), `GET /arcs` (co-occurrence LineStrings), `GET /stats` (HUD stats), `POST /cache/invalidate`. |
| `auth.py` | Shared authentication module. Implements `verify_api_key` as a FastAPI dependency using `APIKeyHeader`. Uses `secrets.compare_digest` for timing-safe comparison. Supports a dev-mode bypass when `INTELLIGENCE_API_KEY` is unset and `ENVIRONMENT != "production"`. |
| `routers/dashboard.py` | Dashboard statistics router. Aggregates article counts, entity counts, geocoding coverage, top sources, top mentioned entities, and report quality metrics into a single `DashboardStats` response. Uses multiple private helper functions (`_get_entity_stats`, `_get_quality_stats`, `_get_date_range`, `_count_reports`, `_calc_coverage`, `_count_articles_today`), each opening its own DB connection. |
| `routers/reports.py` | Reports router. Supports paginated listing with filters (status, type, date range), detailed retrieval by ID including sources and per-section feedback. Handles two source JSON shapes (flat list vs. dict with `recent_articles`/`historical_context` keys). **`compare_two_reports()` endpoint** invokes `report_compare_service.compare_reports()` with two report IDs to generate Gemini-synthesized delta (new_developments, resolved_topics, trend_shifts, persistent_themes). Derives the report title from metadata, the first content line, or a fallback. |
| `routers/stories.py` | Storyline and graph router (~525 lines). Exposes the force-graph network (nodes from `v_active_storylines`, edges from `v_storyline_graph`, cached 1h), **community listing** (Louvain communities with key entities), **ego network** (per-node subgraph with min_weight=0.05), a paginated storyline list (default: `emerging` + `active` only), and a storyline detail endpoint with related storylines (via `storyline_edges`, traversed in both directions) and the 10 most recent linked articles. Filters isolated nodes (0 edges) from the graph endpoint. Handles `key_entities` JSON parsing defensively. |
| `schemas/common.py` | Generic `APIResponse[T]` wrapper (success, data, error, generated_at) and `PaginationMeta` with a `calculate()` class method. |
| `schemas/dashboard.py` | Pydantic models for the dashboard endpoint: `OverviewStats`, `DateRange`, `SourceCount`, `ArticleStats`, `EntityMention`, `EntityStats`, `QualityStats`, `DashboardStats`. |
| `schemas/reports.py` | Pydantic models for reports: `ReportSource`, `ReportFeedback`, `ReportListItem`, `ReportSection`, `ReportContent`, `ReportMetadata`, `ReportDetail`, `ReportFilters`. Also defines `Literal` type aliases `ReportStatus`, `ReportType`, `Category`. |
| `schemas/stories.py` | Pydantic models for storyline graph: `StorylineNode`, `StorylineEdge`, `GraphStats`, `GraphNetwork`, `RelatedStoryline`, `LinkedArticle`, `StorylineDetail`. |

## Endpoints

| Method | Path | Router | Description | Auth Required |
|--------|------|--------|-------------|---------------|
| GET | `/` | main.py | API root, returns name and status | No |
| GET | `/health` | main.py | Health check | No |
| GET | `/api/v1/map/entities` | routers/map.py | GeoJSON FeatureCollection with filters: entity_type, days, min_mentions, min_score, search; limit 1–10000, default 5000; cached 5min | Yes |
| GET | `/api/v1/map/entities/{entity_id}` | routers/map.py | Entity detail with related articles and storylines | Yes |
| GET | `/api/v1/map/arcs` | routers/map.py | GeoJSON LineStrings for entity pairs sharing storylines; params: min_score (default 0.3), limit (default 300); cached 5min | Yes |
| GET | `/api/v1/map/stats` | routers/map.py | Live HUD stats: entity counts, geocoded count, active storylines, type breakdown | Yes |
| POST | `/api/v1/map/cache/invalidate` | routers/map.py | Invalidate map entity cache (called by refresh_map_data.py after pipeline) | Yes |
| GET | `/api/v1/dashboard/stats` | routers/dashboard.py | Aggregated dashboard statistics (overview, articles, entities, quality) | Yes |
| GET | `/api/v1/reports` | routers/reports.py | Paginated report list; query params: `page`, `per_page`, `status`, `report_type`, `date_from`, `date_to` | Yes |
| GET | `/api/v1/reports/{report_id}` | routers/reports.py | Full report detail: content, sources, feedback, metadata | Yes |
| GET | `/api/v1/reports/compare` | routers/reports.py | **LLM-synthesized delta analysis** between two reports; query params: `ids` (comma-separated report IDs, e.g. `42,38`); returns Gemini-generated delta: new_developments, resolved_topics, trend_shifts, persistent_themes | Yes |
| GET | `/api/v1/stories/graph` | routers/stories.py | Full narrative graph (nodes + links + aggregate stats) for react-force-graph; query params: `min_edge_weight` (default 0.10), `min_momentum` (optional); cached 1h; filters isolated nodes (0 edges). Views (`v_active_storylines`, `v_storyline_graph`) include `emerging`, `active`, **and `stabilized`** storylines (migration 017). | Yes |
| GET | `/api/v1/stories/communities` | routers/stories.py | Louvain community listing: community_id, size, label (from LLM summary of member titleset), top storylines, key entities, avg momentum | Yes |
| GET | `/api/v1/stories/{storyline_id}/network` | routers/stories.py | Ego network: returns the specified node plus all direct neighbors and connecting edges; min_weight=0.05 (lower than graph default to show weaker connections) | Yes |
| GET | `/api/v1/stories` | routers/stories.py | Paginated storyline list; query params: `page`, `per_page`, `status`; default filter: emerging + active | Yes |
| GET | `/api/v1/stories/{storyline_id}` | routers/stories.py | Storyline detail with related storylines (up to 10) and recent articles (up to 10) | Yes |
| POST | `/api/v1/oracle/chat` | routers/oracle.py | Oracle 2.0 chat: NL query → agentic tool loop (RAG/SQL/Graph/Market/...) → Claude Sonnet 4.6 synthesis. Body: `query`, `session_id`, `start_date`, `end_date`, `categories`, `gpe_filter`, `mode`. **BREAKING (2026-04-17)**: `gemini_api_key` BYOK field removed — passing it returns HTTP 422. Rate limit: **3/minute per IP**. | Yes |
| GET | `/api/v1/oracle/health` | routers/oracle.py | Oracle 2.0 service health check | No |

## Pydantic Schemas

### schemas/common.py

**`APIResponse[T]`** — Generic response envelope used by the dashboard router (declared as `response_model`) and echoed structurally by the stories/reports routers (which build equivalent plain dicts).
- `success: bool` (default: `True`)
- `data: T`
- `error: Optional[str]`
- `generated_at: datetime` (auto-set to `utcnow()`)

**`PaginationMeta`** — Pagination metadata.
- `page: int`, `per_page: int`, `total: int`, `pages: int`
- Class method `calculate(total, page, per_page)` computes `pages` using ceiling division.

### schemas/dashboard.py

**`OverviewStats`**: `total_articles: int`, `total_entities: int`, `total_reports: int`, `geocoded_entities: int`, `coverage_percentage: float` (0–100).

**`DateRange`**: `first: Optional[datetime]`, `last: Optional[datetime]`.

**`SourceCount`**: `source: str`, `count: int`.

**`ArticleStats`**: `by_category: dict[str, int]`, `by_source: list[SourceCount]`, `recent_7d: int`, `articles_today: int` (count of articles published/ingested today, UTC), `date_range: DateRange`.

**`EntityMention`**: `name: str`, `type: str` (default `"UNKNOWN"`), `mentions: int`.

**`EntityStats`**: `by_type: dict[str, int]`, `top_mentioned: list[EntityMention]` (top 10 by mention_count).

**`QualityStats`**: `reports_reviewed: int`, `average_rating: Optional[float]` (1–5, from `report_feedback`), `pending_review: int`.

**`DashboardStats`**: Composes `overview: OverviewStats`, `articles: ArticleStats`, `entities: EntityStats`, `quality: QualityStats`.

### schemas/reports.py

**`ReportSource`**: `article_id: int`, `title: str`, `link: str`, `relevance_score: Optional[float]`.

**`ReportFeedback`**: `section: str`, `rating: Optional[int]` (1–5), `comment: Optional[str]`.

**`ReportListItem`**: `id`, `report_date: date`, `report_type: str`, `status: str`, `title: Optional[str]`, `category: Optional[str]`, `executive_summary: str` (BLUF: first meaningful non-heading line from `final_content`/`draft_content` via `_extract_bluf()`, max 150 chars), `article_count: int`, `generated_at`, `reviewed_at`, `reviewer`.

**`ReportSection`**: `category: str`, `content: str`, `entities: list[str]`. (Defined but not populated — sections list is always empty in current router.)

**`ReportContent`**: `title: str`, `executive_summary: str` (first 500 chars of full text), `full_text: str`, `sections: list[ReportSection]`.

**`ReportMetadata`**: `processing_time_ms: Optional[int]`, `token_count: Optional[int]`.

**`ReportDetail`**: Composes `id`, `report_date`, `report_type`, `status`, `model_used`, `content: ReportContent`, `sources: list[ReportSource]`, `feedback: list[ReportFeedback]`, `metadata: ReportMetadata`.

**`ReportFilters`**: `status`, `report_type`, `date_from`, `date_to` — echoed back in list responses to confirm which filters were applied.

**Literal type aliases**: `ReportStatus = Literal["draft", "reviewed", "approved"]`, `ReportType = Literal["daily", "weekly"]`, `Category = Literal["GEOPOLITICS", "DEFENSE", "ECONOMY", "CYBER", "ENERGY", "OTHER"]`.

### schemas/stories.py

**`StorylineNode`**: `id: int`, `title: str`, `summary: Optional[str]`, `category: Optional[str]`, `narrative_status: str` (`emerging`/`active`/`stabilized`), `momentum_score: float`, `article_count: int`, `key_entities: list[str]`, `start_date: Optional[str]`, `last_update: Optional[str]`, `days_active: Optional[int]`, **`community_id: Optional[int]`** (Louvain community assignment).

**`StorylineEdge`**: `source: int`, `target: int`, `weight: float`, `relation_type: str` (default `"relates_to"`).

**`GraphStats`**: `total_nodes: int`, `total_edges: int`, `avg_momentum: float`, **`communities_count: int`** (number of distinct communities), **`avg_edges_per_node: float`** (graph density metric).

**`GraphNetwork`**: `nodes: list[StorylineNode]`, `links: list[StorylineEdge]`, `stats: GraphStats`.

**`CommunityInfo`** *(new)*: `community_id: int`, `size: int`, `label: Optional[str]`, `top_storylines: list[dict]`, `key_entities: list[str]`, `avg_momentum: float`.

**`RelatedStoryline`**: `id: int`, `title: str`, `weight: float`, `relation_type: str`.

**`LinkedArticle`**: `id: int`, `title: str`, `source: Optional[str]`, `published_date: Optional[str]`.

**`StorylineDetail`**: `storyline: StorylineNode`, `related_storylines: list[RelatedStoryline]`, `recent_articles: list[LinkedArticle]`.

## Dependencies / Configuration

**Python libraries:**
- `fastapi` — ASGI web framework
- `uvicorn` — ASGI server (`uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000`)
- `slowapi` — Rate limiting middleware built on the `limits` library; uses `get_remote_address` as the key function
- `pydantic` v2 — Schema validation and serialization
- `psycopg2` — PostgreSQL driver (accessed via `DatabaseManager` from `src.storage.database`)

**Environment variables:**
- `INTELLIGENCE_API_KEY` — Required in production. If unset in production, all auth-protected requests receive HTTP 503. If unset in development, auth is bypassed with a warning. Generate with: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
- `ENVIRONMENT` — Set to `"production"` to enforce strict auth behavior. Defaults to `"development"`.
- `ALLOWED_ORIGINS` — Comma-separated CORS origins. Defaults to `"http://localhost:3000,http://localhost:3001,http://localhost:3002"`. Must be set to the actual frontend domain in production.
- `DATABASE_URL` — Consumed by `DatabaseManager` (defined in `src/storage/database.py`).

**Rate limits (slowapi, keyed by remote IP address):**

| Endpoint | Limit |
|----------|-------|
| `GET /` | 10/minute |
| `GET /health` | 10/minute |
| `GET /api/v1/map/entities` | 30/minute |
| `GET /api/v1/map/entities/{entity_id}` | 60/minute |
| `POST /api/v1/oracle/chat` | 3/minute |
| All other router endpoints (dashboard, reports, stories) | No `@limiter.limit` decorator — not individually rate-limited |

## Data Flow

```
Client request
    │
    ├── CORSMiddleware (preflight check; allows GET and OPTIONS only)
    │
    ├── slowapi rate limiter (IP-based; active only on map endpoints in main.py)
    │
    ├── verify_api_key (FastAPI Depends — applied to all non-root/health endpoints)
    │       ├── Reads X-API-Key header via APIKeyHeader(auto_error=False)
    │       ├── INTELLIGENCE_API_KEY unset + dev mode → bypass, return "dev_mode"
    │       ├── INTELLIGENCE_API_KEY unset + production → HTTP 503
    │       ├── Header missing → HTTP 401
    │       └── secrets.compare_digest mismatch → HTTP 403
    │
    ├── Router handler
    │       ├── dashboard.py  → db.get_statistics() + private SQL helpers → DashboardStats
    │       ├── reports.py    → dynamic WHERE + LIMIT/OFFSET SQL → ReportListItem[] or ReportDetail
    │       ├── stories.py    → v_active_storylines + v_storyline_graph views → GraphNetwork,
    │       │                   or storylines table + storyline_edges + article_storylines → StorylineDetail
    │       └── map.py        → db.get_entities_for_map() / get_entity_arcs() / get_map_stats() → JSONResponse (GeoJSON)
    │
    └── JSON response
            ├── dashboard: APIResponse[DashboardStats] (response_model declared)
            ├── reports list: plain dict {"success", "data": {reports, pagination, filters_applied}, "generated_at"}
            ├── report detail: plain dict {"success", "data": ReportDetail.model_dump(), "generated_at"}
            ├── stories graph: plain dict {"success", "data": GraphNetwork.model_dump(), "generated_at"}
            ├── stories list: plain dict {"success", "data": {storylines, pagination}, "generated_at"}
            ├── stories detail: plain dict {"success", "data": StorylineDetail.model_dump(), "generated_at"}
            └── map entities: EntityCollection (GeoJSON FeatureCollection, response_model declared)
```

## Known Gotchas / Important Notes

**Authentication bypass in development:** When `INTELLIGENCE_API_KEY` is not set and `ENVIRONMENT` is not `"production"`, `verify_api_key` returns the string `"dev_mode"` without raising any exception. This is intentional for local development, but means any instance without the env var is fully open. Always configure the key before exposing the backend to a network.

**Timing-safe key comparison:** `auth.py` uses `secrets.compare_digest(api_key, INTELLIGENCE_API_KEY)` instead of `==`. This prevents timing attacks that could reveal key length or value through measurable response-time differences.

**CORS is GET/OPTIONS only:** `allow_methods=["GET", "OPTIONS"]` — the API is read-only by design. Browser-originated POST/PUT/DELETE requests will be blocked by CORS.

**Rate limiting scope:** `@limiter.limit(...)` is applied to the four endpoints defined directly in `main.py` and to `POST /api/v1/oracle/chat` (3/min). The dashboard, reports, and stories router endpoints have no per-endpoint rate limit decorator. The global `RateLimitExceeded` exception handler is registered on the app, so adding limits to routers requires only adding the decorator.

**DatabaseManager instantiated per request:** Each router handler calls `get_db()` which creates `DatabaseManager()` on every request. The class internally uses `psycopg2.pool.SimpleConnectionPool`, so connection pooling happens at that layer — not at the FastAPI dependency level.

**`response_model` inconsistency across routers:** The dashboard router declares `response_model=APIResponse[DashboardStats]` and the map endpoint declares `response_model=EntityCollection`. The reports and stories routers return raw dicts and declare no `response_model`, so FastAPI serializes them without Pydantic validation at the response layer.

**Source JSON shapes in reports:** The `sources` column in the `reports` table can be either a flat `list` of source dicts or a `dict` with `recent_articles` and `historical_context` keys, depending on which version of the report generator produced the row. The router in `reports.py` handles both cases defensively.

**`key_entities` JSON parsing in stories:** The `key_entities` field can arrive from the DB as a Python list (if psycopg2 auto-parses the JSONB column) or as a raw JSON string. The router calls `json.loads()` inside a `try/except (json.JSONDecodeError, TypeError)` and falls back to an empty list.

**`ReportContent.sections` is always empty:** The router sets `sections=[]` unconditionally. The raw report text is not parsed into sections at the API layer; consumers receive `full_text` and must parse it client-side if section-level access is needed.

**Title derivation for reports:** Both list and detail endpoints derive `title` with this priority: `metadata->>'title'` (JSONB field) → first non-empty line of content (stripped of Markdown `#` characters, truncated to 120 chars) → `"Report {report_date}"`. This handles both newer reports (which store a title in metadata) and legacy reports (which do not).

**`v_active_storylines` and `v_storyline_graph` as primary graph sources:** The graph endpoint queries these two DB views directly. The views include storylines with `narrative_status IN ('emerging', 'active', 'stabilized')` (migration 017 added `stabilized`). Changes to that logic must be made in the migrations, not in the router.

**Related storylines in detail endpoint traverse edges in both directions:** The SQL query in `get_storyline_detail` uses `WHERE (e.source_story_id = %s OR e.target_story_id = %s)` with a `CASE` to resolve the peer ID. This ensures bidirectional graph traversal without requiring edge duplication in the `storyline_edges` table.

**`date_from` / `date_to` query parameters are `date` type, not `datetime`:** FastAPI automatically parses ISO 8601 date strings (e.g., `2025-01-15`). Passing a full datetime string will cause a 422 Unprocessable Entity error.

**Error responses never leak exception details:** All `except` blocks log the full traceback internally but raise `HTTPException(status_code=500, detail="Internal server error")` to the client. The `str(e)` value is never included in the HTTP response body.
