-- Migration 047 Rollback: restore storylines archived by the 'emerging' cleanup
--
-- Run: psql $DATABASE_URL -f migrations/047_archive_stuck_emerging_rollback.sql
--
-- Restores narrative_status from storylines_047_backfill_snapshot, the table
-- created by the forward migration.
--
-- ---------------------------------------------------------------------------
-- VALID ONLY IF scripts/rebuild_graph_edges.py HAS NOT RUN
-- ---------------------------------------------------------------------------
-- That script's step 0 hard-deletes storyline_edges rows whose endpoints are
-- archived and stale by more than 30 days — a predicate every row restored here
-- satisfied while archived. If it ran, this rollback restores narrative_status
-- but the edges are gone for good: the script's rebuild pass only regenerates
-- edges for emerging/active/stabilized rows, so nothing recreates them.
--
-- Check before rolling back:
--   SELECT count(*) FROM storyline_edges e
--   JOIN storylines_047_backfill_snapshot s
--     ON e.source_story_id = s.id OR e.target_story_id = s.id;
-- A count near zero means the edges were already deleted and this rollback will
-- restore the rows but not the graph.
--
-- The `status` column needs no explicit restore: the sync_narrative_status
-- trigger is symmetric and maps 'emerging' back to 'ACTIVE' on its own.
-- ===========================================================================

BEGIN;

UPDATE storylines s
SET narrative_status = b.narrative_status
FROM storylines_047_backfill_snapshot b
WHERE s.id = b.id
  AND s.narrative_status = 'archived';

COMMIT;

-- Rebuild the IDF weights over the restored corpus.
REFRESH MATERIALIZED VIEW CONCURRENTLY entity_idf;

-- ---------------------------------------------------------------------------
-- Verify after running:
--   SELECT narrative_status, count(*) FROM storylines GROUP BY 1 ORDER BY 2 DESC;
--   -- 'emerging' should be back to roughly its pre-migration count (~2226)
--
-- Then drop the snapshot once you are done:
--   DROP TABLE storylines_047_backfill_snapshot;
-- ---------------------------------------------------------------------------
