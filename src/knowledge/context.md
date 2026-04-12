# src/knowledge — Ontological Knowledge Layer

## Purpose
Carica e gestisce il knowledge base teorico sugli indicatori macro. Usato per iniettare contesto "Just-In-Time" nelle LLM call, limitando il token budget ai soli indicatori anomali del giorno.

## Key Files

- `ontology_manager.py` — Singleton loader per `config/asset_theory_library.yaml`
  - **Singleton**: `OntologyManager()` restituisce sempre la stessa istanza; il YAML viene letto una sola volta al boot
  - Al boot carica anche `config/macro_convergences.yaml` e `config/sc_sector_map.yaml` per accesso condiviso senza re-read
  - `screen_anomalies(indicators, prev_indicators, top_n=6, metadata=None)` — anomaly screener data-driven
    - Scoring: `anomaly_score = abs(delta_pct) / _MATERIALITY_SIGNIFICANT[category]` (normalizzato per categoria — evita che un +1% oil oscuri un +3pt VIX)
    - **USD_CNH escluso** dai top movers (`_EXCLUDED_FROM_TOP_MOVERS`): reliability='restricted', fixing PBoC distorce il segnale
    - Default `top_n=6` (era 4 in Phase 1-2)
    - Restituisce lista con `anomaly_score` aggiunto ai dicts
  - `build_jit_context(top_mover_keys)` — assembla contesto teorico per i top movers
  - `get_ontology(key)`, `get_correlations(key)`, `get_spread_signal(key)` — accessors per l'asset library
  - `convergences` property — dict convergenze da `macro_convergences.yaml`
  - `sc_map` property — dict SC sector map da `sc_sector_map.yaml`

## Dependencies
- `config/asset_theory_library.yaml` — 35 indicator ontologies con correlazioni causali
- `config/macro_convergences.yaml` — 8 pattern di convergenza (caricato al boot)
- `config/sc_sector_map.yaml` — SC sector map (caricato al boot)

## Integration Points
- **Chiamato da**: `src/llm/report_generator.py._generate_macro_analysis()` (JIT pipeline)
- **Usato da**: Oracle 2.0 per `build_full_context_for_keys()` nelle query di tipo MARKET/ANALYTICAL
