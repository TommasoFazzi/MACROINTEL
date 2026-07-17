# HITL Context

## Purpose
Human-in-the-Loop (HITL) interface for reviewing, correcting, and approving LLM-generated intelligence reports. Provides Streamlit utilities for the multi-page dashboard application.

## Architecture Role
Quality assurance layer between LLM report generation and final publication. Enables human analysts to review reports, provide corrections, and submit feedback that improves future LLM prompts. Phase 5 of the intelligence pipeline.

## Key Files

- `streamlit_utils.py` - Shared utilities for Streamlit MPA
  - `get_db_manager()` - Cached singleton DatabaseManager (prevents connection pool exhaustion)
  - `get_embedding_model()` - Cached SentenceTransformer for embedding queries
  - `load_ticker_whitelist()` - Load tickers from `config/top_50_tickers.yaml`
  - `detect_tickers_in_text(text)` - Find ticker mentions with word boundaries
  - Custom CSS styling functions
  - Path setup helpers for multi-page app

- `dashboard_legacy.py` - Original single-page dashboard (~26k lines)
  - Legacy implementation before multi-page architecture
  - Contains report review, feedback submission, statistics
  - Deprecated in favor of `pages/` structure

## Dependencies

- **Internal**: `src/storage/database`, `src/utils/logger`
- **External**:
  - `streamlit` - Web dashboard framework
  - `sentence-transformers` - Embedding model for queries
  - `pyyaml` - Config loading

## Data Flow

- **Input**:
  - Generated reports from `reports/` directory
  - User corrections and feedback
  - Search queries for RAG

- **Output**:
  - `report_feedback` table - Human corrections, ratings, comments
  - Updated report status (draft → reviewed → published)
  - Prompt improvement suggestions based on feedback patterns

## Related Files

- `Home.py` - Streamlit home page (project root)
- `pages/` - Multi-page dashboard structure:
  - `1_Daily_Briefing.py` - Report editor with split view
  - `3_Intelligence_Scores.py` - Intelligence scores dashboard
    - Full scoring breakdown table (price, SMA200, PE, valuation)
    - Filtri: days, min_score, signal type, data quality
    - Export CSV, score distribution chart
  - `4_Oracle_Admin.py` - Oracle 2.0 monitoring: active sessions, tool usage, latency percentiles (reads `oracle_query_log`)
  - `5_Clustering_Shadow_Comparison.py` - Phase 1E Decision-22 dashboard: consumes `narrative_run_metrics.shadow_partitions` (migration 046) to compare the 4 shadow clustering partitions
  - Note: `2_The_Oracle.py` (Oracle 1.0 RAG interface) was removed with the Oracle 2.0 migration
- `scripts/run_dashboard.sh` - Dashboard launcher
