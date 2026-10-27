-- Migration 049: Links that ingestion has already attempted
--
-- Run: psql $DATABASE_URL -f migrations/049_ingestion_seen_links.sql
-- Rollback: migrations/049_ingestion_seen_links_rollback.sql
--
-- ---------------------------------------------------------------------------
-- WHY
-- ---------------------------------------------------------------------------
-- Ingestion used to keep only feed items published in the last 24h. Publishers
-- that stamp a nominal time and post later (RAND, EveryCRSReport) lost most of
-- their items. Selection is now "link never attempted and published within
-- 7 days" (src/ingestion/pipeline.py select_new_items).
--
-- "Attempted" = in articles.link OR in this table. This table is needed because
-- links dropped after extraction (relevance filter, content-hash duplicate,
-- extraction failure, scraped page too old) never reach articles, and would
-- otherwise be re-extracted on every run of the window.
--
-- Rows are written AFTER extraction completes, so a crash during extraction
-- (daily_pipeline retries ingestion once) leaves nothing recorded and the
-- retry selects the same items. Rows older than 30 days are pruned at the
-- start of each ingestion run.
--
-- Not exposed to Oracle. Idempotent.

CREATE TABLE IF NOT EXISTS ingestion_seen_links (
    link          TEXT PRIMARY KEY,
    source        TEXT,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ingestion_seen_links_first_seen
    ON ingestion_seen_links (first_seen_at);
