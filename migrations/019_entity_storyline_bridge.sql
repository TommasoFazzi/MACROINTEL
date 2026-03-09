-- Migration 019: Entity–Storyline bridge materialized view + intelligence_score
--
-- 1. mv_entity_storyline_bridge: pre-computed JOIN across 4 tables
--    entities → entity_mentions → articles → article_storylines → storylines
--    Refreshed after each narrative processing run.
--
-- 2. intelligence_score on entities: composite significance metric.

-- ============================================================
-- 1. Materialized view
-- ============================================================
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_entity_storyline_bridge AS
SELECT
    e.id                 AS entity_id,
    e.name               AS entity_name,
    e.entity_type,
    s.id                 AS storyline_id,
    s.title              AS storyline_title,
    s.narrative_status,
    s.momentum_score,
    s.community_id,
    COUNT(DISTINCT a.id) AS shared_articles,
    MAX(a.published_date) AS latest_article_date
FROM entities e
JOIN entity_mentions em     ON em.entity_id = e.id
JOIN articles a             ON a.id = em.article_id
JOIN article_storylines ast ON ast.article_id = a.id
JOIN storylines s           ON s.id = ast.storyline_id
WHERE s.narrative_status IN ('emerging', 'active', 'stabilized')
GROUP BY e.id, e.name, e.entity_type,
         s.id, s.title, s.narrative_status, s.momentum_score, s.community_id;

-- Unique index required for REFRESH MATERIALIZED VIEW CONCURRENTLY
CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_esb_pk
    ON mv_entity_storyline_bridge (entity_id, storyline_id);

-- Lookup indexes
CREATE INDEX IF NOT EXISTS idx_mv_esb_entity
    ON mv_entity_storyline_bridge (entity_id);
CREATE INDEX IF NOT EXISTS idx_mv_esb_storyline
    ON mv_entity_storyline_bridge (storyline_id);
CREATE INDEX IF NOT EXISTS idx_mv_esb_status
    ON mv_entity_storyline_bridge (narrative_status);

COMMENT ON MATERIALIZED VIEW mv_entity_storyline_bridge IS
    'Pre-joined entity↔storyline bridge. Refresh after narrative processing.';

-- ============================================================
-- 2. intelligence_score column on entities
-- ============================================================
ALTER TABLE entities ADD COLUMN IF NOT EXISTS intelligence_score REAL DEFAULT 0.0;

CREATE INDEX IF NOT EXISTS idx_entities_intel_score
    ON entities (intelligence_score DESC);

COMMENT ON COLUMN entities.intelligence_score IS
    'Composite significance score (0–1). Combines mention frequency, '
    'storyline connectivity, recency, and momentum.';

-- ============================================================
-- 3. Log completion
-- ============================================================
DO $$
BEGIN
    RAISE NOTICE '✓ Migration 019 completed: mv_entity_storyline_bridge + intelligence_score';
END $$;
