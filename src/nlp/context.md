# NLP Context

## Purpose
Natural Language Processing module that transforms raw article text into structured, searchable data for RAG (Retrieval-Augmented Generation). Handles text cleaning, chunking, entity extraction, embedding generation, **LLM relevance classification**, and **narrative storyline processing**.

## Architecture Role
Processing layer between ingestion and storage. Takes JSON output from `src/ingestion/`, applies NLP pipeline, and produces enriched articles ready for vector database storage. The **Narrative Engine** (`narrative_processor.py`) clusters related articles into ongoing storylines, evolves summaries via LLM, maintains a graph of inter-storyline relationships (TF-IDF weighted Jaccard), and enforces content relevance via post-clustering validation.

## Key Files

- `processing.py` - Core NLP pipeline (~603 lines)
  - `NLPProcessor` class - Hybrid NLP processor
  - Text Processing: `clean_text()`, `create_chunks(is_long_document=False)`, `preprocess_text()`
  - **Section-aware chunking**: When `is_long_document=True` and text contains `## ` Markdown headings (from pymupdf4llm), splits on `\n## ` then applies sliding window within each section; each chunk carries `section_title` metadata
  - `_create_section_chunks(text, chunk_size, chunk_overlap)` — Splits Markdown on `#{1,2}` headings, preserves section title per chunk
  - `_create_sliding_window_chunks(text, chunk_size, chunk_overlap)` — Standard sliding window (original `create_chunks` logic)
  - Entity Extraction: `extract_entities()` - spaCy NER (GPE, ORG, PERSON, LOC)
  - Embeddings: `generate_embedding()`, `generate_chunk_embeddings()` - 384-dim (`paraphrase-multilingual-MiniLM-L12-v2`)
  - Batch Processing: `process_article()`, `process_batch()`

