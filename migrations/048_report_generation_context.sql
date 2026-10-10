-- Migration 048: Persist the exact context given to the daily report writer
--
-- Run: psql $DATABASE_URL -f migrations/048_report_generation_context.sql
-- Rollback: migrations/048_report_generation_context_rollback.sql
--
-- ---------------------------------------------------------------------------
-- WHY
-- ---------------------------------------------------------------------------
-- A daily report could not be reproduced from the DB:
--   * the v2 writer sees only the top 10 articles as summary[:500], but
--     reports.sources lists every filtered article;
--   * the morning report reads intraday macro values written by
--     ensure_daily_macro_data(), which the 23:00 UTC evening fetch overwrites
--     with closes on the same date rows.
-- The citation verifier (src/llm/citation_verifier.py) also needs exactly what
-- the writer saw to judge [Article N] / [Storyline N] claims fairly.
--
-- One row per report, written by DatabaseManager.save_report() in the same
-- transaction as the report. Separate table (not reports.metadata) because
-- prompts are 30-150 KB/day and the reports API reads metadata.
--
-- Not exposed to Oracle: deliberately absent from SQLTool.ALLOWED_TABLES.
-- Idempotent.

CREATE TABLE IF NOT EXISTS report_generation_context (
    report_id            INTEGER PRIMARY KEY REFERENCES reports(id) ON DELETE CASCADE,
    writer_path          TEXT NOT NULL,                       -- 'v2' | 'v1'
    writer_model         TEXT,
    system_prompt        TEXT,
    user_prompt          TEXT NOT NULL,
    articles_in_prompt   JSONB NOT NULL DEFAULT '[]'::jsonb,  -- [{n, article_id, link, title, source, date, excerpt}]
    storylines_in_prompt JSONB NOT NULL DEFAULT '[]'::jsonb,  -- [{rank, storyline_id, title, summary}]
    macro_context_text   TEXT,
    macro_snapshot       JSONB,                               -- {captured_at, indicators: [...]}
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
