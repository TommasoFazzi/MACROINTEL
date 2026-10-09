-- Rollback for migration 048: drop the persisted writer context.
-- Lossy: the captured prompts and macro snapshots cannot be regenerated.
DROP TABLE IF EXISTS report_generation_context;
