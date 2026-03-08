-- Migration 016: Graph cleanup
-- Reduces zombie storylines and weak/duplicate edges for a readable graph.
-- Run: psql $DATABASE_URL -f migrations/016_graph_cleanup.sql
--
-- Deploy order: apply this BEFORE the next pipeline run.
-- After running: python scripts/compute_communities.py --min-weight 0.25

-- 1. Archive zombie storylines (emerging, <3 articles, >5 days old)
UPDATE storylines
SET narrative_status = 'archived'
WHERE narrative_status = 'emerging'
  AND article_count < 3
  AND created_at < NOW() - INTERVAL '5 days';

-- 2. Delete weak edges (below raised threshold of 0.20)
DELETE FROM storyline_edges WHERE weight < 0.20;

-- 3. Delete duplicate bidirectional edges.
--    For each (A→B, B→A) pair, keep the one with higher weight.
--    If equal weight, keep the one with lower ctid (arbitrary stable tie-break).
DELETE FROM storyline_edges e1
USING storyline_edges e2
WHERE e1.source_story_id = e2.target_story_id
  AND e1.target_story_id = e2.source_story_id
  AND (
    e1.weight < e2.weight
    OR (e1.weight = e2.weight AND e1.ctid > e2.ctid)
  );

-- 4. Refresh entity_idf to reflect the new active storylines set
REFRESH MATERIALIZED VIEW entity_idf;

-- 5. Null out community_id (will be recomputed by compute_communities.py)
UPDATE storylines SET community_id = NULL;

-- Verify results after running:
-- SELECT COUNT(*) FROM storylines WHERE narrative_status IN ('emerging', 'active');
-- SELECT COUNT(*) FROM storyline_edges;
-- SELECT AVG(weight), MIN(weight), MAX(weight) FROM storyline_edges;
