## Why

`macro_indicators` is missing a `previous_value` column that the Strategic Intelligence Layer Phase 3 code has been reading since the merge. Every pipeline run since deployment fails silently on `_get_macro_indicators_for_screening()` → `indicators_delta` is empty → v2 analysis path is bypassed and v1 fallback runs instead. The column was designed into Phase 3 code but never added to the schema or to the insert logic.

## What Changes

- **New migration 036**: `ALTER TABLE macro_indicators ADD COLUMN IF NOT EXISTS previous_value NUMERIC(20,6)` + one-time backfill via correlated UPDATE
- **`_save_macro_indicator()` in `openbb_service.py`**: updated INSERT to populate `previous_value` inline via a subquery (no extra round-trip)
- **`migrations/context.md`**: updated to reflect migration 036

## Non-goals

- No changes to Phase 3 convergence matching or anomaly screening logic
- No changes to `market_tool.py` (already queries `previous_value` correctly)
- No changes to `_get_macro_indicators_for_screening()` in `report_generator.py`
- No API or frontend changes

## Capabilities

### New Capabilities

- `macro-indicator-delta`: `macro_indicators` stores the previous day's value alongside the current value, enabling delta calculation without a second query or CTE join at read time.

### Modified Capabilities

- `strategic-intelligence-layer`: Phase 3 screening (`indicators_delta`) now receives data — unblocks the v2 analysis path for the first time in production.

## Impact

- **Modules**: `migrations/` (new 036 file), `src/integrations/openbb_service.py` (`_save_macro_indicator`)
- **Context files**: `migrations/context.md`, `src/integrations/context.md`
- **SQL migration**: **yes** — migration 036, must be applied to production after deploy
- **Gemini model tier**: none
- **Strategic Intelligence Layer phase**: prerequisite fix for Phase 3 activation
