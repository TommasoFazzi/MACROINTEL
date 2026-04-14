# Strategic Intelligence Layer — Implementation Spec

Branch: `feature/strategic-intelligence-layer`

## Objective

Upgrade the report generator from a single-horizon monolithic LLM call to a 3-horizon strategic intelligence system with:
- **Early Warning (EW):** 1–4 week outlook
- **Strategic Positioning:** 1–6 month outlook  
- **Scenario Analysis:** 3–12 month outlook

Plus: 60-day macro regime memory, convergence detection, Supply Chain (SC) signals, self-improving EW feedback loop.

## Phase Status

| Phase | Name | Status | Completed |
|-------|------|--------|-----------|
| 1 | Data Quality Foundation | ✅ COMPLETE | 2026-04-10 |
| 2 | Freshness Layer + Smart Delta | ✅ COMPLETE | 2026-04-10 |
| 3 | Convergence Detection + SC Signals | ✅ COMPLETE | 2026-04-14 |
| 4 | LLM Call #1 + Regime Persistence | ✅ COMPLETE | 2026-04-14 |
| 5 | Report Cutover + Narrative Engine + Oracle | ✅ COMPLETE | 2026-04-14 |
| 6 | EW Signal Tracking + Post-Mortem Loop | ❌ NOT STARTED | — |

## New Package: `src/macro/`

| Module | Purpose |
|--------|---------|
| `match_convergences.py` | Scores all macro indicators against `config/macro_convergences.yaml` with staleness-aware weighting |
| `build_sc_signals_context.py` | Deterministic SC signals from `config/sc_sector_map.yaml` |
| `macro_regime_persistence.py` | `MacroRegimePersistence` singleton — 60-day regime snapshots |
| `macro_analysis_schema.py` | Pydantic v2 models for structured macro analysis output |
| `strategic_intelligence_prompt.py` | Prompt templates for 3-horizon report structure |
| `ew_tracker.py` | EW signal tracking + post-mortem accuracy feedback (Phase 6) |

## Key Architectural Decisions

### Two-Call LLM Architecture
1. **LLM Call #1** (`_generate_macro_analysis()`): macro regime + convergence context → structured `MacroAnalysis` Pydantic model
2. **LLM Call #2** (`_generate_full_report()`): full 3-horizon report using Call #1 output + RAG + narrative context

### Regime Persistence
- `MacroRegimePersistence` is a standalone singleton (NOT in `OntologyManager`)
- Stores regime snapshots in `macro_regime_history` table (migration 035)
- Topic matching uses embedding cosine similarity (≥ 0.6), NOT substring matching
- Pre-cache SC sector embeddings in `MacroRegimePersistence` at init

### Convergence Staleness Weighting
In `match_convergences.py`:
- `staleness > max_stale * 3` → weight = 0.0 (ignored, but kept in denominator for honest confidence)
- `staleness > max_stale` → weight = 0.5
- NICKEL (67d stale, monthly) → excluded from `china_stress_global_slowdown` convergence via staleness rule

### EW Signal Output Format
HTML comment for deterministic parsing in post-mortem:
```
<!-- EW_SIGNAL trigger: <name> horizon_days: <N> sectors: <comma-separated> -->
```

### Shadow Mode (Phase 3-4)
Convergence and SC signal results are logged but NOT injected into LLM prompt until Phase 5 go/no-go:
- Validation failure rate < 5%
- Regime consistency ≥ 80% across 5+ pipeline runs
- SC signals non-empty

## Go/No-Go Criteria (Phase 5 Cutover)

Before injecting new context into the report prompt:
1. Run `_generate_macro_analysis()` on 5 consecutive pipeline runs
2. Check validation failure rate < 5% (Pydantic errors / total calls)
3. Check regime labels consistent ≥ 80% (no wild swings without real data change)
4. Verify SC signals non-empty on at least 3 of 5 runs

## Migration 035

Required before any Phase 1+ pipeline run.

```sql
-- macro_indicator_metadata: real data dates per indicator
-- macro_regime_history: 60-day regime snapshots
```

Apply via:
```bash
docker compose -p app exec postgres psql -U intelligence_user -d intelligence_ita \
  -f /opt/intelligence-ita/repo/migrations/035_macro_metadata_regime.sql
```

## Removed Indicators (Phase 1)

These are **permanently removed** — do not re-add without updating all 3 locations:
- `TED_SPREAD` — removed from `MACRO_INDICATORS`, `asset_theory_library.yaml`, cross-reference maps
- `EPU_GLOBAL` — same
- `USD_RUB` — same

## Source Changes (Phase 1)

| Indicator | Old Source | New Source |
|-----------|------------|------------|
| ALUMINUM | FRED monthly | CME futures `ALI=F` via yfinance (daily) |
| WHEAT | FRED monthly | CME futures `ZW=F` via yfinance (daily) |
| USD_GBP | FRED daily | yfinance |
| USD_CNY | FRED daily | yfinance |

## Phase 6 — EW Tracker (Not Started)

`src/macro/ew_tracker.py` will:
1. Parse `<!-- EW_SIGNAL ... -->` comments from generated reports
2. After `horizon_days`, compare predicted vs actual market moves
3. Compute accuracy per trigger type and sector
4. Feed accuracy weights back into `match_convergences.py` confidence scoring
