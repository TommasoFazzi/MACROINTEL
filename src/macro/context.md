# src/macro — Strategic Intelligence Layer

## Purpose
New package introdotto nella Phase 3 del Strategic Intelligence Layer. Contiene i moduli di convergence detection, supply chain signal generation, e (nelle fasi successive) regime persistence e prompt architecture.

## Architecture Role
Strato intermedio tra `src/integrations/openbb_service.py` (fetch dei dati macro) e `src/llm/report_generator.py` (generazione report). Produce output strutturato deterministico prima della LLM call — convergenze attive, segnali SC pre-calcolati — che l'LLM poi valida e arricchisce.

## Key Files

### Phase 3 (completata)

- `match_convergences.py` — Pattern matching engine per convergenze multi-variato
  - `match_convergences(indicators_today, metadata, ontology_mgr)` — entry point
  - Confronta TUTTI gli indicatori (non solo top movers) contro `config/macro_convergences.yaml`
  - **Staleness weight logic**: indicatori con `staleness_days > max_stale * 3` vengono ignorati (weight=0.0); `max_stale < staleness <= 3x max_stale` → weight=0.5. Senza questo, NICKEL (67d stale) contribuisce a `china_stress_global_slowdown` con peso pieno.
  - Categorie hardcoded in `KEY_CATEGORY` (la YAML non ha il campo category)
  - `ConvergenceMatch` dataclass: `convergence_id`, `confidence`, `triggers_aligned`, `triggers_total`, `active` (True se confidence >= 0.55)
  - `TriggerResult` dataclass: trigger-level detail con `staleness_note` se penalizzato

- `build_sc_signals_context.py` — Generazione deterministica segnali Supply Chain
  - `build_sc_signals_context(indicators_today, indicator_materiality, indicator_values)` — entry point
  - YAML path fisso: `config/sc_sector_map.yaml` (non parametro)
  - `CONFIRMATION_ONLY_INDICATORS = {"CASS_FREIGHT_INDEX"}` — trattato separatamente come confirmation layer
  - `INDICATOR_FREQUENCY` — mappa frequenza per calcolo `is_monthly` (NICKEL, CASS, US_CPI, etc.)
  - `PRE_CONFIDENCE_MATRIX` e `PRE_CONFIDENCE_MATRIX_MONTHLY` — scala confidence un livello verso il basso per indicatori mensili
  - Corroboration boost: 2+ segnali medium → high, solo se almeno un indicatore è daily
  - Restituisce `(List[AggregatedSCSignal], str)` — segnali aggregati + prompt block XML `<sc_pre_signals>`

### Phase 4 (completata — shadow mode)

- `macro_regime_persistence.py` — Persistence layer per `macro_regime_history` + singleton
  - `MacroRegimePersistence(db)` — salva e legge storia regime 60gg
  - `save(analysis_date, analysis_json, freshness_gap_days)` — INSERT ON CONFLICT (idempotente)
  - `get_regime_context(target_date)` → `RegimeContext` — usato dal Narrative Engine
  - `get_regime_streak(as_of)` → `RegimeStreak` — streak giorni consecutivi stesso regime
  - `get_sc_signal_streaks(as_of, min_days=2)` → `List[SCSignalStreak]` — settori SC persistenti
  - `get_regime_history_summary(days=30)` → `List[dict]` — per Oracle e Strategic Layer
  - `get_scenario_context(as_of)` → `dict` — contesto strutturato per Scenario Analysis
  - `compute_regime_momentum_boost(topics, regime_context, sc_streaks)` → `float` — boost 1.0-1.3
  - `get_macro_regime_persistence_singleton()` — thread-safe singleton (double-checked locking)

