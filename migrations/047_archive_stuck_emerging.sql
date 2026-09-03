-- Migration 047: Archive storylines stuck in the 'emerging' dead-end
--
-- Run: psql $DATABASE_URL -f migrations/047_archive_stuck_emerging.sql
-- Rollback: migrations/047_archive_stuck_emerging_rollback.sql
--
-- ---------------------------------------------------------------------------
-- WHY
-- ---------------------------------------------------------------------------
-- 'emerging' had no exit for a whole class of storylines. A storyline is created
-- with narrative_status='emerging' and article_count = HDBSCAN cluster size, which
-- is frequently >= 3. The only two ways out were:
--
--   * promotion to 'active'  — fires only when a NEW article is assigned
--   * rule_4 archival        — fires only when article_count < 3
--
-- One born with >= 3 articles that never receives another satisfies neither.
-- rule_1 decays its momentum forever, but rule_2 acts only on 'active' and rule_3
-- only on 'stabilized'. It stays 'emerging' indefinitely.
--
-- Measured in production 2026-08-23:
--   emerging     2226 rows, avg momentum 0.041, 1828 older than 30 days,
--                oldest last_update 2026-02-16
--   stabilized    142 rows, 0 older than 30 days   <- healthy
--   active         74 rows, 0 older than 30 days   <- healthy
--
-- The accumulation inflated /api/v1/stories/graph to 2402 nodes + 41344 edges
-- (5.82 MB), which broke the landing page's live data: the fetch exceeded both its
-- 2500 ms budget (measured 3229 ms) and Next.js's hard 2 MB data-cache entry limit.
--
-- The permanent fix is rule_5 in NarrativeProcessor._apply_decay(), which archives
-- stale 'emerging' rows going forward. THIS migration cleans up the rows that
-- accumulated before rule_5 existed. Both are required: without rule_5 the backlog
-- simply rebuilds (~370 rows/month at the observed rate).
--
-- ---------------------------------------------------------------------------
-- CRITICAL: do NOT add an article_count predicate
-- ---------------------------------------------------------------------------
-- migrations/016_graph_cleanup.sql step 1 already attempted this cleanup, but it
-- copied rule_4's predicate verbatim:
--
--     WHERE narrative_status = 'emerging'
--       AND article_count < 3            <-- inherited the blind spot
--       AND created_at < NOW() - INTERVAL '5 days';
--
-- That predicate selects exactly the rows rule_4 was already archiving on its own,
-- so 016 never touched a single stuck row. The bug survived the cleanup meant to
-- fix it. Filtering here is on status and staleness ONLY.
--
-- ---------------------------------------------------------------------------
-- WARNING: scripts/rebuild_graph_edges.py makes this irreversible
-- ---------------------------------------------------------------------------
-- Its step 0 runs:
--     DELETE FROM storyline_edges
--     WHERE source_story_id IN (SELECT id FROM storylines
--         WHERE narrative_status='archived' AND last_update < NOW() - INTERVAL '30 days')
--        OR target_story_id IN (...)
--
-- Every row archived below already satisfies that predicate at the moment of
-- archival (they are all stale by more than 30 days by definition). Running that
-- script after this migration therefore hard-deletes ~40k edges, and the rollback
-- would restore narrative_status but NOT the graph — the rebuild pass only
-- regenerates edges for emerging/active/stabilized rows, never archived ones.
--
-- Do not run rebuild_graph_edges.py until this change is confirmed good in prod.
--
-- Deploy order: apply BEFORE the next pipeline run, then restart the backend
-- (the graph endpoint holds a 1h in-memory cache; without a restart the effect
-- is not observable). After confirming, re-run scripts/compute_communities.py to
-- refresh community_id / community_name.
-- ===========================================================================

BEGIN;

-- 1. Snapshot for rollback. Created inside the migration so that cleanup and
--    rollback are self-contained rather than depending on hand-run commands.
--    Kept as a permanent table: drop it manually once the change is confirmed.
DROP TABLE IF EXISTS storylines_047_backfill_snapshot;

CREATE TABLE storylines_047_backfill_snapshot AS
SELECT id, narrative_status, status, momentum_score, article_count,
       last_update, updated_at
FROM storylines
WHERE narrative_status = 'emerging'
  AND last_update < NOW() - INTERVAL '30 days';

-- 2. Archive the stuck rows.
--    Filtered on status and staleness ONLY — see the CRITICAL note above.
--    Two triggers fire on this UPDATE, both verified safe:
--      * sync_narrative_status   sets status='ARCHIVED' (symmetric on rollback:
--                                'emerging' maps back to 'ACTIVE')
--      * storylines_updated_at   sets updated_at=NOW() — touches updated_at only,
--                                NOT last_update, so the predicate above stays
--                                stable and this statement is repeatable
UPDATE storylines
SET narrative_status = 'archived'
WHERE narrative_status = 'emerging'
  AND last_update < NOW() - INTERVAL '30 days';

COMMIT;

-- 3. Refresh the IDF weights.
--    entity_idf filters on narrative_status IN ('emerging','active','stabilized')
--    both in its FROM clause and in the corpus-size subquery, so archiving rows
--    leaves it stale with inflated doc_freq and N. Its weights drive the TF-IDF
--    weighted Jaccard of every edge computed afterwards. Migration 016 step 4 did
--    the same thing for the same reason.
--    CONCURRENTLY is possible because idx_entity_idf_unique already exists.
--    Must run outside the transaction block above.
REFRESH MATERIALIZED VIEW CONCURRENTLY entity_idf;

-- ---------------------------------------------------------------------------
-- Verify after running:
-- ---------------------------------------------------------------------------
--   SELECT count(*) FROM storylines_047_backfill_snapshot;          -- expect ~1828
--   SELECT narrative_status, count(*),
--          count(*) FILTER (WHERE last_update < now() - interval '30 days') AS stale
--   FROM storylines GROUP BY 1 ORDER BY 2 DESC;                     -- emerging stale = 0
