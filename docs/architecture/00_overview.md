# INTELLIGENCE_ITA — Architecture Overview

## C4 Level 1: System Context

```mermaid
C4Context
    title INTELLIGENCE_ITA — System Context

    Person(analyst, "Intelligence Analyst", "Reviews daily reports, queries Oracle AI, monitors geospatial map, tracks narrative storylines")

    System(platform, "INTELLIGENCE_ITA", "End-to-end geopolitical intelligence platform: ingest → analyze → report → visualize")

    System_Ext(rss, "56 RSS Feeds", "Italian & English news sources (breaking_news, intelligence, tech_economy, security)")
    System_Ext(llm, "LLM Providers (5-tier factory)", "T1 Gemini 3.1 Pro (reports) / T2 Claude Sonnet 4.6 (Oracle) / T3 DeepSeek V3.2 (extraction) / T4b Mistral Codestral (SQL) / T4a+T5 Gemini 2.5 Flash-Lite (NLP bulk)")
    System_Ext(openbb, "OpenBB / yfinance / FRED / Trading Economics / tvDatafeed", "34 macro indicators: equities, FX, commodities, rates, credit, Romania")
    System_Ext(osanctions, "OpenSanctions / UCDP / IMF WEO / World Bank", "Structured intelligence data sources (knowledge base)")
    System_Ext(geonames, "GeoNames / Photon", "Geocoding gazetteer (geo_gazetteer table, ~2-3M rows)")

    Rel(analyst, platform, "HTTPS — Dashboard / Oracle / Map / Stories")
    Rel(rss, platform, "Async ingestion pipeline (aiohttp)")
    Rel(platform, llm, "LLMFactory.get(tier) — per-tier model, timeout, provider")
    Rel(platform, openbb, "Daily market data fetch (evening workflow, post-NYSE close)")
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
    Container(frontend, "Next.js 16", "TypeScript 5 / React 19 / Tailwind CSS 4", "Public routes: Landing, Insights, Dashboard, Map, Stories, Oracle")
    Container(backend, "FastAPI", "Python 3.12 / uvicorn", "9 REST routers + Oracle 2.0 agentic engine (Claude Sonnet 4.6)")
    ContainerDb(db, "PostgreSQL 17 + pgvector + PostGIS", "psycopg2 connection pool", "Articles, storylines, reports, macro indicators + regimes, entities, sanctions, narrative themes")
    Container(pipeline, "Daily Pipeline", "Python scripts (GitHub Actions 08:00 UTC)", "12-step orchestrator: ingest → NLP → narratives → clustering (live+shadow) → geocoding → reports → email")

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
| **Backend framework** | FastAPI + uvicorn | 0.128 |
| **Backend language** | Python | 3.12 |
| **NLP** | spaCy (xx_ent_wiki_sm) + sentence-transformers | 3.8 / 5.6 |
| **Embeddings model** | paraphrase-multilingual-MiniLM-L12-v2 | 384-dim |
| **Clustering** | scikit-learn HDBSCAN + k-means (warm-start), scipy Hungarian matching | — |
| **Community detection** | python-louvain + networkx (live), leidenalg + igraph (shadow) | — |
| **LLM routing** | 5-tier LLMFactory: Gemini 3.1 Pro / Claude Sonnet 4.6 / DeepSeek V3.2 / Mistral Codestral / Gemini 2.5 Flash-Lite | — |
| **Market data** | OpenBB v4 + yfinance + FRED + Trading Economics + tvDatafeed | 4.6.0 |
| **Database** | PostgreSQL + pgvector + PostGIS | 17 |
| **Infrastructure** | Docker Compose on Hetzner CAX31 (ARM64) | — |
| **CI/CD** | GitHub Actions | — |
| **Monitoring** | Grafana + Loki + Promtail | — |
