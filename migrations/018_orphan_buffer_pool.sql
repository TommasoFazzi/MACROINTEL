-- Migration 018: Orphan event buffer pool
-- Instead of creating singleton storylines from HDBSCAN noise events,
-- store them in a buffer and retry matching on each pipeline run.

CREATE TABLE IF NOT EXISTS orphan_events (
    id              SERIAL PRIMARY KEY,
    article_ids     INTEGER[] NOT NULL,
    representative_title TEXT NOT NULL,
    centroid_embedding   vector(384) NOT NULL,
    key_entities    JSONB DEFAULT '[]'::jsonb,
    category        TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    retry_count     INTEGER DEFAULT 0,
    last_retry      TIMESTAMPTZ DEFAULT NOW()
);

-- Index for cosine similarity matching
CREATE INDEX IF NOT EXISTS idx_orphan_events_embedding
    ON orphan_events USING ivfflat (centroid_embedding vector_cosine_ops)
    WITH (lists = 10);

-- Index for decay cleanup
CREATE INDEX IF NOT EXISTS idx_orphan_events_created_at
    ON orphan_events (created_at);

COMMENT ON TABLE orphan_events IS
    'Buffer pool for articles that could not be matched to any storyline or cluster. '
    'Retried on each pipeline run; discarded after 14 days.';
