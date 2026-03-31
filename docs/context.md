# Docs Context

## Purpose
Technical documentation for the INTELLIGENCE_ITA project. Contains detailed implementation notes, architectural documentation, and guides.

## Architecture Role
Knowledge base for developers and users. Documents system design decisions, implementation details, and operational procedures. Complements inline code documentation.

## Key Files

### Implementation Details
- `HITL_FEEDBACK_LOOP.md` - Feedback loop architecture (~20k chars)
  - Feedback categories
  - Prompt improvement workflow
  - Quality metrics via Next.js Dashboard API

- `INTELLIGENCE_MAP_IMPLEMENTATION.md` - Map visualization guide
  - Mapbox GL JS setup on Next.js 14/15
  - Entity geocoding via PostGIS and Hybrid resolvers (GeoNames+LLM)
  - Interactive layers (Arcs, Pulse, Heatmap)

- `NARRATIVE_ENGINE.md` - Narrative Engine documentation
  - HDBSCAN clustering pipeline
  - Storyline matching and LLM evolution
  - Graph edge generation (Jaccard entity-overlap)
  - 3-layer content filtering (Filtro 1, 2, 4)
  - Momentum scoring and decay

### Archive
- `archive/` - Contains obsolete historical documents (Phase implementations, deprecated feature plans). **These should be ignored by the LLM unless explicitly requested.**

## Dependencies

- **Internal**: Referenced by all modules
- **External**: None (Markdown files)

## Data Flow

- **Input**: Developer knowledge, implementation decisions
- **Output**: Documentation for users and developers

## Related Documentation

- `README.md` (project root) - Main project README (authoritative)
- `CLAUDE.md` (project root) - AI assistant project guide
- `DEDUPLICATION_IMPLEMENTATION.md` (root) - 2-phase dedup strategy
- `RERANKING_IMPLEMENTATION.md` (root) - Cross-encoder reranking details