- `macro_analysis_schema.py` — Prompt + schema per LLM call #1
  - `MACRO_ANALYSIS_SYSTEM_PROMPT` — regole di classificazione regime, convergenza, divergenze, SC signals. Include:
    - **COORDINATE READING RULES**: come interpretare P30 (≤20 oversold, ≥80 overbought), MA7d vs MA30d come trend direction, Δ7d/Δ30d come momentum, pct_change_12m come structural baseline, σ come volatility regime. POSITIONAL AMPLIFICATION: stesso Δ% con P30 diversi → interpretazioni diverse.
    - **COMPLEX SYSTEM RULES**: mercati sono sistemi complessi multi-direzionali. Ogni ipotesi causale deve usare probability language ("likely >60%", "probable >70%", "high confidence >80%", "uncertain"). Single-cause explanation vietata per movimenti notabili. Reverse causality e intertwined causality devono essere riconosciute esplicitamente.
  - **New output fields** (call #1 produce ora anche):
    - `asset_state_map`: lista di `AssetStateEntry` — full coordinate per indicator con materiality ≥ notable. `position_label` derivato da P30 (≤20 → "oversold", ≥80 → "overbought", altrimenti "neutral").
    - `causal_hypotheses`: lista di `CausalHypothesisEntry` — ≥2 ipotesi pesate per asset (PRIMARY/SECONDARY/STRUCTURAL). `osint_anchor` è un pointer forward-looking che call #2 risolve contro gli articoli OSINT.
    - `macro_state_narrative`: 150-200 parole, sintesi discorsiva ONTOLOGIA + TREND + EVENTI con probability language. Estende (non sostituisce) `macro_narrative` per maggiore profondità analitica.
  - **7 regime labels (Literal-constrained)**: `risk_off_systemic`, `risk_off_moderate`, `neutral`, `risk_on_moderate`, `risk_on_expansion`, `crisis_acute`, `stagflationary`
  - `CROSS_VALIDATION_BLOCK` — regole cross-validation macro-news per LLM call #2 (Phase 5)

- `strategic_intelligence_prompt.py` — Prompt assembler per LLM call #2 (Phase 5)
  - `STRATEGIC_INTELLIGENCE_SYSTEM_PROMPT` — system prompt per report strategico 3 orizzonti. Include:
    - **PRE-ANALYSIS PROTOCOL**: prima di scrivere qualsiasi sezione, call #2 deve sintetizzare 3 layer internamente: ONTOLOGY (da `causal_hypotheses`), TREND (da `asset_state_map` position_label + direction), EVENTS (articoli OSINT che confermano/negano gli `osint_anchor`). Ogni movimento discusso deve essere fondato su ≥2 layer. Per Scenario Analysis: ogni scenario deve referenziare almeno un elemento da ciascun layer.
    - **ANALYTICAL STANDARDS** (invariati): central bank constraint (a) orthodox reaction function vs (b) political pressure.
  - `CROSS_VALIDATION_BLOCK` — cross-validation rules. Regola 7 aggiunta: **COORDINATE-HYPOTHESIS CONSISTENCY** — ogni market move significativo deve referenziare l'`asset_state_map` (position_label + trend direction). Un claim che ignora il contesto posizionale richiede esplicito acknowledgment del layer mancante.
  - `build_output_instructions(target_date)` — 7 sezioni output (invariato).
  - `build_strategic_intelligence_prompt(macro_analysis_json, macro_regime_context_xml, storylines_xml, articles, target_date, data_quality_flags, macro_context_raw="")` → `(system_prompt, user_prompt)`. Nuovo parametro `macro_context_raw`: quando non vuoto, aggiunge sezione "RAW INDICATOR COORDINATES" come reference block per call #2. Ordine user_sections aggiornato: [1] data quality → [2] asset state map → [3] causal hypotheses → [4] raw coordinates → [5] regime history → [6] macro analysis → [7] storylines → [8] OSINT → [9] cross-validation → [10] output instructions.
  - `_build_asset_state_section(asset_state_map)` — helper: tabella compatta per notable asset (ASSET | position_label | direction | Δ7d | Δ30d | Δ12m | P30 | σ).
  - `_build_causal_hypotheses_section(causal_hypotheses)` — helper: blocchi PRIMARY/SECONDARY/STRUCTURAL con weight, mechanism, trend_context, osint_anchor.

### Phase 6 (futura)
- `ew_tracker.py` — Early Warning signal tracking + accuracy feedback loop

## Config Files
- `config/macro_convergences.yaml` — 8 pattern di convergenza (risk_off_systemic, industrial_cycle_expansion, banking_liquidity_stress, real_rate_shock, recession_signal_leading, recession_in_progress, inflationary_spiral, china_stress_global_slowdown, carry_trade_unwind_jpy)
- `config/sc_sector_map.yaml` — mappatura indicatori → settori supply chain con meccanismi causali, lag, confidence, monitor_sources

## Integration Points
- **Input da**: `src/llm/report_generator.py._get_macro_metadata()` (metadata staleness), `src/integrations/openbb_service.py` (indicator data)
- **Output a**: `report_generator.py._generate_macro_analysis()` (Phase 3 data via `_phase3` key); `_generate_macro_analysis_v2()` (Phase 4, LLM call #1); `_generate_strategic_report()` (Phase 5, LLM call #2)
- **Phase 5**: `build_strategic_intelligence_prompt()` from `strategic_intelligence_prompt.py` assembles the user prompt for LLM call #2. `MacroRegimePersistence.get_regime_history_summary(60d)` provides regime context XML. Both called inside `_generate_strategic_report()`.
- **Dipende da**: `config/macro_convergences.yaml`, `config/sc_sector_map.yaml` (entrambi devono esistere)

## Critical Pitfalls
- `match_convergences.py` usa `KEY_CATEGORY` hardcoded, non l'OntologyManager — la YAML non ha il campo `category`
- Staleness weight a `0.0` fa sì che il trigger conti comunque nel **denominatore** (total_weight), abbassando la confidence — comportamento corretto: un trigger non disponibile non deve far sembrare la convergenza più forte
- `build_sc_signals_context` logga un warning se il YAML non è trovato ma non crasha (returns `[]`, `""`)
- NICKEL ha `staleness_days` tipicamente 45-70gg — con `max_stale=45` per `monthly`, viene penalizzato (weight=0.5) ma non ignorato; oltre i 135gg viene ignorato completamente
