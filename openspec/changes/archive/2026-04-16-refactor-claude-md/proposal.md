## Why

CLAUDE.md loads into every Claude Code session and consumes tokens on each request — yet 51% of its 423 lines are either duplicated in the 17 per-module `context.md` files, stale (line counts wrong by hundreds, migration count "19+" vs actual 42), or pure ops runbook content that has nothing to do with coding. The file violates April 2026 best practices: it teaches Claude things it could read from the code, and buries the genuinely non-derivable Critical Pitfalls in noise.

## What Changes

- **CLAUDE.md rewritten** from 423 → ~205 lines (-51%), removing all derivable content and duplicated architecture prose
- **All stale data corrected**: `report_generator.py` (2700→3385 lines), `database.py` (2445→2708 lines), migrations ("19+"→42 SQL files), config files listed (3→9), required env vars (2→4), GitHub Actions workflows (3→6), pytest markers (4→6 including `eval_fast`/`eval_slow`)
- **Critical Pitfalls reorganized** into 8 named categories (LLM Integration, Data Encoding, Database/Schema, Macro/Financial, Geocoding, Auth/Access, Oracle, Ingestion) — all 27 items preserved, improved scannability
- **`docs/runbooks/production.md` created** (~120 lines): receives the 147-line infrastructure section (Docker Compose, SSH, DB access, Nginx, GitHub Actions commands)
- **Module Index table** replaces stale prose "Key Modules" section — corrected line counts, pointers to context.md files

## Non-goals

- Not modifying any of the 17 `context.md` files (all up-to-date)
- Not modifying `README.md` (different audience: user onboarding)
- Not modifying `docs/architecture/*.md` (already complete)
- Not changing any source code

## Capabilities

### New Capabilities

- `production-runbook`: Standalone ops reference at `docs/runbooks/production.md` covering Docker Compose, SSH, DB access, Nginx, and GitHub Actions commands for the Hetzner production environment.

### Modified Capabilities

_(none — this is a documentation-only refactor, no spec-level behavior changes)_

## Impact

- **Primary file modified**: `CLAUDE.md` (repo root)
- **New file created**: `docs/runbooks/production.md`
- **SQL migration required**: No
- **Gemini model tier involved**: None
- **Strategic Intelligence Layer phase**: Not applicable
- **Affected audience**: Claude Code AI assistant sessions (reduced token consumption per session); on-call engineers and operators (production runbook now has a dedicated home)
