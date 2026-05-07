-- =============================================================================
-- Migration 037 — Romania Vertical PoC: geo-focus tagging + source metadata
--
-- Adds geographic focus tagging to articles and per-source metadata to
-- intelligence_sources to enable the Romania vertical pipeline and
-- multi-tenant vertical support.
--
-- Changes:
--   1. Adds geo_focus TEXT[] DEFAULT '{}' to articles with GIN index
--   2. Backfills geo_focus from existing GPE entities
--   3. Adds geo_region TEXT DEFAULT 'global' to intelligence_sources
--   4. Adds cadence_use, cadence_weight, retrieval_profile, languages
--      per-source metadata to intelligence_sources
--
-- Rollback:
--   ALTER TABLE articles DROP COLUMN IF EXISTS geo_focus;
--   ALTER TABLE intelligence_sources
--     DROP COLUMN IF EXISTS geo_region,
--     DROP COLUMN IF EXISTS cadence_use,
--     DROP COLUMN IF EXISTS cadence_weight,
--     DROP COLUMN IF EXISTS retrieval_profile,
--     DROP COLUMN IF EXISTS languages;
--
-- Apply: psql $DATABASE_URL -f migrations/037_romania_vertical.sql
-- =============================================================================

-- 1. geo_focus on articles
ALTER TABLE articles
    ADD COLUMN IF NOT EXISTS geo_focus TEXT[] DEFAULT '{}';

-- Backfill from GPE entities already extracted by spaCy
UPDATE articles
SET geo_focus = ARRAY(
    SELECT jsonb_array_elements_text(entities->'by_type'->'GPE')
)
WHERE entities ? 'by_type'
  AND entities->'by_type' ? 'GPE'
  AND (geo_focus IS NULL OR geo_focus = '{}');

CREATE INDEX IF NOT EXISTS idx_articles_geo_focus
    ON articles USING GIN(geo_focus);

-- 2. geo_region on intelligence_sources
ALTER TABLE intelligence_sources
    ADD COLUMN IF NOT EXISTS geo_region TEXT DEFAULT 'global';

-- 3. Per-source cadence metadata
ALTER TABLE intelligence_sources
    ADD COLUMN IF NOT EXISTS cadence_use TEXT[] DEFAULT '{daily,weekly}',
    ADD COLUMN IF NOT EXISTS cadence_weight JSONB DEFAULT '{"daily":0.5,"weekly":0.5}',
    ADD COLUMN IF NOT EXISTS retrieval_profile TEXT DEFAULT 'always',
    ADD COLUMN IF NOT EXISTS languages TEXT[] DEFAULT '{en}';

-- 4. country_code on macro_indicators for multi-country filtering
ALTER TABLE macro_indicators
    ADD COLUMN IF NOT EXISTS country_code VARCHAR(10) DEFAULT 'US';

CREATE INDEX IF NOT EXISTS idx_macro_country ON macro_indicators(country_code, indicator_key);
