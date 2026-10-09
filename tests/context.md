# Tests Context

## Purpose
Pytest test suite for validating all core modules of the INTELLIGENCE_ITA system. Provides unit tests, integration tests, and end-to-end tests for the intelligence pipeline.

## Architecture Role
Quality assurance layer ensuring code correctness before deployment. Tests are organized by module to mirror the `src/` structure. Uses pytest fixtures for shared test setup.

## Key Files

### Configuration
- `conftest.py` - Pytest fixtures and configuration
  - Database test fixtures (mock or real)
  - Sample article fixtures
  - Embedding model fixtures

- `README.md` - Test documentation and running instructions

### Test Modules

- `test_ingestion/` - Feed parsing, content extraction, and async pipeline tests
  - Feed parser tests (RSS parsing, fallback scraping, async `_fetch_and_parse_feed`)
  - Content extractor tests (Trafilatura, Newspaper3k, concurrent batch extraction)
  - Deduplication tests (hash-based)
  - Pipeline integration tests (mock `_parse_all_feeds_async` / `_extract_batch_async` with `AsyncMock`)

- `test_nlp/` - NLP processing tests
  - Text cleaning tests
  - Chunking tests (sentence boundaries, overlap)
  - Entity extraction tests (NER)
  - Embedding generation tests

- `test_storage/` - Database operations tests
  - Connection pool tests
  - Article save/retrieve tests
  - Semantic search tests
  - Content hash deduplication tests

- `test_llm/` - LLM integration tests
  - Query analyzer tests (filter extraction)
  - Report generator tests (structure validation)
  - Query expansion tests
  - Macro-first degraded reasons (`test_macro_first_degraded.py`)

- `test_scripts/` - Pipeline script tests
  - Degraded exit path: `generate_report.py` exit 3, `daily_pipeline.py` continues and ends red

- `test_finance/` - Financial scoring tests
  - Technical penalty calculation
  - Fundamental score calculation
  - Intelligence score integration

- `test_hitl/` - HITL dashboard tests
  - Streamlit utility tests

- `test_e2e/` - End-to-end integration tests
  - Full pipeline tests (ingestion → report)

### Standalone Tests
- `test_setup.py` - Environment setup verification
- `test_sprint2_full.py` - Sprint 2 integration tests
- `test_openbb_service.py` - OpenBB API integration tests

## Dependencies

- **Internal**: All `src/` modules
- **External**:
  - `pytest` - Test framework
  - `pytest-cov` - Coverage reporting
  - `pytest-mock` - Mocking utilities
  - `pytest-asyncio` - Async test support
  - `unittest.mock.AsyncMock` - Mocking async methods

## Data Flow

- **Input**:
  - Mock data from fixtures
  - Sample articles for testing
  - Test database (can be separate from production)

- **Output**:
  - Test results (pass/fail)
  - Coverage reports

## Running Tests

```bash
# All tests
pytest

# Specific module
pytest tests/test_ingestion/

# With coverage
pytest --cov=src --cov-report=html

# Verbose output
pytest -v

# Specific test file
pytest tests/test_llm/test_query_analyzer.py
```
