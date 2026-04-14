# Pipeline Architecture

## Daily Pipeline — 10 Core Steps

Orchestrated by `scripts/daily_pipeline.py`. Steps execute in this exact order:

| # | Step Key | Script/Module | continue_on_failure | timeout |
|---|----------|---------------|---------------------|---------|
| 1 | `ingestion` | `src.ingestion.pipeline` | false | 3600s |
| 2 | `market_data` | `scripts/fetch_daily_market_data.py` | **true** | 600s |
| 3 | `nlp_processing` | `scripts/process_nlp.py` | false | 3600s |
| 4 | `load_to_database` | `scripts/load_to_database.py` | false | 1800s |
| 5 | `narrative_processing` | `scripts/process_narratives.py` | **true** | 1800s |
| 6 | `community_detection` | `scripts/compute_communities.py` | **true** | 600s |
| 7 | `entity_extraction` | `scripts/extract_entities.py` | **true** | 600s |
| 8 | `geocoding` | `scripts/geocode_geonames.py` | **true** | 1200s |
| 9 | `refresh_map_data` | `scripts/refresh_map_data.py` | **true** | 300s |
| 10 | `generate_report` | `scripts/generate_report.py` | false | 1800s |

**Conditional weekly (Sunday):** `weekly_report` via `scripts/generate_report.py --weekly`
**Conditional monthly (1st of month):** `monthly_recap`

## Data Flow

```
RSS Feeds (33) → [1] Ingestion
                     ↓
              [2] Market Data (OpenBB/FRED/yfinance) ──→ macro_indicators table
                     ↓
              [3] NLP Processing (spaCy NER + embeddings)
                     ↓
              [4] Load to Database (PostgreSQL + pgvector)
                     ↓
              [5] Narrative Processing (HDBSCAN + LLM evolution + Jaccard graph)
                     ↓
              [6] Community Detection (Louvain + Gemini community names)
                     ↓
              [7] Entity Extraction (geocodable entities)
                     ↓
              [8] Geocoding (GeoNames + Gemini + Photon hybrid)
                     ↓
              [9] Refresh Map Data (GeoJSON cache + intelligence scores)
                     ↓
              [10] Generate Report (RAG + LLM → Strategic Intelligence Report)
                      ↓
               reports table → FastAPI → Next.js Frontend
```

## Narrative Engine (Step 5) — Internal Flow

`src/nlp/narrative_processor.py`:
1. Fetch recent unprocessed articles
2. HDBSCAN micro-clustering on article embeddings
3. Match clusters to existing storylines via embedding similarity
4. LLM summary evolution (`_evolve_narrative_summary()`) — gemini-2.0-flash
5. Compute TF-IDF Jaccard weights for storyline graph edges (threshold 0.05)
6. Update momentum scores with exponential decay
7. Post-clustering validation (Filtro 4 — archives off-topic storylines)

## 3-Layer Content Filtering

| Layer | Where | Method |
|-------|-------|--------|
| Filtro 1 | `src/ingestion/pipeline.py` | Keyword blocklist (sports/entertainment/food) |
| Filtro 2 | `src/nlp/relevance_filter.py` | LLM classification (RELEVANT/NOT_RELEVANT) — gemini-2.0-flash |
| Filtro 4 | `src/nlp/narrative_processor.py` | Post-clustering: archives storylines with no scope keywords + off-topic patterns |

## Report Generation (Step 10) — Internal Flow

`scripts/generate_report.py` → `src/llm/report_generator.py`:
1. `_generate_macro_analysis()` — LLM Call #1 (macro regime + convergence context)
2. RAG retrieval: vector search top-20 → cross-encoder reranking → top-10
3. Strategic Storyline Tracker: XML-structured narrative context from `v_active_storylines`
4. `_generate_full_report()` — LLM Call #2 (full report with 3-horizon structure)
5. Trade signal extraction: macro-first → Pydantic validation

## Running Individual Steps

```bash
# From INTELLIGENCE_ITA/ directory
python -m src.ingestion.pipeline              # Step 1
python scripts/fetch_daily_market_data.py     # Step 2
python scripts/process_nlp.py                 # Step 3
python scripts/load_to_database.py            # Step 4
python scripts/process_narratives.py          # Step 5
python scripts/compute_communities.py         # Step 6
python scripts/geocode_geonames.py            # Step 8
python scripts/refresh_map_data.py            # Step 9
python scripts/generate_report.py             # Step 10

# Full pipeline
python scripts/daily_pipeline.py

# Report with options
python scripts/generate_report.py --days 3 --macro-first --skip-article-signals
```
