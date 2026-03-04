-- migrations/013_add_oracle_query_log.sql
-- Creates the oracle_query_log table for Oracle 2.0 query auditing.

CREATE TABLE IF NOT EXISTS oracle_query_log (
    id             BIGSERIAL PRIMARY KEY,
    session_id     TEXT        NOT NULL,
    query          TEXT        NOT NULL,
    intent         TEXT,
    complexity     TEXT,
    tools_used     TEXT[],
    execution_time FLOAT,
    success        BOOLEAN     NOT NULL DEFAULT TRUE,
    metadata       JSONB       NOT NULL DEFAULT '{}',
    created_at     TIMESTAMP   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_oracle_query_log_session
    ON oracle_query_log (session_id);

CREATE INDEX IF NOT EXISTS idx_oracle_query_log_created
    ON oracle_query_log (created_at DESC);
