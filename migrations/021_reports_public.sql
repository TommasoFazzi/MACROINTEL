-- Migration 021: Public insights columns on reports table
-- Purpose: Support programmatic SEO via /insights public pages
-- Applied: manually via psql or load_to_database.py --init-only

ALTER TABLE reports
    ADD COLUMN IF NOT EXISTS slug         VARCHAR(500) UNIQUE,
    ADD COLUMN IF NOT EXISTS is_public    BOOLEAN DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_reports_slug      ON reports (slug) WHERE slug IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_reports_is_public ON reports (report_date DESC) WHERE is_public = TRUE;

COMMENT ON COLUMN reports.slug      IS 'Evergreen URL slug for public /insights page (e.g. iran-nuclear-escalation-analysis)';
COMMENT ON COLUMN reports.is_public IS 'If TRUE, report is surfaced on the public /insights endpoint';