- `narrative_processor.py` - **Narrative Engine** (~1498 lines)
  - `NarrativeProcessor` class - Full storyline lifecycle
  - **Key tunable constants:**
    - `MICRO_CLUSTER_THRESHOLD = 0.90` — cosine sim threshold for near-duplicate grouping
    - `MATCH_THRESHOLD = 0.75` — min hybrid score to match an event to an existing storyline
    - `TIME_DECAY_FACTOR = 0.05` — score penalty per day of storyline inactivity
    - `ENTITY_BOOST = 0.10` — bonus when entity Jaccard >= 0.3
    - `ENTITY_JACCARD_THRESHOLD = 0.05` — min TF-IDF weighted Jaccard for graph edges (0.30 fallback without IDF)
    - `HDBSCAN_MIN_CLUSTER_SIZE = 2`, `HDBSCAN_MIN_SAMPLES = 2`
    - `DRIFT_WEIGHT_OLD = 0.85`, `DRIFT_WEIGHT_NEW = 0.15` — embedding drift weights
    - `MOMENTUM_DECAY_FACTOR = 0.7` — weekly decay multiplier for inactive storylines
    - `LLM_RATE_LIMIT_SECONDS = 0.1` — pause between Gemini calls (2.0-flash has high quota)
  - **Public interface:**
    - `process_daily_batch(days, dry_run)` — Main orchestrator: micro-clustering → matching → HDBSCAN discovery → LLM evolution → **post-clustering validation** → graph → decay
  - **Stage 1 — Micro-clustering:**
    - `_create_micro_clusters(articles)` — Groups near-duplicate articles (cosine sim > 0.90) into unique events using greedy clustering; returns list of event dicts with centroid embedding, merged entities, article_ids
    - `_article_to_event(article)` — Converts a single article to an event dict
    - `_articles_to_event(articles)` — Merges multiple articles into a single event (centroid embedding, union of entities, representative title from article with most entities)
  - **Stage 2 — Adaptive matching:**
    - `_load_active_storylines()` — Loads storylines with `narrative_status IN ('emerging', 'active')` ordered by momentum_score DESC
    - `_find_best_match(event, active_storylines)` — Hybrid score = `cosine_sim - time_decay_penalty + entity_boost`; returns best match above MATCH_THRESHOLD
    - `_assign_event_to_storyline(event, storyline_id)` — Links articles, applies embedding drift (85%/15%), merges entities (cap 20), bumps momentum, promotes emerging → active at article_count >= 3
  - **Stage 3 — HDBSCAN discovery:**
    - `_cluster_residuals(orphaned_events)` — Applies HDBSCAN (metric='euclidean' on unit vectors) to orphaned events; noise points become individual storylines; returns list of created storyline IDs
    - `_create_storyline_from_events(events)` — Creates a new storyline record from one or more events, initial `narrative_status='emerging'`, `momentum_score=0.5`
  - **Stage 3.5 — Orphan buffer retry:**
    - `_retry_orphan_pool()` — Attempts to re-match events stored in the `orphan_events` buffer pool against currently active storylines before HDBSCAN clustering; events that find a match are removed from the pool; events older than 14 days are expired; reduces noise in HDBSCAN by recovering events that were too sparse on their original run
  - **Stage 4 — LLM summary evolution:**
    - `_evolve_narrative_summary(storyline_id)` — Calls `GeminiClient("gemini-2.5-flash", timeout=30).generate_content_raw()` (Italian prompt). **EXCEPTION**: stays on 2.5 Flash (not Flash-Lite) — structured narrative generation feeds HDBSCAN clustering; Flash-Lite quality eval pending. new storylines get title+summary from scratch, existing ones integrate new facts; also encodes `summary_vector` via sentence-transformers; max_output_tokens=400, temperature=0.3
    - LLM prompt includes `ENTITÀ:` field — Gemini extracts structured entities from article text, which are then filtered through `_is_garbage_entity()` before storage
  - **Stage 4b — Post-clustering validation (Filtro 4):**
    - `_validate_storyline_relevance(storyline_ids)` — Archives storylines with no scope keywords AND matching off-topic patterns; runs after LLM evolution (so title+summary are available); sets `narrative_status='archived'`, `status='ARCHIVED'`
  - **Stage 5 — Graph builder (TF-IDF weighted Jaccard):**
    - `_load_entity_idf(cur)` — Loads IDF weights from `entity_idf` materialized view; returns `{entity: idf_score}` or empty dict if view unavailable
    - `_update_graph_connections(storyline_id, idf_weights)` — Computes TF-IDF weighted Jaccard similarity with all other `emerging`, `active`, **and `stabilized`** storylines; rare entities (high IDF) contribute more to edge weight; pre-filters candidates at DB level using `EXISTS` on shared entities (reduces O(n²) to ~10-50 candidates); UPSERT edges above threshold (0.05 with TF-IDF, 0.30 fallback without); avoids bidirectional duplicates (keeps higher-weight direction); also updates `last_graph_update`
  - **Stage 6 — Decay:**
    - `_apply_decay()` — 4 rules applied each run:
      1. momentum *= 0.7 for emerging/active not updated in 7 days
      2. active + momentum < 0.3 → stabilized
      3. stabilized + no update for 30 days → archived
      4. emerging + article_count < 3 + older than **5 days** → archived
  - **Entity Sanitization (added for data quality):**
    - `_is_garbage_entity(entity: str)` — Static method: returns `True` for entities that are URLs, emails, pure numbers, single chars, obviously malformed fragments, metadata-like strings (e.g. `"font-size"`, `"ANSA"` prefixed garbage). Called during both storyline creation and LLM entity extraction.
    - `_clean_entity(entity: str)` — Static method: normalizes entity text by stripping quotes, brackets, trailing punctuation, excess whitespace. Called before `_is_garbage_entity` during storyline creation.
    - `_sanitize_entities_batch(entities: list[str])` — Applies `_clean_entity` + `_is_garbage_entity` to a list, returns cleaned survivors.
  - **Helper:**
    - `_extract_entity_list(entities_json)` — Handles both new format (`clean.all`) and old format (`by_type.GPE/ORG/PERSON`)
  - **Module-level constants:** `_SCOPE_KEYWORDS` (compiled regex with geopolitical terms), `_OFF_TOPIC_PATTERNS` (list of compiled regexes for sports/entertainment/celebrity/food/tourism)

- `relevance_filter.py` - **LLM Relevance Classification** (Filtro 2) (~141 lines)
  - `RelevanceFilter` class — uses `LLMFactory.get("t5")` (Gemini 2.5 Flash-Lite, timeout=15s)
  - `classify_article(article)` — Returns `True` (relevant) or `False` (not relevant); parses `{"relevant": bool}` JSON response; on LLM error defaults to `True` (conservative)
  - `filter_batch(articles)` — Classifies a batch, returns `(relevant_articles, filtered_out_articles)` tuple; tags articles with `relevance_label` field; rate-limited at 0.15s between calls
  - JSON mode enabled — eliminates fragile NOT_RELEVANT text parsing; timeout=15s prevents 900s hangs
  - `CLASSIFICATION_PROMPT` — Italian-language system prompt with scope definition
  - `SCOPE_DESCRIPTION` / `OUT_OF_SCOPE` — Platform scope boundaries
  - Conservative: borderline cases → RELEVANT (prefer false positives over missing intelligence)

