## ADDED Requirements

### Requirement: Production runbook file exists at docs/runbooks/production.md
The system SHALL provide a standalone production operations reference at `docs/runbooks/production.md` containing all Docker Compose, SSH, database, Nginx, and GitHub Actions commands for the Hetzner production environment.

#### Scenario: Runbook covers all operational domains
- **WHEN** an operator opens `docs/runbooks/production.md`
- **THEN** they SHALL find: server facts header, Docker Compose commands, GitHub Actions workflow table with all 6 workflows, environment file management, database direct access commands, Nginx commands, and SSH access instructions

#### Scenario: Runbook includes photon geocoder commands
- **WHEN** an operator needs to enable the self-hosted Photon geocoding service
- **THEN** the runbook SHALL provide the `docker compose --profile photon up` command and the one-time Italia dataset download command

#### Scenario: Runbook is linked from CLAUDE.md
- **WHEN** Claude Code loads the project context
- **THEN** CLAUDE.md SHALL contain a reference to `docs/runbooks/production.md` with the key server facts inline (server type, deploy path, env file path)

### Requirement: CLAUDE.md stays at or below 210 lines
CLAUDE.md SHALL be ≤210 lines after the refactor, down from 423 lines, by removing content derivable from code or already covered by per-module context.md files.

#### Scenario: Derivable CLI commands are removed
- **WHEN** a developer reads the new CLAUDE.md
- **THEN** they SHALL NOT find `npm install`, `npm run dev`, `npm run build`, `npm run lint`, standard `pytest tests/ -v` patterns, `black`, `flake8`, or `ruff` commands (all derivable from package.json or requirements-dev.txt)

#### Scenario: Non-derivable pipeline commands are preserved
- **WHEN** a developer reads the new CLAUDE.md
- **THEN** they SHALL find the 7-step pipeline sequence with script names in order, the HITL Streamlit entry point (`streamlit run Home.py`), and the `generate_report.py` flags (`--macro-first`, `--skip-article-signals`)

### Requirement: All stale data in CLAUDE.md is corrected
CLAUDE.md SHALL contain accurate data for module line counts, migration count, config files, required env vars, Docker services, GitHub Actions workflows, and pytest markers.

#### Scenario: Module line counts are accurate
- **WHEN** Claude Code reads the Module Index table
- **THEN** `report_generator.py` SHALL show ~3385 lines (not ~2700), `database.py` SHALL show ~2708 lines (not ~2445), `narrative_processor.py` SHALL show ~1517 lines (not ~1498)

#### Scenario: Migration count is accurate
- **WHEN** Claude Code reads CLAUDE.md
- **THEN** the migration count SHALL read "42 SQL files" (not "19+")

#### Scenario: All 9 config files are listed
- **WHEN** Claude Code reads the Configuration Files section
- **THEN** all 9 files SHALL be listed: feeds.yaml, top_50_tickers.yaml, entity_blocklist.yaml, asset_theory_library.yaml, macro_convergences.yaml, sc_sector_map.yaml, iran_static_data.json, pdf_sources.yaml, and .env/.env.example

#### Scenario: All 4 required env vars are listed
- **WHEN** Claude Code reads the required env vars
- **THEN** all 4 SHALL be listed: DATABASE_URL, GEMINI_API_KEY, INTELLIGENCE_API_KEY, FRED_API_KEY

#### Scenario: All 6 workflows are listed
- **WHEN** Claude Code reads the Infrastructure Reference section
- **THEN** all 6 SHALL be listed: deploy.yml, pipeline.yml, migrate.yml, evals_fast.yml, evals_nightly.yml, update-docs.yml

#### Scenario: All 6 pytest markers are documented
- **WHEN** Claude Code reads the Testing section
- **THEN** all 6 markers SHALL be listed: unit, integration, e2e, slow, eval_fast, eval_slow — with eval_fast/eval_slow behavior explained (mocked vs real model, every PR vs nightly)

### Requirement: Critical Pitfalls are organized into 8 categories
CLAUDE.md SHALL preserve all 27 Critical Pitfalls and organize them under 8 named categories: LLM Integration, Data Encoding, Database/Schema, Macro/Financial, Geocoding, Auth/Access, Oracle, Ingestion.

#### Scenario: All 27 pitfalls are present after reorganization
- **WHEN** the new CLAUDE.md is committed
- **THEN** a count of bold pitfall headings SHALL equal 27 (same as the original)

#### Scenario: Pitfall categories match content domains
- **WHEN** a developer working on ingestion scans Critical Pitfalls
- **THEN** they SHALL find all ingestion-related pitfalls (timeout, per-domain concurrency, Scrapling semaphore, CI test config) grouped under the "Ingestion" category header
