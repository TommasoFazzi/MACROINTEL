# Daily Pipeline Architecture

Orchestrated by `scripts/daily_pipeline.py`. Triggered daily at 08:00 UTC via GitHub Actions (`.github/workflows/pipeline.yml`).

## 10-Step Flow

```mermaid
flowchart TD
    START([GitHub Actions\n08:00 UTC daily]) --> S1

    S1["**Step 1: Ingestion**
    src/ingestion/pipeline.py
    Async RSS fetch (aiohttp TCPConnector limit=20)
    4-tier extraction: Trafilatura → Scrapling → StealthyFetcher → Newspaper3k
    2-phase dedup: hash(link+title) → content_hash DB check
    Keyword blocklist Filtro 1
    timeout: 3600s"]

    S1 --> S2

    S2["**Step 2: Market Data** ⚠️ continue_on_failure
    scripts/fetch_daily_market_data.py
    OpenBB v4 + yfinance: 36 MACRO_INDICATORS
    FRED series + CME futures (ALI=F, ZW=F)
    _fetch_indicator_openbb_fixed() → real data_date
    Saves: macro_indicators + macro_indicator_metadata
    timeout: 600s"]

    S2 --> S3

    S3["**Step 3: NLP Processing**
    scripts/process_nlp.py → src/nlp/processing.py
    spaCy NER (xx_ent_wiki_sm)
    Sentence embeddings 384-dim (paraphrase-multilingual-MiniLM-L12-v2)
    Semantic chunking (500 words)
    Filtro 2: LLM relevance (gemini-2.0-flash)
    Output: JSON with entities + chunks + embeddings"]

    S3 --> S4

    S4["**Step 4: Load to Database**
    scripts/load_to_database.py
    Bulk insert: articles + chunks + entities + embeddings
    PostgreSQL + pgvector (HNSW index)"]

    S4 --> S5

    S5["**Step 5: Narrative Processing** ⚠️ continue_on_failure
    scripts/process_narratives.py → src/nlp/narrative_processor.py
    6-stage pipeline (see 02_narrative_engine.md)
    timeout: 1800s"]

    S5 --> S6

    S6["**Step 6: Community Detection** ⚠️ continue_on_failure
    scripts/compute_communities.py
    Louvain algorithm (python-louvain + networkx)
    min_weight=0.05, resolution=0.2
    Gemini 2.0-flash → community_name
    timeout: 600s"]

    S6 --> S7

    S7["**Step 7: Entity Extraction** ⚠️ continue_on_failure
    scripts/extract_entities.py
    Geocodable entity candidates
    timeout: 600s"]

    S7 --> S8

    S8["**Step 8: Geocoding** ⚠️ continue_on_failure
    scripts/geocode_geonames.py
    4-step hybrid: GeoNames gazetteer → Gemini → Photon → PostGIS
    Requires geo_gazetteer table (migration 023 + load_geonames.py)
    timeout: 1200s"]

    S8 --> S9

    S9["**Step 9: Refresh Map Data** ⚠️ continue_on_failure
    scripts/refresh_map_data.py
    REFRESH MATERIALIZED VIEW entity_idf
    REFRESH MATERIALIZED VIEW mv_entity_storyline_bridge
    Recompute intelligence_score on entities
    Invalidate GeoJSON cache (POST /api/v1/map/cache/invalidate)
    timeout: 300s"]

    S9 --> S10

    S10["**Step 10: Generate Report**
    scripts/generate_report.py --macro-first
    2 LLM calls (see report generation flow below)
    timeout: 1800s"]

    S10 --> COND{Sunday?}
    COND -- Yes --> WEEKLY["Weekly Report
    generate_report.py --weekly"]
    COND -- No --> DONE
    WEEKLY --> MONTHLY{1st of month?}
    MONTHLY -- Yes --> RECAP["Monthly Recap
    generate_report.py --monthly"]
    MONTHLY -- No --> DONE
    RECAP --> DONE([Pipeline complete])
```

---

## Report Generation Flow (Step 10 Detail)

`src/llm/report_generator.py`

```mermaid
flowchart TD
    IN([macro_indicators + articles + storylines]) --> MC

    MC["**Macro Context Assembly**
    OntologyManager.build_jit_context() — top anomalies
    match_convergences() — active multi-indicator patterns
    build_sc_signals_context() — supply chain signals
    MacroRegimePersistence — 60-day regime history"]

    MC --> LLM1

    LLM1["**LLM Call #1: Macro Analysis**
    Model: gemini-2.5-flash (timeout: 60s)
    Input: macro snapshot + JIT asset theory + convergences + SC signals
    Output: MacroAnalysisResultV2 (Pydantic)
    → risk_regime label (7 values) + confidence
    → Persisted to macro_regime_history table"]

    LLM1 --> RAG

    RAG["**RAG Pipeline**
    Multi-query expansion (2-3 variants)
    Vector search HNSW (top-20 per query, chunks table)
    Cross-encoder reranking ms-marco-MiniLM-L-6-v2 → top-10
    ~15-20% precision improvement over pure vector search"]

    RAG --> NAR

    NAR["**Narrative Context**
    Fetch top-10 storylines by momentum from v_active_storylines
    Format as XML: Strategic Storyline Tracker
    Includes: title, summary, momentum, key_entities, connected storylines, linked articles"]

    NAR --> LLM2

    LLM2["**LLM Call #2: Full Report**
    Model: gemini-2.5-flash (timeout: 60s)
    Input: LLM Call #1 output + regime history XML + RAG chunks + narrative XML + articles
    Output: 7-section Strategic Intelligence Report
    Sections: Executive Summary, Key Developments, Macro Dashboard,
              Early Warning (1-4w), Strategic Positioning (1-6m),
              Scenario Analysis (3-12m), Supply Chain Monitor,
              Strategic Storyline Tracker"]

    LLM2 --> SIG

    SIG["**Trade Signal Extraction**
    Extract BULLISH/BEARISH/NEUTRAL/WATCHLIST signals
    Pydantic v2 validation
    Score = LLM confidence − SMA200 penalty + PE valuation
    Save to trade_signals table"]

    SIG --> OUT([Report saved to DB + reports/{timestamp}.md])
```

---

## Content Filtering (3 Layers)

```mermaid
flowchart LR
    ART[Raw Article] --> F1

    F1{"**Filtro 1**
    Keyword blocklist
    src/ingestion/pipeline.py"}
    F1 -- blocked --> BIN1[🗑 Discarded]
    F1 -- pass --> F2

    F2{"**Filtro 2**
    LLM relevance classification
    src/nlp/relevance_filter.py
    gemini-2.0-flash"}
    F2 -- NOT_RELEVANT --> BIN2[🗑 Discarded]
    F2 -- RELEVANT --> F4

    F4{"**Filtro 4**
    Post-clustering validation
    src/nlp/narrative_processor.py
    No scope keywords + off-topic regex"}
    F4 -- archived --> BIN4[📦 Archived storyline]
    F4 -- pass --> OK[✅ Processed article]
```
