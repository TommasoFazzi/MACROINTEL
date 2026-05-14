-- =============================================================================
-- Migration 038 — Historical Context Columns for Macro Indicators
--
-- Adds pre-computed statistical context columns to macro_indicators:
--   - ma_7d / ma_30d     : simple moving averages (last 7 and 30 observations)
--   - std_30d            : population std deviation over last 30 observations
--   - pct_change_7d      : % change vs 7th prior observation
--   - pct_change_30d     : % change vs 30th prior observation
--   - percentile_rank_30d: where current value sits in last 30 observations (0-100)
--
-- All columns are NULLABLE. NULL = insufficient history (graceful degradation).
-- Derived columns are populated by _save_macro_indicator() via SQL subqueries,
-- not by application-side Python, to keep computation atomic per-upsert.
--
-- Prerequisites: migrations 034, 036, 037 must be applied first.
--
-- Rollback:
--   ALTER TABLE macro_indicators
--     DROP COLUMN IF EXISTS ma_7d,
--     DROP COLUMN IF EXISTS ma_30d,
--     DROP COLUMN IF EXISTS std_30d,
--     DROP COLUMN IF EXISTS pct_change_7d,
--     DROP COLUMN IF EXISTS pct_change_30d,
--     DROP COLUMN IF EXISTS percentile_rank_30d;
--
-- Apply:
--   docker compose -p app exec -T postgres psql -U intelligence_user -d intelligence_ita \
--     < migrations/038_macro_historical_context.sql
-- =============================================================================

ALTER TABLE macro_indicators
    ADD COLUMN IF NOT EXISTS ma_7d               NUMERIC(20, 6),
    ADD COLUMN IF NOT EXISTS ma_30d              NUMERIC(20, 6),
    ADD COLUMN IF NOT EXISTS std_30d             NUMERIC(20, 6),
    ADD COLUMN IF NOT EXISTS pct_change_7d       NUMERIC(10, 4),
    ADD COLUMN IF NOT EXISTS pct_change_30d      NUMERIC(10, 4),
    ADD COLUMN IF NOT EXISTS percentile_rank_30d NUMERIC(5, 1);

COMMENT ON COLUMN macro_indicators.ma_7d IS
    'Moving average of last 7 observations (same indicator_key + country_code, chronologically prior)';
COMMENT ON COLUMN macro_indicators.ma_30d IS
    'Moving average of last 30 observations';
COMMENT ON COLUMN macro_indicators.std_30d IS
    'Population std deviation of last 30 observations (STDDEV_POP; returns 0 if N=1, not NULL)';
COMMENT ON COLUMN macro_indicators.pct_change_7d IS
    '% change vs 7th prior observation: (current - v7) / abs(v7) * 100. NULL if < 7 prior rows.';
COMMENT ON COLUMN macro_indicators.pct_change_30d IS
    '% change vs 30th prior observation. NULL if < 30 prior rows.';
COMMENT ON COLUMN macro_indicators.percentile_rank_30d IS
    'Percentile rank of current value within last 30 observations (0=min, 100=max).';
