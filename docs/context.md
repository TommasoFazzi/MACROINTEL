# Docs Context

## Purpose
Technical documentation for the INTELLIGENCE_ITA project. Contains detailed guides for each phase of the pipeline, implementation notes, and architectural documentation.

## Architecture Role
Knowledge base for developers and users. Documents system design decisions, implementation details, and operational procedures. Complements inline code documentation.

## Key Files

### Project Overview
- `QUICKSTART.md` - Quick start guide
  - Installation steps
  - Basic usage
  - Common commands

### Phase Documentation
- `PHASE4_REPORT_GENERATION.md` - LLM report generation details (~18k chars)
  - RAG pipeline architecture
  - Query expansion strategy
  - Cross-encoder reranking
  - Trade signal extraction
  - Prompt templates

- `PHASE5_HITL.md` - Human-in-the-Loop system documentation (~20k chars)
  - Dashboard architecture
  - Feedback collection
  - Report review workflow
  - Multi-page app structure

### Implementation Details
- `HITL_FEEDBACK_LOOP.md` - Feedback loop architecture (~20k chars)
  - Feedback categories
  - Prompt improvement workflow
  - Quality metrics

- `INTELLIGENCE_MAP_IMPLEMENTATION.md` - Map visualization guide (~6k chars)
  - Mapbox setup
  - Entity geocoding
  - Frontend architecture

- `NARRATIVE_ENGINE.md` - Narrative Engine documentation
  - HDBSCAN clustering pipeline
  - Storyline matching and LLM evolution
  - Graph edge generation (Jaccard entity-overlap)
  - 3-layer content filtering (Filtro 1, 2, 4)
  - Momentum scoring and decay

## Dependencies

- **Internal**: Referenced by all modules
- **External**: None (Markdown files)

## Data Flow

- **Input**: Developer knowledge, implementation decisions
- **Output**: Documentation for users and developers

## Related Documentation

- `README.md` (project root) - Main project README (authoritative, updated 2026-03-24)
- `CLAUDE.md` (project root) - AI assistant project guide
- `DEDUPLICATION_IMPLEMENTATION.md` (root) - 2-phase dedup strategy
- `RERANKING_IMPLEMENTATION.md` (root) - Cross-encoder reranking details
- `ORACLE_2.0_DEVELOPMENT_PLAN.md` (root) - Oracle 2.0 architecture plan
