-- =============================================================================
-- Migration 036 — Add previous_value to macro_indicators
-- Strategic Intelligence Layer — Phase 3 prerequisite fix.
--
-- The Phase 3 screening function (_get_macro_indicators_for_screening) and
-- market_tool.py both SELECT previous_value FROM macro_indicators, but the
-- column was never added to the schema. This caused the entire v2 analysis
-- path to bypass silently on every pipeline run.
--
-- Changes:
--   1. Adds nullable previous_value NUMERIC(20,6) to macro_indicators
--   2. Backfills existing rows with the most recent prior value per indicator
--
-- Rollback: ALTER TABLE macro_indicators DROP COLUMN IF EXISTS previous_value;
-- Apply: psql $DATABASE_URL -f migrations/036_add_previous_value_macro_indicators.sql
-- =============================================================================

ALTER TABLE macro_indicators
    ADD COLUMN IF NOT EXISTS previous_value NUMERIC(20, 6);

-- Backfill: for each row, set previous_value to the most recent prior value
-- for the same indicator_key. Rows with no prior history remain NULL.
-- Uses the existing idx_macro_key index on (indicator_key, date DESC).
UPDATE macro_indicators mi
SET previous_value = (
    SELECT value
    FROM macro_indicators prev
    WHERE prev.indicator_key = mi.indicator_key
      AND prev.date < mi.date
    ORDER BY prev.date DESC
    LIMIT 1
);
