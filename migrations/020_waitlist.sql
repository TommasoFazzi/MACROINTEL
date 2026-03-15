-- Migration 020: Waitlist entries table
-- Purpose: Store invite requests from landing page
-- Applied: manually via psql or load_to_database.py --init-only

CREATE TABLE IF NOT EXISTS waitlist_entries (
    id              SERIAL PRIMARY KEY,
    email           VARCHAR(255) UNIQUE NOT NULL,
    name            VARCHAR(255),
    role            VARCHAR(100),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    access_code_sent BOOLEAN DEFAULT FALSE,
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_waitlist_email ON waitlist_entries (email);
CREATE INDEX IF NOT EXISTS idx_waitlist_created ON waitlist_entries (created_at DESC);

COMMENT ON TABLE waitlist_entries IS 'Landing page invite-only waitlist submissions';
