-- Migration 043: narrative run metrics + partition history + shadow community + NIS score
--
-- Adds observability tables and columns for the clustering upgrade (Phase 1B).
-- All operations are additive (CREATE TABLE / ADD COLUMN IF NOT EXISTS): zero
-- impact on current pipeline runs until producers start populating them.
--
-- Tables:
--   narrative_run_metrics       — one row per pipeline run (narrative_processing,
--                                 community_detection, consolidation)
--   storyline_community_history — partition history for TCS/EPR/Hungarian lineage
--                                 (key (run_id, storyline_id))
--
-- New storylines columns:
--   community_id_shadow         — destination for Leiden+CPM shadow runs
--                                 (Phase 1E task 1.20)
--   nis_score                   — Narrative Identity Stability per storyline,
--                                 cos(original_embedding, current_embedding),
--                                 populated by narrative_processor.py Stage 4
--
-- Spec reference: openspec/changes/upgrade-narrative-clustering-algorithms/
--                 design.md § Decision 6 (canonical schema) + tasks.md task 1.6
--                 (extra columns for TCS overlap, EWMA smoothing, drift,
--                 adaptive γ sweep).
--
-- Note on FK types: design.md § Decision 6 declared storyline_id as UUID, but
-- storylines.id is SERIAL (migration 008). Using INTEGER here to match. The
-- same correction will need to propagate to migration 045 (community_lineage.
-- anchor_storylines) and to the corresponding Pydantic / Python types.

-- =========================================================================
-- 1. narrative_run_metrics
-- =========================================================================
CREATE TABLE IF NOT EXISTS narrative_run_metrics (
    id                          SERIAL PRIMARY KEY,
    run_id                      UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    ts                          TIMESTAMP DEFAULT NOW(),
    pipeline_step               TEXT NOT NULL
        CHECK (pipeline_step IN ('narrative_processing',
                                  'community_detection',
                                  'consolidation',
                                  'orphan_consolidation')),

    -- Population / partition counts (Phase 1C task 1.8)
    n_storylines_total          INTEGER,
    n_storylines_active         INTEGER,
    n_edges_pre_filter          INTEGER,
    n_edges_post_filter         INTEGER,
    n_communities               INTEGER,
    n_singletons                INTEGER,
    max_community_size          INTEGER,
    n_orphans                   INTEGER,
    n_archived                  INTEGER,

    -- Quality metrics (Phase 1C tasks 1.9, 1.11)
    silhouette                  FLOAT,
    community_coherence_med     FLOAT,
    cpm_quality                 FLOAT,
    modularity                  FLOAT,        -- legacy Louvain + shadow comparison
    tcs                         FLOAT,        -- NMI(part_t, part_t-1) on intersection
    epr                         FLOAT,        -- edge persistence ratio
    tcs_overlap_size            INTEGER,      -- |storyline intersection| for TCS (task 1.11)
    tcs_unreliable              BOOLEAN DEFAULT FALSE,  -- TRUE when overlap < 50

    -- Adaptive parameters (Phase 1E + 2D)
    hdbscan_mcs_smoothed        INTEGER,      -- EWMA-smoothed min_cluster_size (task 2.7)
    gamma_sweep_range           JSONB,        -- {start, end, num} for γ-sweep (task 1.19)
    backbone_weight_p50         FLOAT,        -- median edge weight post-disparity (task 1.19)
    backbone_weight_p75         FLOAT,        -- p75 edge weight post-disparity (task 1.19)

    -- Stage stats
    decay_stats                 JSONB,        -- {rule_1, rule_2, rule_3, rule_4, reverse_promo}
    match_stats                 JSONB,        -- {matched, new_storyline, sent_to_orphan,
                                              --  edges_classified_regex, edges_classified_llm_batch,
                                              --  summary_cache_skips}
    consolidation_stats         JSONB,
    shadow_diff_report          JSONB,        -- populated only by --shadow runs (task 1.20)
    drift_signals               JSONB,        -- populated by detect_drift (task 5.1)

    runtime_seconds             FLOAT
);

CREATE INDEX IF NOT EXISTS idx_narrative_run_metrics_ts
    ON narrative_run_metrics (ts DESC);

CREATE INDEX IF NOT EXISTS idx_narrative_run_metrics_step_ts
    ON narrative_run_metrics (pipeline_step, ts DESC);

COMMENT ON TABLE narrative_run_metrics IS
    'One row per pipeline step run. Drives baseline mobile p50 30d for drift detection (Phase 5B).';
COMMENT ON COLUMN narrative_run_metrics.tcs_unreliable IS
    'TRUE when intersection size < 50 (TCS statistically unstable). NULL TCS when overlap < 30.';


-- =========================================================================
-- 2. storyline_community_history (partition history per TCS/EPR/lineage)
-- =========================================================================
-- NB: storyline_id is INTEGER (not UUID as in design.md § Decision 6) to match
-- storylines.id (SERIAL, migration 008).
CREATE TABLE IF NOT EXISTS storyline_community_history (
    run_id        UUID NOT NULL,
    storyline_id  INTEGER NOT NULL REFERENCES storylines(id) ON DELETE CASCADE,
    community_id  INTEGER,
    ts            TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (run_id, storyline_id)
);

CREATE INDEX IF NOT EXISTS idx_storyline_community_history_storyline
    ON storyline_community_history (storyline_id);

CREATE INDEX IF NOT EXISTS idx_storyline_community_history_run
    ON storyline_community_history (run_id);

COMMENT ON TABLE storyline_community_history IS
    'Snapshot of (run_id, storyline_id, community_id) for every community_detection run. '
    'Source-of-truth for TCS intersection (task 1.11) and Hungarian cross-run matching (Phase 4A).';


-- =========================================================================
-- 3. storylines.community_id_shadow + nis_score
-- =========================================================================
ALTER TABLE storylines
    ADD COLUMN IF NOT EXISTS community_id_shadow INTEGER DEFAULT NULL;

ALTER TABLE storylines
    ADD COLUMN IF NOT EXISTS nis_score FLOAT DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_storylines_community_id_shadow
    ON storylines (community_id_shadow)
    WHERE community_id_shadow IS NOT NULL;

COMMENT ON COLUMN storylines.community_id_shadow IS
    'Leiden+CPM shadow partition. Cleared by --promote-shadow (Phase 1E task 1.21).';
COMMENT ON COLUMN storylines.nis_score IS
    'Narrative Identity Stability: cos(original_embedding, current_embedding). '
    'Computed post-Stage 4 in narrative_processor.py (Phase 1C task 1.12).';


-- =========================================================================
-- Rollback (manual, commented out for safety)
-- =========================================================================
-- DROP INDEX IF EXISTS idx_storylines_community_id_shadow;
-- ALTER TABLE storylines DROP COLUMN IF EXISTS nis_score;
-- ALTER TABLE storylines DROP COLUMN IF EXISTS community_id_shadow;
-- DROP TABLE IF EXISTS storyline_community_history;
-- DROP TABLE IF EXISTS narrative_run_metrics;
