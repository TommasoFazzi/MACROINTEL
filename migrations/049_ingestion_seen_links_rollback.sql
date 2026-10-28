-- Rollback for migration 049: drop the attempted-links table.
-- Lossless for articles. After rollback, ingestion falls back to the 24h filter
-- (the link lookup fails) until the code is reverted too.
DROP TABLE IF EXISTS ingestion_seen_links;
