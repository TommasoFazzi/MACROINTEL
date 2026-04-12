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

### Fasi Future (Phase 4+)
- `macro_regime_persistence.py` — `MacroRegimePersistence` singleton + `get_macro_regime_persistence_singleton()`
- `macro_analysis_schema.py` — `MacroAnalysisResultV2` Pydantic schema + `MACRO_ANALYSIS_SYSTEM_PROMPT`
- `strategic_intelligence_prompt.py` — `build_strategic_intelligence_prompt()` per LLM call #2
- `ew_tracker.py` — Phase 6: Early Warning signal tracking + accuracy feedback loop

## Config Files
- `config/macro_convergences.yaml` — 8 pattern di convergenza (risk_off_systemic, industrial_cycle_expansion, banking_liquidity_stress, real_rate_shock, recession_signal_leading, recession_in_progress, inflationary_spiral, china_stress_global_slowdown, carry_trade_unwind_jpy)
- `config/sc_sector_map.yaml` — mappatura indicatori → settori supply chain con meccanismi causali, lag, confidence, monitor_sources

## Integration Points
- **Input da**: `src/llm/report_generator.py._get_macro_metadata()` (metadata staleness), `src/integrations/openbb_service.py` (indicator data)
- **Output a**: `report_generator.py._generate_macro_analysis()` (Phase 3: log-only; Phase 4+: injected into prompt)
- **Dipende da**: `config/macro_convergences.yaml`, `config/sc_sector_map.yaml` (entrambi devono esistere)

## Critical Pitfalls
- `match_convergences.py` usa `KEY_CATEGORY` hardcoded, non l'OntologyManager — la YAML non ha il campo `category`
- Staleness weight a `0.0` fa sì che il trigger conti comunque nel **denominatore** (total_weight), abbassando la confidence — comportamento corretto: un trigger non disponibile non deve far sembrare la convergenza più forte
- `build_sc_signals_context` logga un warning se il YAML non è trovato ma non crasha (returns `[]`, `""`)
- NICKEL ha `staleness_days` tipicamente 45-70gg — con `max_stale=45` per `monthly`, viene penalizzato (weight=0.5) ma non ignorato; oltre i 135gg viene ignorato completamente
