-- Migration 045: narrative_themes (persistent centroids + cross-run lineage)
--
-- Adds the persistence layer for k-means-on-embedding community detection
-- (design.md § Decision 3). Unlike storylines.community_id (which resets in
-- meaning every time the active window rotates or a re-fit happens),
-- narrative_themes.persistent_id is an identity that survives across re-fits:
-- a theme that goes quiet and later re-emerges keeps the same persistent_id
-- instead of being treated as new.
--
-- Additive only (CREATE TABLE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS): zero
-- impact on existing pipeline runs until the k-means path starts populating it.
--
-- Spec reference: openspec/changes/narrative-clustering-embedding-based/
--                 design.md § Decision 3 (schema) + § Decision 2 (lifecycle)
--                 + Open Question 1 (shadow sink column).

-- =========================================================================
-- 1. narrative_themes
-- =========================================================================
CREATE TABLE IF NOT EXISTS narrative_themes (
    persistent_id            SERIAL PRIMARY KEY,
    centroid                 vector(384) NOT NULL,
    lifecycle_status         TEXT NOT NULL DEFAULT 'emerging'
        CHECK (lifecycle_status IN ('emerging', 'active', 'dormant', 'retired')),
    label                    TEXT,
    first_seen               TIMESTAMP DEFAULT NOW(),
    last_seen                TIMESTAMP DEFAULT NOW(),
    last_refit_run_id        UUID,
    n_members_last_refit     INTEGER,
    source_persistent_ids    INTEGER[],
    split_from_persistent_id INTEGER REFERENCES narrative_themes(persistent_id)
);

CREATE INDEX IF NOT EXISTS idx_narrative_themes_status
    ON narrative_themes (lifecycle_status);

COMMENT ON TABLE narrative_themes IS
    'Persistent k-means centroids for narrative theme clustering (champion path). '
    'persistent_id survives active-window rotation and periodic re-fits — the '
    'identity layer that storylines.community_id (window-scoped) cannot provide. '
    'See design.md § Decision 3.';
COMMENT ON COLUMN narrative_themes.centroid IS
    '384-dim k-means centroid in the same embedding space as storylines.current_embedding '
    '(paraphrase-multilingual-MiniLM-L12-v2). Updated on each periodic re-fit via warm-start.';
COMMENT ON COLUMN narrative_themes.lifecycle_status IS
    'emerging: no match in previous re-fit (new theme). active: matched via Hungarian '
    'matching in the last re-fit. dormant: no match in the last re-fit, kept for possible '
    're-emergence (never deleted). retired: reserved for future manual/automatic pruning, '
    'not written by this change.';
COMMENT ON COLUMN narrative_themes.label IS
    'LLM-generated name (_name_community, Gemini T5), nullable until named. Propagated to '
    'storylines.community_name for all member storylines — see design.md § Decision 6.';
COMMENT ON COLUMN narrative_themes.last_refit_run_id IS
    'Logical FK to narrative_run_metrics.run_id (no physical FK: run_id is UNIQUE, not PK, '
    'in that table — same pattern as migration 043).';
COMMENT ON COLUMN narrative_themes.source_persistent_ids IS
    'Lineage: persistent_id(s) this theme was merged from (N->1), NULL if not a merge result.';
COMMENT ON COLUMN narrative_themes.split_from_persistent_id IS
    'Lineage: persistent_id this theme split from (1->N), NULL if not a split result.';


-- =========================================================================
-- 2. storylines.community_id_kmeans_shadow (temporary shadow-period sink)
-- =========================================================================
-- k-means writes here during the shadow period, before promotion to champion.
-- storylines.community_id_shadow (migration 043) stays HDBSCAN's column for the
-- whole shadow period — no role inversion. Dropped in a future migration once
-- k-means is promoted and starts writing storylines.community_id directly.
-- See design.md § Open Question 1 (resolved) and § Migration Plan step 4.
ALTER TABLE storylines
    ADD COLUMN IF NOT EXISTS community_id_kmeans_shadow INTEGER DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_storylines_community_id_kmeans_shadow
    ON storylines (community_id_kmeans_shadow)
    WHERE community_id_kmeans_shadow IS NOT NULL;

COMMENT ON COLUMN storylines.community_id_kmeans_shadow IS
    'Temporary shadow-period sink for the k-means champion candidate, written during '
    'validation before promotion to storylines.community_id. To be dropped once promoted '
    '(design.md § Open Question 1).';


-- =========================================================================
-- Rollback (manual, commented out for safety)
-- =========================================================================
-- ALTER TABLE storylines DROP COLUMN IF EXISTS community_id_kmeans_shadow;
-- DROP TABLE IF EXISTS narrative_themes;
