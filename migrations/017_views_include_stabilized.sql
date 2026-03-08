-- Migration 016: Include 'stabilized' storylines in graph views
-- Previously only 'emerging' and 'active' were visible.
-- Stabilized storylines are mature but still relevant (not archived).

-- 1. Update v_active_storylines to include stabilized
DROP VIEW IF EXISTS v_active_storylines;
CREATE VIEW v_active_storylines AS
SELECT
    s.id,
    s.title,
    s.summary,
    s.status,
    s.narrative_status,
    s.category,
    s.article_count,
    s.momentum_score,
    s.start_date,
    s.last_update,
    s.key_entities,
    s.last_graph_update,
    (s.summary_vector IS NOT NULL) AS has_summary_vector,
    EXTRACT(DAY FROM NOW() - s.start_date)::INTEGER AS days_active,
    EXTRACT(DAY FROM NOW() - s.last_update)::INTEGER AS days_since_update,
    s.community_id
FROM storylines s
WHERE s.narrative_status IN ('emerging', 'active', 'stabilized')
ORDER BY s.momentum_score DESC, s.last_update DESC;

-- 2. Update v_storyline_graph to include stabilized edges
CREATE OR REPLACE VIEW v_storyline_graph AS
SELECT
    e.id AS edge_id,
    e.source_story_id,
    s1.title AS source_title,
    s1.narrative_status AS source_status,
    s1.momentum_score AS source_momentum,
    e.target_story_id,
    s2.title AS target_title,
    s2.narrative_status AS target_status,
    s2.momentum_score AS target_momentum,
    e.relation_type,
    e.weight,
    e.explanation
FROM storyline_edges e
JOIN storylines s1 ON e.source_story_id = s1.id
JOIN storylines s2 ON e.target_story_id = s2.id
WHERE s1.narrative_status IN ('emerging', 'active', 'stabilized')
  AND s2.narrative_status IN ('emerging', 'active', 'stabilized')
ORDER BY e.weight DESC;
