# API Endpoint Map

FastAPI backend — `src/api/main.py` + `src/api/routers/`

Base URL: `https://api.macrointel.net` (prod) / `http://localhost:8000` (dev)

## Endpoint Overview

```mermaid
flowchart LR
    subgraph AUTH["Authentication"]
        AK["X-API-Key header
        secrets.compare_digest()
        Dev bypass if ENVIRONMENT != production"]
    end

    subgraph dashboard_r["/api/v1/dashboard"]
        D1["GET /stats
        Articles, entities, reports, quality KPIs
        Aggregated dashboard metrics"]
    end

    subgraph reports_r["/api/v1/reports"]
        R1["GET /
        Paginated list
        filters: status, type, date_from, date_to"]
        R2["GET /{id}
        Full content + sources (with bullet_points[])
        + feedback + metadata"]
        R3["GET /compare?ids=A,B
        Gemini 2.5-flash delta analysis
        → new_developments, resolved_topics, trend_shifts, persistent_themes"]
        R4["POST /feedback
        Submit human HITL rating (1-5) + comment"]
    end

    subgraph stories_r["/api/v1/stories"]
        S1["GET /graph
        Full narrative graph (nodes + links + stats)
        Includes: emerging + active + stabilized
        Cache: 1h per (min_weight, min_momentum)"]
        S2["GET /communities
        Louvain community listing
        id, size, community_name, top_storylines, key_entities"]
        S3["GET /
        Paginated storyline list
        default: emerging + active"]
        S4["GET /{id}
        Detail + related storylines + recent articles"]
        S5["GET /{id}/network
        Ego network: node + neighbors + connecting edges"]
        S6["GET /tickers
        Ticker mention counts across storylines"]
        S7["GET /ticker/{ticker}
        Themes + sentiment for specific ticker"]
    end

    subgraph map_r["/api/v1/map"]
        M1["GET /entities
        GeoJSON FeatureCollection (gzipped)
        filters: type, days, min_mentions, min_score, search
        Rate: 30/min | Cache: 5min TTL"]
        M2["GET /entities/{id}
        Entity detail + related articles + storylines
        Rate: 60/min"]
        M3["GET /arcs
        GeoJSON LineStrings (entity co-occurrence)
        Cache: 5min TTL"]
        M4["GET /stats
        Live HUD: entity counts, geocoded%, active storylines"]
        M5["POST /cache/invalidate
        Force GeoJSON cache invalidation
        Called by refresh_map_data.py post-pipeline"]
    end

    subgraph oracle_r["/api/v1/oracle"]
        O1["POST /chat
        Oracle 2.0 agentic query
        body: {query, session_id, filters, gemini_api_key?}
        Rate: 3/min | Timeout: 120s (proxy)"]
        O2["GET /health
        Oracle service health (no auth)"]
    end

    subgraph public_r["/api/v1/ — public (no auth)"]
        P1["GET /insights
        Public intelligence briefings list"]
        P2["GET /insights/{slug}
        Public briefing detail"]
        P3["POST /waitlist
        Register email for early access"]
        P4["GET /waitlist
        Waitlist stats (admin)"]
    end

    subgraph ingest_r["/api/v1/ingest"]
        I1["POST /pdf
        Manual PDF upload + ingestion trigger"]
    end

    AUTH --> dashboard_r & reports_r & stories_r & map_r & oracle_r & ingest_r
```

---

## Rate Limits

| Endpoint | Limit | Key |
|----------|-------|-----|
| `POST /api/v1/oracle/chat` | **3 / min** | IP |
| `GET /api/v1/map/entities` | 30 / min | IP |
| `GET /api/v1/map/entities/{id}` | 60 / min | IP |
| `GET /` (root) | 10 / min | IP |
| `GET /health` | 10 / min | IP |
| All other endpoints | Unlimited | — |

---

## Response Shape Conventions

### Success
```json
{
  "success": true,
  "data": { ... },
  "generated_at": "2026-04-14T10:00:00Z"
}
```

### Error (FastAPI default)
```json
{
  "detail": "error message"
}
```

### Oracle Response
```json
{
  "answer": "...",
  "sources": [{"id": 1, "title": "...", "url": "...", "relevance": 0.87, "key_points": [...]}],
  "query_plan": {"intent": "ANALYTICAL", "complexity": "medium", "tools": [...]},
  "execution_steps": [{"tool": "RAGTool", "status": "success", "duration_ms": 340}],
  "session_id": "...",
  "follow_up": false
}
```

---

## Middleware Stack

```mermaid
flowchart TD
    REQ[Incoming Request] --> CORS[CORS Middleware\nGET+POST allowed\nALLOWED_ORIGINS env var]
    CORS --> GZIP[GZipMiddleware\nmin_size=500 bytes]
    GZIP --> RATE[slowapi RateLimitMiddleware]
    RATE --> AUTH[verify_api_key dependency\nsecrets.compare_digest]
    AUTH --> ROUTER[FastAPI Router]
    ROUTER --> RESP[Response]
```

---

## Key Pydantic Schemas (`src/api/schemas/`)

| Schema | Fields | Used By |
|--------|--------|---------|
| `OracleRequest` | query, session_id, filters (OracleActiveFilters), gemini_api_key | POST /oracle/chat |
| `OracleActiveFilters` | mode, search_type, date_from, date_to, gpe_filter | Oracle request |
| `OracleResponse` | answer, sources, query_plan, execution_steps, session_id | POST /oracle/chat |
| `GraphNetwork` | nodes (StorylineNode[]), links (StorylineEdge[]), stats | GET /stories/graph |
| `ReportDetail` | id, date, type, content, sources (ReportSource[]), metadata | GET /reports/{id} |
| `ReportSource` | id, title, url, relevance_score, bullet_points[] | Report detail |
| `ReportComparisonResponse` | new_developments[], resolved_topics[], trend_shifts[], persistent_themes[] | GET /reports/compare |
| `MapEntitiesResponse` | type=FeatureCollection, features (GeoJSON Feature[]) | GET /map/entities |
| `DashboardStats` | article_count, entity_count, report_count, storyline_count, quality_metrics | GET /dashboard/stats |
