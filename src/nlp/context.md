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

- `narrative_processor.py` - **Narrative Engine** (~1798 lines)
  - `NarrativeProcessor` class - Full storyline lifecycle
  - **Key tunable thresholds** — externalized to `config/narrative_clustering.yaml` (Phase 1A); loaded in `__init__` via `src/nlp/config.py :: load_clustering_config()` and exposed as instance attributes for backward compat. Current Phase 1A values preserve previous code behavior; Phase 2B sweep / Phase 1E Leiden / Phase 2D HDBSCAN adaptive will retune them.
    - `self.MICRO_CLUSTER_THRESHOLD = 0.90` ← `micro_cluster.threshold`
    - `self.MATCH_THRESHOLD = 0.75` ← `matching.threshold` (the old Decision 14 target 0.65 is superseded: `storyline-formation-causal-review` measured near-threshold joins at 0.33 precision)
    - `self.TIME_DECAY_FACTOR = 0.05` ← `matching.time_decay_factor`
    - `self.ENTITY_BOOST = 0.10` ← `matching.entity_boost`
    - `self.ENTITY_JACCARD_THRESHOLD = 0.05` ← `matching.entity_jaccard_threshold` (Decision 14 target: 0.15)
    - `self.HDBSCAN_MIN_CLUSTER_SIZE = 2`, `self.HDBSCAN_MIN_SAMPLES = 2` ← `hdbscan.*` (Decision 14 base: 3; Phase 2D adds adaptive sqrt + EWMA)
    - `self.DRIFT_WEIGHT_OLD = 0.85`, `self.DRIFT_WEIGHT_NEW = 0.15` ← `evolution.*`
    - `self.LINK_COUNT_FIX = False` ← `formation_levers.link_count_fix` (lever L0, accepted 2026-10-19): the loader skips orphan-pool articles and `_assign_event_to_storyline` adds the links actually inserted to `article_count`. The only `formation_levers` key prod reads; switch on at least 7 days after the level-2 promotion
    - `self.MOMENTUM_DECAY_FACTOR = 0.7` — hardcoded constant; Phase 2F replaces with EWMA + burst (`momentum.*`)
    - `self.LLM_RATE_LIMIT_SECONDS = 0.1` — hardcoded constant (Tier 4 fixed operational cap)
  - `src/nlp/config.py` (new): Pydantic models (`NarrativeClusteringConfig` + sub-models with `extra='forbid'`) + cached `load_clustering_config(path=None, force_reload=False)` + `reset_cache()` for tests.
    - `FormationLevers` (`formation_levers` section, `storyline-formation-causal-review` D6): level-1 lever flags L0–L8, every default = today's behaviour. Read only by the offline replay; prod wires a lever in only once it is accepted. L7 uses `matching.threshold` and L1's slope `matching.time_decay_factor`, so they have no key here.
    - `ThemeClusteringConfig` (`theme_clustering` section, `narrative-clustering-embedding-based`): `k_current` (default 12 since 2026-10-16, chosen by the labelled offline validation study `clustering-offline-validation-study`; was 18 from the silhouette diagnostic), `k_sweep_range`, `warm_start`, `outlier_threshold`, `refit_cadence_days`, `hdbscan_shadow: HDBSCANShadowConfig` (`enabled`, `min_cluster_size`, `min_samples` — distinct from `hdbscan.min_cluster_size_base` used by storyline micro-clustering). Consumed by `scripts/theme_clustering.py`, not by `narrative_processor.py`. `HDBSCANShadowConfig.umap: UMAPConfig` (`clustering-shadow-metrics-umap`, 2026-07-19): `enabled` (default `false`), `n_components` (10), `n_neighbors` (15), `min_dist` (0.0), `random_state` (42) — optional UMAP pre-reduction before HDBSCAN in the `hdbscan_shadow` permanent-challenger path only, never the k-means champion or live Louvain path.
  - `src/nlp/clustering_validation.py` (`clustering-offline-validation-study`, 2026-10-15): pure functions for the offline k-means vs HDBSCAN+UMAP study, no DB access. Stability with seed vs data variance (`pairwise_ari_on_intersection`; `pairwise_ari_hdbscan` scores ARI only on ids non-noise in both runs and reports noise Jaccard, coverage and p90 noise ratio). `dbcv(X, labels)`: DBCV in NumPy/SciPy with log-space core distances, matching `hdbscan.validity.validity_index` to ~1e-14 on the reference datasets in the tests; descriptive only. Pair metrics against the hand-labelled reference set (`LabelledPairs`, `predict_same` with noise/missing = `different`, `point_metrics`, macro F1 over S-A/S-B/S-C only), stratified bootstrap with resamples shared across configurations so differences are paired (`bootstrap_indices`, `bootstrap_f1`, `ci95`), `rater_agreement` (Cohen's κ + raw agreement), the blind pair sampler (`sample_pairs`: S-C balanced by disagreement direction, S-N, S-A, S-B, no pair in two strata; `labelling_order`: repeats in the second half at least 60 rows after their first occurrence, left/right randomised; `load_labelled_pairs`), and the pre-registered D7 rule `decide(candidates)` / `apply_eligibility` (noise gate before the relative stability bar, `ARI_FLOOR`, paired tie test, k=18 incumbent, verdict table). The D7 constants are frozen at module level and copied into the report. Also holds the report's Pydantic models (`SnapshotManifest`, `ConfigResult`, `StudyReport`, …). Consumers: `scripts/export_clustering_snapshot.py`, `scripts/clustering_validation_study.py`.
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
    - `_apply_decay()` — 5 rules applied each run, in this order:
      1. momentum *= 0.7 for emerging/active not updated in 7 days
      2. active + momentum < 0.3 → stabilized
      3. stabilized + no update for 30 days → archived
      4. emerging + article_count < 3 + older than **5 days** → archived
      5. emerging + no update for **`decay.emerging_ttl_days`** (default 30) → archived, **regardless of `article_count`**
      Returns a per-rule counter dict, persisted verbatim into `narrative_run_metrics.decay_stats`.
      Rule 5 runs *after* rule 4 by design: rule 4 flips small stale rows out of `emerging`
      first, so rule 5's counter measures exactly the leak described below and nothing else.
  - **Entity Sanitization (added for data quality):**
    - `_is_garbage_entity(entity: str)` — Static method: returns `True` for entities that are URLs, emails, pure numbers, single chars, obviously malformed fragments, metadata-like strings (e.g. `"font-size"`, `"ANSA"` prefixed garbage). Called during both storyline creation and LLM entity extraction.
    - `_clean_entity(entity: str)` — Static method: normalizes entity text by stripping quotes, brackets, trailing punctuation, excess whitespace. Called before `_is_garbage_entity` during storyline creation.
    - `_sanitize_entities_batch(entities: list[str])` — Applies `_clean_entity` + `_is_garbage_entity` to a list, returns cleaned survivors.
  - **Helper:**
    - `_extract_entity_list(entities_json)` — Handles both new format (`clean.all`) and old format (`by_type.GPE/ORG/PERSON`); applies clean → garbage filter → **media_artifacts blocklist filter** → dedup, returns top 15 entities
  - **Entity blocklist filter** (Fix `fix-clustering-singleton-bias`):
    - `_load_entity_blocklist(path=None)` — Module-level helper: loads the `media_artifacts` section of `config/entity_blocklist.yaml` (Reuters, TASS, AP, Bloomberg, Xinhua, AFP, ANSA, Kyodo, EFE, dpa, etc.) as a lower-cased `set[str]` for O(1) membership lookup. Other sections (`generic_terms`, `noise`, `numbers_years`) are intentionally NOT loaded — they may contain legitimate-actor names in geopolitical context (e.g. "President", "China"). On missing/malformed YAML, logs a warning and returns `set()` (engine continues without source filtering).
    - `_ENTITY_BLOCKLIST: set[str]` — Module-level constant populated at import time. Consulted in `_extract_entity_list` to reject media/agency entities from `key_entities` before they reach the storyline graph (where they would inflate Jaccard edge weights via shared-source bias).
  - **Module-level constants:** `_SCOPE_KEYWORDS` (compiled regex with geopolitical terms), `_OFF_TOPIC_PATTERNS` (list of compiled regexes for sports/entertainment/celebrity/food/tourism), `_ENTITY_BLOCKLIST` (set, see above)

- `storyline_decisions.py` - **Pure level-1 decision functions** (`storyline-formation-causal-review` D2, 2026-10-16). No DB, no clock: `now` is passed in. `NarrativeProcessor` keeps the DB orchestration and calls these, so prod and the offline chronological replay share one implementation: `create_micro_clusters` / `micro_cluster_indices` / `article_to_event` / `articles_to_event` (greedy near-duplicate grouping, order-dependent), `score_candidates(event, storylines, now, MatchParams) -> [CandidateScore]` (cosine − linear time penalty + entity boost, with every component) and `select_match` (best ≥ threshold, first wins ties), `residual_labels` (HDBSCAN on unmatched events), `drift_embedding`, `merge_entities` (cap 20; which entities survive depends on set order — prod behaviour, relevant to H4), `momentum_bump`, `promote_status` (emerging → active at 3 articles), and the relevance predicate `is_off_topic` with `SCOPE_KEYWORDS` / `OFF_TOPIC_PATTERNS` (moved here from `narrative_processor.py`). Decay and orphan expiry stay SQL, now as module constants in `narrative_processor.py` (`DECAY_RULE_1_SQL` … `DECAY_RULE_5_SQL`, `ORPHAN_EXPIRY_SQL`) so the replay's Python predicates can be pinned to them. Extraction was behaviour-neutral: `tests/test_nlp/test_storyline_decisions_refactor.py` compares each function with a verbatim copy of the pre-extraction method on synthetic inputs with a frozen clock. Changing a decision is a flag-gated lever in that change, never an edit here. `time_penalty(days, params)` holds lever L1 (`MatchParams.time_penalty_mode` linear | capped | exponential, `time_penalty_cap`); the defaults are prod's linear penalty, and `NarrativeProcessor` does not pass the new fields.
- `storyline_replay.py` - **Offline chronological replay of level 1** (`storyline-formation-causal-review` D3/D4). In-memory state (storylines, links, orphan pool) re-running `process_daily_batch`'s formation steps at prod's recorded run times from a cold start, on a `scripts/export_formation_snapshot.py` export, through the same `storyline_decisions` functions prod calls. Reproduces prod quirks on purpose: the loader predicate (embedding, `published_date` within 1 day, not linked; orphan-pool articles are reloaded), the early return on a run with no articles (no orphan retry, no decay), stale candidate lists during orphan retry and matching, `article_count` adding event size (L0 bug), HDBSCAN's no-single-cluster default. Decay and orphan expiry are Python predicates whose constants are pinned to `narrative_processor.DECAY_RULE_*_SQL` / `ORPHAN_EXPIRY_SQL` by a test. Skips LLM summaries, NIS and graph edges; the relevance filter is a hook: `prod_off_topic_hook` (fidelity run: prod's off-topic archivals, inferred from final title+summary and mapped by majority article overlap) or `title_only_off_topic` (lever runs). Implemented levers (`IMPLEMENTED_LEVERS`, others raise `NotImplementedError`): L0 `link_count_fix` (loader skips orphan-pool articles, `article_count` adds inserted links), L1 `time_penalty`/`time_penalty_cap`, L2 `match_stabilized` (stabilized storylines are candidates; a join reactivates them) and L8 (`small_storyline_ttl_from` = created | last_join for rule_4, `archived_grace_days`: rule_4/rule_5 archivals stay candidates and a join revives them). `DecisionRecord.target_status` records the joined storyline's status before the join (J-H2/J-H8 strata). Emits a `DecisionRecord` per decision (joined / new_storyline / orphan / orphan_recovered / orphan_expired / never_considered) with the best emerging/active candidate, the same without time penalty, and the best candidate of any status, for the attribution waterfall. `fidelity_report()` gives co-membership ARI, coverage and new storylines/day vs prod after a burn-in (default 30 days). Non-default `FormationLevers` are refused until implemented (tasks.md 7.x). Differences from prod: one clock per run (prod's `NOW()` per statement), ties broken by id, PYTHONHASHSEED pinned by the CLI because `merge_entities` depends on set order.
- `storyline_attribution.py` - **Causal attribution waterfall for the level-1 replay** (`storyline-formation-causal-review` D4). `attribute(result, threshold)` gives every fragmentation event (new storyline or orphan) and lost article (never considered, expired orphan) the first cause in the fixed order never_considered (H7) → archived_candidate (H8: best any-status candidate archived by rule_4/rule_5/off-topic clears the threshold) → stabilized_candidate (H2) → time_penalty (H1: clears without the penalty) → boost_lost (H4: clears with the storyline's creation entities) → orphan_twin (H3: a later orphan at cosine ≥ 0.75 before this one ends) → residual, plus non-exclusive flags. Expired orphans inherit the cause of the record that pooled them. `waterfall(result)` tallies events and articles by cause after the burn-in. Says what would have joined, not whether the join is right (labelled sets, D5).
- `formation_labels.py` - **Blind labelled sets for the level-1 review and their estimator** (`storyline-formation-causal-review` D5, D7 amended 2026-10-16). Pools from attributed replays (both bracket ends): J ("does this article belong to this storyline?") strata J-base (baseline replay joins), J-H1 / J-H8 / J-H2 (what removing the time penalty would add: best active / H8-archived / stabilized candidate clearing without it), J-near (best active in [0.65, 0.75)); S ("same story?") strata S-new (replay storyline vs its best existing candidate, cosine ≥ 0.60) and S-prod (prod storylines whose article centroids are at cosine ≥ 0.80). `sample_strata` draws per stratum without duplicates; ordering and repeats reuse `clustering_validation.labelling_order`. `stratified_precision` weighs each stratum's labelled precision by a configuration's own decision counts, never a plain average; `paired_delta` is the stratified paired bootstrap, with the amended J bound −0.05. `stratum_labels` turns judge labels into per-stratum 0/1 arrays (drops `unsure`), `wilson_ci` gives per-stratum intervals, `auc` is the Mann–Whitney AUC used for H6. D7 lever evaluation: `join_counts` (joins to stabilized storylines as J-H2, to revived archived ones as J-H8, others as J-base if they clear under the baseline's linear penalty, else J-H1), `coverage`, `close_duplicate_share` (S-new pool / new storylines), `lever_result` (coverage gain, duplicate change, paired J-precision delta per label source, accepted flag). Pool items also carry article ids (`recent_ids`, `left_ids`/`right_ids`, up to `RICH_ARTICLES` = 8) for the rich-context judge round, plus full membership (`member_ids`, `left_members`/`right_members`) for the representation track. C (cohesion) waits for round 2.
- `formation_representation.py` - **Candidate level-1 formation vectors** (`storyline-representation-track` D1–D5). `sentence_windows` packs cleaned text into consecutive-sentence windows within a token budget (R2/R3). `with_formation_vectors` substitutes a candidate vector for each `ReplayArticle.embedding`, the whole replay hook: every level-1 decision reads only that field, and level 2 is not replayed. `build_from_means` / `load_vectors` handle `formation_<name>.npz` files aligned to a snapshot. `auc_gate` (D3) compares the candidate's and the baseline's cosine AUC on the J/S gold, using the plain mean of each storyline's members, with a within-class paired bootstrap; it passes at S Δ ≥ +0.05 with a 95% lower bound > 0 and J Δ ≥ −0.02. `calibration_pairs` + `calibrate` (D4) quantile-match the micro (0.90) and matching (0.75) thresholds on the same pairs in both spaces, with a ×0.75 / ×1.25 share sensitivity. `co_assignment_result` (D5) is the duplicate measure for this track: the share of S gold pairs' cross article pairs that end in one storyline, with a +10% relative benefit on `same` pairs and a ≤ 5 pp guard on `different` pairs. `join_split` / `representation_result` (D8) split a candidate replay's joins into those the baseline also made (≥ half the target's prior articles in common) and new ones (stratum `J-new-R`, labelled separately), and give coverage, co-assignment and the stratified precision verdict (`pending_labels` until J-new-R is labelled). Outcome 2026-10-23: R1, R2, R3 and R2 + L1 all rejected (`openspec/changes/storyline-representation-track/decision-memo.md`); nothing in prod reads formation vectors.
- `relevance_filter.py` - **LLM Relevance Classification** (Filtro 2) (~160 lines)
  - `RelevanceFilter(scope: str = "global")` — uses `LLMFactory.get("t5")` (Gemini 2.5 Flash-Lite, timeout=15s)
  - `scope` parameter: `"global"` (geopolitics/defense/finance — default) or `"ceseo"` (Romania economic/business — activated with `--scope ceseo` in `process_nlp.py`)
  - `classify_article(article)` — Returns `True` (relevant) or `False` (not relevant); parses `{"relevant": bool}` JSON response; on LLM error defaults to `True` (conservative)
  - `filter_batch(articles)` — Classifies a batch, returns `(relevant_articles, filtered_out_articles)` tuple; tags articles with `relevance_label` field; rate-limited at 0.15s between calls
  - JSON mode enabled — eliminates fragile NOT_RELEVANT text parsing; timeout=15s prevents 900s hangs
  - `max_tokens=50` (bumped from 20 on 2026-07-15 — the old budget occasionally truncated the JSON response mid-string, raising a `json.loads` error caught by the same defaulting `except`)
  - `CLASSIFICATION_PROMPT` — Global Italian-language system prompt (geopolitics scope)
  - `_CESEO_SCOPE_PROMPT` — CESEO Romania scope prompt: classifies economic/business/energy/banking/infrastructure RO content as relevant; sport/lifestyle → not_relevant
  - `_SCOPE_PROMPTS` — dict mapping scope name → prompt template
  - `SCOPE_DESCRIPTION` / `OUT_OF_SCOPE` — Platform scope boundaries (global)
  - Conservative: borderline cases → RELEVANT (prefer false positives over missing intelligence)
  - **invoke**: `python scripts/process_nlp.py --scope ceseo` for Romania vertical; default unchanged
  - **Fixed 2026-07-08:** both prompt templates embedded a literal `{"relevant": true}` JSON example that broke `str.format()` (unescaped single braces treated as field refs) — raised `KeyError` on every call since the filter's introduction (2026-02-17), silently caught by `scripts/process_nlp.py`'s outer `try/except` and defaulting to "proceed with all articles" every run. Now double-braced (`{{"relevant": true}}`). See CLAUDE.md LLM Integration pitfalls.

**Note:** `story_manager.py` (legacy narrative engine) has been **deleted**. `narrative_processor.py` is the sole storyline engine.

## 3-Layer Content Filtering

| Layer | File | Stage | Method |
|-------|------|-------|--------|
| Filtro 1 | `src/ingestion/pipeline.py` | Ingestion | Keyword blocklist (sports/entertainment/food) |
| Filtro 2 | `relevance_filter.py` | NLP processing | LLM classification (T5, Gemini 2.5 Flash-Lite) |
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
- **`emerging` was a dead-end state until `rule_5` (fix `fix-emerging-storyline-lifecycle-leak`)**: a storyline is created `emerging` with `article_count` = HDBSCAN cluster size, frequently >= 3 (`min_cluster_size_base=2`, and each event carries >= 1 article). The only two exits were promotion to `active` — which fires exclusively inside `_assign_event_to_storyline`, i.e. only when a **new** article is assigned — and `rule_4`, which requires `article_count < 3`. One born with >= 3 articles that never received another satisfied neither: `rule_1` decayed its momentum indefinitely, but `rule_2` acts only on `active` and `rule_3` only on `stabilized`. Production accumulated **1828 such rows** (oldest `last_update` 2026-02-16, avg momentum 0.041) out of 2226 `emerging`, while `active` and `stabilized` had **zero** rows older than 30 days — the rest of the state machine was healthy. The blast radius was downstream: the graph endpoint reached 2402 nodes / 41344 edges / 5.82 MB, exceeding the landing page's fetch budget and Next.js's 2 MB data-cache limit, so the public landing silently served static fallbacks for weeks. **When touching lifecycle rules, verify every non-terminal state has a time-based exit that does not depend on new data arriving.**
- **Do not add an `article_count` predicate to `rule_5`**: `migrations/016_graph_cleanup.sql` step 1 attempted this same cleanup but copied `rule_4`'s `article_count < 3` verbatim, so it only selected rows `rule_4` already handled and never touched a single stuck row — the bug survived the cleanup meant to fix it.
- **DB-level candidate pre-filtering**: Graph builder uses `EXISTS (SELECT 1 FROM jsonb_array_elements_text(key_entities) ...)` to reduce candidates from thousands to ~10-50 per storyline, eliminating the previous O(n²) timeout issue.
- **`entity_blocklist.yaml` is loaded ONLY by narrative_processor `_load_entity_blocklist()` (added by `fix-clustering-singleton-bias`)**: before this fix the YAML was only consulted by `scripts/backfill_entities.py`, `clean_entities.py`, `deep_clean_entities.py` on the `entities` table — narrative_processor wrote unfiltered `key_entities` (with Reuters/TASS/AP/Bloomberg/Xinhua/AFP) into `storylines`, polluting Jaccard edge weights downstream. The hook is module-level (one load at import); pipeline must be restarted to pick up YAML changes.
