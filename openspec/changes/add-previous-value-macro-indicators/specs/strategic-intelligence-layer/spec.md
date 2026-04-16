## MODIFIED Requirements

### Requirement: Phase 3 indicator delta calculation receives data
`_get_macro_indicators_for_screening()` SHALL return a non-empty list of indicator dicts
(each with `indicator_key`, `value`, `previous_value`, `category`) when at least one macro
indicator row exists for `target_date` and the `previous_value` column is populated.
The `indicators_delta` dict in Phase 3 data MUST be non-empty for the v2 analysis path to activate.

**Pre-change behavior**: `_get_macro_indicators_for_screening()` raised a DB error
(`column "previous_value" does not exist`) on every call → `indicators_delta` was always empty
→ `use_strategic_v2` was always `False`.

**Post-change behavior**: query succeeds, returns rows with numeric `previous_value` where
available, `None` where not. Phase 3 delta computation proceeds normally; v2 path activates
when `indicators_delta` is non-empty.

#### Scenario: v2 path activates after migration
- **WHEN** migration 036 has been applied AND `macro_indicators` has rows for `target_date` with non-NULL `previous_value`
- **THEN** `indicators_delta` is non-empty AND `use_strategic_v2 = True` AND the pipeline log shows `[STEP 3/4] Generating strategic intelligence report (v2)...`

#### Scenario: v1 fallback still activates when no prior data
- **WHEN** migration 036 has been applied BUT all `previous_value` values for `target_date` are `NULL` (e.g., first ever pipeline run)
- **THEN** `indicators_delta` is empty AND `use_strategic_v2 = False` AND v1 fallback runs without error
