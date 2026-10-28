# Utils Context

## Purpose
Shared utility functions used across the entire INTELLIGENCE_ITA system. Provides centralized logging configuration and domain-specific query preprocessing for semantic search optimization.

## Architecture Role
Foundation layer that supports all other modules. The logger ensures consistent logging format across the pipeline, while the stopwords module improves RAG retrieval accuracy by filtering out intelligence-domain noise from user queries.

## Key Files

- `logger.py` - Centralized logging configuration
  - `setup_logger(name, log_level)` - Creates configured logger with console output
  - `get_logger(name)` - Retrieves or creates logger instance
  - Format: `YYYY-MM-DD HH:MM:SS - name - LEVEL - message`
  - Respects `LOG_LEVEL` environment variable (default: INFO)

- `run_summary.py` - Warnings that must reach the GitHub Actions run page
  - `report_run_warning(title, message)` - Always `logger.warning`; when `GITHUB_STEP_SUMMARY` is set (inherited by `daily_pipeline.py`'s child processes) also appends one markdown line to that file. Never raises
  - Why not `::warning`: `daily_pipeline.py` captures child output and re-logs it with a prefix, so workflow commands printed by a step never reach the runner
  - Used by ingestion selection (`src/ingestion/pipeline.py`) for the 24h fallback and failed link inserts

- `ingestion_logger.py` - `IngestionRunStats`, per-run funnel/timing counters for ingestion (incl. `already_attempted`, `over_cap`, `scraped_too_old`); `log_report()` at the end of the run

- `stopwords.py` - Intelligence domain query cleaning
  - `INTELLIGENCE_STOPWORDS` - Set of domain terms to filter (e.g., "report", "briefing", "analysis")
  - `PRESERVE_TERMS` - Critical terms to keep (e.g., "nato", "cyber", "sanctions")
  - `QueryCleaner` class - Uses spaCy NER to preserve named entities while removing noise
  - `clean_query(query)` - Main function to clean user queries for semantic search
  - Example: `"latest intelligence report Taiwan"` → `"Taiwan"`

## Dependencies

- **Internal**: None (base layer)
- **External**:
  - `logging` (stdlib)
  - `spacy` - For NER-based entity preservation in query cleaning
  - `xx_ent_wiki_sm` - spaCy multilingual NER model

## Data Flow

- **Input**:
  - Logger name and optional log level
  - Raw user search queries
- **Output**:
  - Configured `logging.Logger` instances
  - Cleaned queries with stopwords removed but entities preserved
