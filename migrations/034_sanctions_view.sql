-- Migration 034: Sanitized public view for sanctions_registry
-- Rationale: sanctions_registry.properties (JSONB) can contain raw PII fields
-- (birthDate, address, idNumber, passportNumber, nationalId) from OpenSanctions.
-- This view strips those fields, providing a safe surface for SQLTool and
-- ReferenceTool without requiring PostgreSQL Row-Level Security roles.
-- All Oracle 2.0 tools must query v_sanctions_public, not the base table.

CREATE OR REPLACE VIEW v_sanctions_public AS
SELECT
    id,
    caption,
    schema_type,
    aliases,
    countries,
    datasets,
    properties
        - 'birthDate'
        - 'birthPlace'
        - 'address'
        - 'idNumber'
        - 'taxNumber'
        - 'passportNumber'
        - 'nationalId'
        - 'registrationNumber'
        - 'phone'
        - 'email' AS properties,
    first_seen,
    last_seen,
    last_updated
FROM sanctions_registry;

COMMENT ON VIEW v_sanctions_public IS
    'PII-sanitized view of sanctions_registry. '
    'Strips: birthDate, birthPlace, address, idNumber, taxNumber, passportNumber, '
    'nationalId, registrationNumber, phone, email. '
    'Use this view in all Oracle 2.0 tool queries instead of the base table.';
