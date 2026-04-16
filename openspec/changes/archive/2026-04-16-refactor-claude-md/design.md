## Context

CLAUDE.md is the primary context file loaded into every Claude Code session for this project. At 423 lines it has grown beyond optimal size through two patterns: (1) documentation drift — content that belonged in per-module `context.md` files was also added here; (2) ops accretion — server commands and GitHub Actions references accumulated over 6 months of infrastructure work.

The result: ~120 lines of duplicated architecture prose (already in 17 context.md files), 147 lines of ops runbook content, and multiple stale data points. The 27 Critical Pitfalls — the most valuable section — are buried and hard to scan.

Codebase audit (2026-04-15):
- `report_generator.py`: 3385 lines (CLAUDE.md claims ~2700)
- `database.py`: 2708 lines (CLAUDE.md claims ~2445)
- Migrations: 42 SQL files (CLAUDE.md claims "19+")
- Config files: 9 total (CLAUDE.md lists 3)
- Required env vars: 4 core (CLAUDE.md lists 2)
- GitHub Actions: 6 workflows (CLAUDE.md lists 3)
- Pytest markers: 6 including `eval_fast`/`eval_slow` (CLAUDE.md lists 4)

## Goals / Non-Goals

**Goals:**
- Reduce CLAUDE.md to ≤210 lines (-51%) by removing derivable and duplicated content
- Fix all stale data (line counts, migration count, config files, env vars, workflows, markers)
- Reorganize Critical Pitfalls into 8 categories for faster scanning — all 27 preserved
- Create `docs/runbooks/production.md` as the dedicated home for ops commands
- Apply April 2026 best practices: only include what Claude cannot derive from reading code

**Non-Goals:**
- Modifying any of the 17 context.md files
- Modifying README.md or docs/architecture/*.md
- Changing any source code, tests, or CI workflows
- Adding new architectural documentation (covered by existing docs/architecture/)

## Decisions

### Decision 1: Move infrastructure ops to a separate runbook, not inline links

**Choice:** Create `docs/runbooks/production.md` with full Docker Compose, SSH, DB, and Nginx commands. CLAUDE.md references it with a 5-line pointer containing only the most frequently needed facts (server type, deploy path, env file path, workflow list).

**Why over alternatives:**
- *Alternative A — Keep inline in CLAUDE.md:* 147 lines of ops is ~35% of the file. These commands are never needed during coding tasks — they spike cost every coding session for zero benefit.
- *Alternative B — Delete entirely:* Commands are critical for on-call/deploy work and would need to be re-derived from docker-compose.yml each time.
- *Chosen approach:* Separate file preserves full fidelity, eliminates per-session token cost, and establishes a clear separation: CLAUDE.md = coding context; runbooks = ops context.

### Decision 2: Keep all 27 Critical Pitfalls, reorganize into categories

**Choice:** Preserve all 27 items verbatim, grouped under 8 category headers (LLM Integration, Data Encoding, Database/Schema, Macro/Financial, Geocoding, Auth/Access, Oracle, Ingestion).

**Why over alternatives:**
- *Alternative A — Compress each pitfall to one line:* Too much information is lost. Each pitfall encodes a production incident; the 2-3 lines per item contain precise parameter names and thresholds that matter.
- *Alternative B — Move some to context.md files:* The pitfalls cross module boundaries (e.g., the UTF-8 fix touches both database.py and narrative_processor.py). CLAUDE.md is the right home for cross-cutting issues.
- *Chosen approach:* Categories add ~30 lines of whitespace/headers but turn a flat list of 27 into a navigable reference. A developer working on ingestion goes directly to "Ingestion" — no need to scan 27 items.

### Decision 3: Replace "Key Modules" prose with a Module Index table

**Choice:** A concise table listing module file paths, corrected line counts, and the path to each module's context.md — replacing 19 lines of prose that duplicated context.md content.

**Why:** The table gives Claude the two most useful facts (where complexity lives, where to read detail) in half the space. Line counts are the most actionable signal for "how much to read before making a change." Prose descriptions duplicate what context.md files say at greater length and accuracy.

### Decision 4: Strip derivable commands from "Common Commands"

**Choice:** Remove all commands Claude can infer from standard conventions (`npm run dev`, `pytest tests/ -v`, `black src/`) or that are documented in package.json and requirements-dev.txt. Keep only non-obvious commands: pipeline step sequence and ordering, HITL dashboard (`streamlit run Home.py`), report generation flags (`--macro-first`, `--skip-article-signals`), and the new `eval_fast`/`eval_slow` markers.

**Why:** 71 lines → 23 lines. Claude knows standard pytest patterns. The value of CLAUDE.md is in what Claude *doesn't* know — the specific script names in order, the Streamlit entry point, the flags that control report generation behavior.

## Risks / Trade-offs

**[Risk] Runbook becomes stale faster when separated** → Mitigation: The runbook is a pure command reference. Docker Compose commands change only when services change; SSH/DB access commands are nearly static. Lower change frequency than CLAUDE.md. Add a note in CLAUDE.md pointing to the runbook file so it's discoverable.

**[Risk] Missing a Critical Pitfall during reorganization** → Mitigation: Verification step explicitly counts all 27 items after rewrite using `grep -c "^\- \*\*"` against the original. The plan file catalogs all 27 by category for cross-reference.

**[Risk] New stale data introduced immediately after the refactor** → Mitigation: The Module Index table uses `~NNNN lines` approximations, not exact counts — same pattern as current CLAUDE.md. These are stable at the ±50 line level and will be updated by the normal post-task documentation protocol.

## Migration Plan

1. Create `docs/runbooks/` directory (does not currently exist)
2. Write `docs/runbooks/production.md` by extracting CLAUDE.md lines 277-423 with corrections
3. Rewrite CLAUDE.md top-to-bottom with the new structure
4. Verify: line count ≤210, all 27 pitfalls present, no stale data (`grep "2700\|2445\|19+"`)
5. Commit both files together as a single atomic change

**Rollback:** `git revert` of the commit. No runtime state affected — this is documentation only.

## Open Questions

_(none — all decisions resolved via codebase audit and best practices research)_
