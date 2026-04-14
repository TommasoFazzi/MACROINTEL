# INTELLIGENCE_ITA — Architecture Overview

## C4 Level 1: System Context

```mermaid
C4Context
    title INTELLIGENCE_ITA — System Context

    Person(analyst, "Intelligence Analyst", "Reviews daily reports, queries Oracle AI, monitors geospatial map, tracks narrative storylines")

    System(platform, "INTELLIGENCE_ITA", "End-to-end geopolitical intelligence platform: ingest → analyze → report → visualize")

    System_Ext(rss, "33+ RSS Feeds", "Italian & English news sources (breaking_news, intelligence, tech_economy, security)")
    System_Ext(gemini, "Google Gemini API", "gemini-2.0-flash (NLP layer) / gemini-2.5-flash (LLM/report layer)")
    System_Ext(openbb, "OpenBB / yfinance / FRED", "36 macro indicators: equities, FX, commodities, rates, credit")
    System_Ext(osanctions, "OpenSanctions / UCDP / IMF / World Bank", "Structured intelligence data sources")
    System_Ext(geonames, "GeoNames / Photon", "Geocoding gazetteer (geo_gazetteer table, ~2-3M rows)")

    Rel(analyst, platform, "HTTPS — Dashboard / Oracle / Map / Stories")
    Rel(rss, platform, "Async ingestion pipeline (aiohttp)")
    Rel(platform, gemini, "REST (transport='rest') — NLP + report generation")
    Rel(platform, openbb, "Daily market data fetch")
    Rel(osanctions, platform, "Batch structured data load (scripts)")
    Rel(platform, geonames, "Entity geocoding")
```

---

## C4 Level 2: Container Diagram

```mermaid
C4Container
    title INTELLIGENCE_ITA — Container Diagram

    Person(analyst, "Intelligence Analyst")

    Container(nginx, "Nginx", "Reverse proxy", "SSL termination, routes /api/* → backend, /* → frontend")
    Container(frontend, "Next.js 16", "TypeScript 5 / React 19 / Tailwind CSS 4", "6 routes: Landing, Access, Insights, Dashboard, Map, Stories, Oracle")
    Container(backend, "FastAPI", "Python 3.12 / uvicorn", "8 REST routers + pipeline orchestration + Oracle 2.0 agentic engine")
    ContainerDb(db, "PostgreSQL 17 + pgvector + PostGIS", "psycopg2 connection pool", "Articles, storylines, reports, macro indicators, entities, sanctions")
    Container(pipeline, "Daily Pipeline", "Python scripts (GitHub Actions 08:00 UTC)", "10-step orchestrator: ingest → NLP → narratives → report")

    Rel(analyst, nginx, "HTTPS :443")
    Rel(nginx, frontend, "Port 3000")
    Rel(nginx, backend, "Port 8000 (/api/v1/*)")
    Rel(frontend, backend, "REST + X-API-Key header (via /api/proxy/* server-side)")
    Rel(backend, db, "psycopg2 + pgvector (HNSW index)")
    Rel(pipeline, db, "Bulk insert articles, storylines, reports, macro data")
    Rel(pipeline, backend, "POST /api/v1/map/cache/invalidate (post-pipeline)")
```

---

## Technology Stack Summary

| Layer | Technology | Version |
|-------|-----------|---------|
| **Frontend framework** | Next.js App Router | 16 |
| **Frontend UI** | React + Tailwind CSS + Shadcn/ui | 19 / 4 |
| **Frontend data fetching** | SWR | — |
| **Map visualization** | Mapbox GL | — |
| **Graph visualization** | react-force-graph-2d (Canvas 2D) | — |
| **Backend framework** | FastAPI + uvicorn | 0.128 / 0.40 |
| **Backend language** | Python | 3.12 |
| **NLP** | spaCy (xx_ent_wiki_sm) + sentence-transformers | 3.8 / 5.1 |
| **Embeddings model** | paraphrase-multilingual-MiniLM-L12-v2 | 384-dim |
| **Clustering** | scikit-learn HDBSCAN | — |
| **LLM** | Google Gemini (2.0-flash / 2.5-flash) | — |
| **Market data** | OpenBB v4 + yfinance | 4.6.0 |
| **Database** | PostgreSQL + pgvector + PostGIS | 17 / 0.4 |
| **Infrastructure** | Docker Compose on Hetzner CAX31 (ARM64) | — |
| **CI/CD** | GitHub Actions | — |
| **Monitoring** | Grafana + Loki + Promtail | — |
