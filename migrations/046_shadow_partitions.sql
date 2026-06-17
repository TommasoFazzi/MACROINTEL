-- Migration 046: shadow_partitions JSONB on narrative_run_metrics
--
-- Adds the persistence column for the Phase 1E 4-way shadow comparison framework
-- (design.md § Decision 22). Each community_detection run stores an array of
-- partition score dicts — Louvain-full, Louvain-backbone, Leiden-full,
-- Leiden-backbone — as pure observation metrics. Only louvain_full is the
-- partition actually applied to storylines.community_id; the other 3 are shadow.
--
-- Additive only (ADD COLUMN IF NOT EXISTS / CREATE ... IF NOT EXISTS): zero
-- impact on existing rows or pipeline runs until producers populate it.
--
-- Replaces the legacy shadow_diff_report (043) approach — see Decision 22 for why
-- the single-shadow / --promote-shadow pattern was dropped in favor of 4-way.
--
-- shadow_partitions JSONB shape (one object per partition):
--   {"name": "louvain_full", "n_edges": 92653, "n_communities": 27,
--    "n_singletons": 10, "max_community_size": 341, "avg_community_size": 66.9,
--    "modularity": 0.357, "silhouette": -0.074, "coherence_med": 0.377,
--    "runtime_ms": 3100, "gamma_used": null, "gamma_sweep_range": null}
--
-- Spec reference: openspec/changes/upgrade-narrative-clustering-algorithms/
--                 tasks.md task 1.21a + design.md § Decision 22.

-- =========================================================================
-- 1. shadow_partitions column
-- =========================================================================
ALTER TABLE narrative_run_metrics
    ADD COLUMN IF NOT EXISTS shadow_partitions JSONB;

-- GIN index for dashboard queries that filter/extract inside the JSONB array.
CREATE INDEX IF NOT EXISTS idx_narrative_run_metrics_shadow_partitions
    ON narrative_run_metrics USING GIN (shadow_partitions);

COMMENT ON COLUMN narrative_run_metrics.shadow_partitions IS
    'Phase 1E (Decision 22) 4-way shadow comparison: array of partition score '
    'dicts (louvain_full, louvain_backbone, leiden_full, leiden_backbone). '
    'Pure metrics — only louvain_full is applied to storylines.community_id. '
    'Replaces the legacy shadow_diff_report column.';


-- =========================================================================
-- 2. v_shadow_partitions_unnested — flat view for dashboard SQL
-- =========================================================================
-- One row per (run, partition) via LATERAL jsonb_to_recordset, so the Streamlit
-- dashboard (task 1.22a) can query metrics relationally instead of digging into
-- the JSONB array. Rows with NULL shadow_partitions are skipped (INNER join via
-- the implicit cross-join-lateral on a non-null array).
CREATE OR REPLACE VIEW v_shadow_partitions_unnested AS
SELECT
    m.run_id,
    m.ts,
    m.n_storylines_active,
    p.name,
    p.n_edges,
    p.n_communities,
    p.n_singletons,
    p.max_community_size,
    p.avg_community_size,
    p.modularity,
    p.silhouette,
    p.coherence_med,
    p.runtime_ms,
    p.gamma_used,
    p.gamma_sweep_range
FROM narrative_run_metrics m
CROSS JOIN LATERAL jsonb_to_recordset(m.shadow_partitions) AS p(
    name                TEXT,
    n_edges             INTEGER,
    n_communities       INTEGER,
    n_singletons        INTEGER,
    max_community_size  INTEGER,
    avg_community_size  FLOAT,
    modularity          FLOAT,
    silhouette          FLOAT,
    coherence_med       FLOAT,
    runtime_ms          INTEGER,
    gamma_used          FLOAT,
    gamma_sweep_range   JSONB
)
WHERE m.shadow_partitions IS NOT NULL;

COMMENT ON VIEW v_shadow_partitions_unnested IS
    'Flattened (run_id, partition) rows from narrative_run_metrics.shadow_partitions '
    'for the Phase 1E shadow comparison dashboard (task 1.22a / Decision 22).';


-- =========================================================================
-- Rollback (manual, commented — additive migration)
-- =========================================================================
-- DROP VIEW IF EXISTS v_shadow_partitions_unnested;
-- DROP INDEX IF EXISTS idx_narrative_run_metrics_shadow_partitions;
-- ALTER TABLE narrative_run_metrics DROP COLUMN IF EXISTS shadow_partitions;
