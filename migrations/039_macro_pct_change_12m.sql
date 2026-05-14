-- Migration 039: add pct_change_12m to macro_indicators
-- Observation-count-based YoY proxy: 12 prior observations.
-- Semantic meaning by frequency:
--   daily   → 12 trading days (~2.5 weeks)  — not displayed
--   weekly  → 12 weeks (~quarterly)          — displayed as Δ12w
--   monthly → 12 months (true YoY)           — displayed as Δ12m(YoY)

ALTER TABLE macro_indicators
    ADD COLUMN IF NOT EXISTS pct_change_12m NUMERIC(10,4);
