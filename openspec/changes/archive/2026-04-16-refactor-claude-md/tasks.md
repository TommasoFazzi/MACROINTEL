## 1. Create production runbook

- [x] 1.1 Create `docs/runbooks/` directory
- [x] 1.2 Write `docs/runbooks/production.md` with: server facts header, Docker Compose commands (including `--profile photon` commands), all 6 GitHub Actions workflows table + `gh` CLI commands, environment file management (removing JWT_SECRET as "active"), database direct access commands, Nginx commands, SSH section
- [x] 1.3 Verify `docs/runbooks/production.md` is ~120 lines and all operational domains are covered

## 2. Rewrite CLAUDE.md

- [x] 2.1 Write new Header + Project Overview (keep current verbatim, ~12 lines)
- [x] 2.2 Write Module Index table with corrected line counts: report_generator.py=~3385, database.py=~2708, narrative_processor.py=~1517; pointers to each context.md
- [x] 2.3 Write Architecture Diagrams table (compress from 27→14 lines, remove bash code blocks)
- [x] 2.4 Write Key Commands section (23 lines): pipeline 7-step sequence in order, HITL Streamlit entry point, FastAPI command, generate_report.py flags, eval_fast/eval_slow pytest commands — remove all derivable commands
- [x] 2.5 Write Configuration Files table with all 9 files: feeds.yaml, top_50_tickers.yaml, entity_blocklist.yaml, asset_theory_library.yaml, macro_convergences.yaml, sc_sector_map.yaml, iran_static_data.json, pdf_sources.yaml, .env/.env.example
- [x] 2.6 Write Testing section (13 lines): all 6 markers with eval_fast/eval_slow behavior explained (mocked vs real, every PR vs nightly, fail threshold)
- [x] 2.7 Write Environment Requirements (8 lines): Python 3.12, PostgreSQL + pgvector + PostGIS, Node.js 16+, spaCy xx_ent_wiki_sm, Docker Compose 5 services (postgres/backend/frontend/nginx/photon), all 4 required env vars
- [x] 2.8 Write Key Technical Patterns (keep current + add photon geocoder pattern)
- [x] 2.9 Write Documentation Update Rules (keep verbatim)
- [x] 2.10 Write Critical Pitfalls reorganized into 8 categories — all 27 items preserved: LLM Integration (6), Data Encoding (2), Database/Schema (5), Macro/Financial (4), Geocoding (1), Auth/Access (1), Oracle (2), Ingestion (3)
- [x] 2.11 Write Debugging + General Rules + Domain Concepts (keep verbatim)
- [x] 2.12 Write Infrastructure Reference section (5 lines): server facts + link to docs/runbooks/production.md + 6-workflow list

## 3. Verify

- [x] 3.1 Count lines: `wc -l CLAUDE.md` — must be ≤210
- [x] 3.2 Check no stale data: `grep -n "2700\|2445\|19+" CLAUDE.md` — must return no matches
- [x] 3.3 Count Critical Pitfalls: verify all 27 are present under their 8 category headers
- [x] 3.4 Verify docs/runbooks/production.md exists and is linked from CLAUDE.md
- [x] 3.5 Verify all 9 config files are listed in Configuration Files section
- [x] 3.6 Verify eval_fast and eval_slow markers appear in Testing section