**Note:** `story_manager.py` (legacy narrative engine) has been **deleted**. `narrative_processor.py` is the sole storyline engine.

## 3-Layer Content Filtering

| Layer | File | Stage | Method |
|-------|------|-------|--------|
| Filtro 1 | `src/ingestion/pipeline.py` | Ingestion | Keyword blocklist (sports/entertainment/food) |
| Filtro 2 | `relevance_filter.py` | NLP processing | LLM classification (Gemini 2.0 Flash) |
| Filtro 4 | `narrative_processor.py` | Post-clustering | Regex scope keywords + off-topic patterns → archive |

**Filtro 4 logic (two conditions must both be true to archive):**
1. Title+summary contains NO match in `_SCOPE_KEYWORDS` (geopolitical terms, key countries, agencies, etc.)
2. Title+summary matches at least one `_OFF_TOPIC_PATTERNS` (sports leagues, entertainment awards, celebrity, food/travel)

If a storyline has no summary yet (LLM not yet run), it passes validation and is checked again on the next run.

## Dependencies

- **Internal**: `src/storage/database`, `src/utils/logger`
- **External**:
  - `spacy` + `xx_ent_wiki_sm` - NER, tokenization
  - `sentence-transformers` - Embeddings (384-dim) and `summary_vector` encoding
  - `sklearn.cluster.HDBSCAN` (scikit-learn >= 1.3) - Density-based clustering; gracefully degrades if unavailable
  - `numpy`, `scikit-learn` - Vector operations
  - `google-generativeai` — Gemini 2.5 Flash (narrative_processor exception, direct GeminiClient) + Flash-Lite via LLMFactory T5 (relevance_filter, bullet_generator)

## Data Flow

- **Input**: JSON articles from `data/articles_{timestamp}.json`
- **Output**:
  - Enriched articles with `nlp_data` (chunks, entities, embeddings)
  - Storylines in `storylines` table with evolved summaries and momentum
  - Graph edges in `storyline_edges` table (TF-IDF weighted Jaccard)
  - Article-storyline links in `article_storylines` junction table

## Known Gotchas

- **HDBSCAN import**: Uses `sklearn.cluster.HDBSCAN` (added in scikit-learn 1.3), not the standalone `hdbscan` package. Falls back to individual storyline creation if unavailable.
- **Embedding model is lazy-loaded**: `NarrativeProcessor.embedding_model` property loads SentenceTransformer on first access. This avoids slow startup when LLM-only features are used.
- **`skip_llm` flag**: If `NarrativeProcessor(skip_llm=True)`, LLM summary evolution is disabled but all other stages run. Filtro 4 still runs (without summaries, new storylines with no scope keywords in the title but also no off-topic match will pass).
- **Momentum bump per article**: `min(1.0, 0.1 * len(event['article_ids']))` — capped at 1.0 total.
- **Entities capped at 20**: `_assign_event_to_storyline` merges entities with a hard cap at 20 items; `_create_storyline_from_events` uses top 15 by frequency.
- **`summary_vector` vs `current_embedding`**: `current_embedding` drifts with new article embeddings (semantic drift tracking); `summary_vector` is re-encoded from the LLM-generated summary text each evolution cycle.
- **TF-IDF graph fallback**: When `entity_idf` materialized view doesn't exist (migration 015 not applied), `_update_graph_connections` falls back to plain Jaccard with the safe legacy threshold of 0.30 to avoid edge explosion.
- **Graph candidate query includes `stabilized`**: The SQL in `_update_graph_connections` queries `narrative_status IN ('emerging', 'active', 'stabilized')` — this ensures stabilized storylines remain connected in the graph even after they stop receiving new articles. Earlier versions only queried `emerging` and `active`, causing sparse graphs.
- **IDF weights are critical for rebuild scripts**: `scripts/rebuild_graph_edges.py` must load IDF weights via `processor._load_entity_idf(cur)` and pass them to `_update_graph_connections(sid, idf_weights)`. Without IDF weights, the fallback threshold is 0.30 (vs 0.05 with TF-IDF), resulting in ~90% fewer edges.
- **Bidirectional edge deduplication**: Graph builder checks for reverse edges and keeps only the direction with higher weight. Migration 016 also cleans pre-existing bidirectional duplicates.
- **DB-level candidate pre-filtering**: Graph builder uses `EXISTS (SELECT 1 FROM jsonb_array_elements_text(key_entities) ...)` to reduce candidates from thousands to ~10-50 per storyline, eliminating the previous O(n²) timeout issue.
