# Ingestion Context

## Purpose
Data collection pipeline that fetches news articles from RSS/Atom feeds, extracts full-text content from URLs, and prepares data for NLP processing. This is Phase 1 of the intelligence pipeline.

## Architecture Role
Entry point for all external data. Reads feed configurations from `config/feeds.yaml`, fetches all RSS feeds in parallel via aiohttp, extracts full article text concurrently using Trafilatura/Newspaper3k/Cloudscraper, and outputs JSON files to `data/` directory for downstream processing by `src/nlp/`.

A single `asyncio.run()` in `pipeline.run()` orchestrates both feed parsing and content extraction. Sync libraries (feedparser, trafilatura, newspaper3k) are executed via `asyncio.to_thread()` to avoid blocking the event loop.

## Key Files

- `feed_parser.py` - RSS/Atom feed parsing with fallback scraping
  - `FeedParser` class - Loads feeds from YAML config
  - `parse_feed(url, name)` - Parse single feed using `feedparser` (sync)
  - `_fetch_and_parse_feed(session, url, name, category, subcategory)` - Async: fetch RSS via aiohttp, parse with `asyncio.to_thread(feedparser.parse, ...)`
  - `_scrape_fallback_async(feed_name, session)` - Async fallback scraper (cloudscraper via to_thread, aiohttp for simple gets)
  - `_parse_all_feeds_async(category)` - Async: `aiohttp.ClientSession` with `TCPConnector(limit=20, limit_per_host=3)`, launches all feeds via `asyncio.gather()`
  - `parse_all_feeds(category)` - Sync wrapper (`asyncio.run()`) for standalone use only
  - `scrape_fallback(feed_name)` - Sync BeautifulSoup fallback for broken RSS
  - `FALLBACK_SCRAPERS` - Config for sites needing HTML scraping (Defense One, CFR, CSIS, ECFR, ISS Africa)
  - `cloudscraper` support for anti-bot protected sites (403 bypass)
  - User-Agent rotation on every request to avoid blocks

- `content_extractor.py` - Full-text extraction from URLs
  - `ContentExtractor` class - Multi-method extraction with `max_concurrent=10` global semaphore
  - **Per-domain concurrency control** (Fix 3, 2026-04-09): `_domain_semaphores` dict limits max 2 concurrent requests per domain. Reduces anti-bot triggering (e.g., Chatham House rate limiting). Async method `_get_domain_semaphore(url)` lazily creates semaphores.
  - **Per-article timeout** (Fix 1, 2026-04-09): `PER_ARTICLE_TIMEOUT = 30s`. Each extraction wrapped in `asyncio.wait_for(..., timeout)` to escape indefinite hangs on Cloudflare challenges or unresponsive servers. If timeout fires → `asyncio.TimeoutError` caught, article marked failed, pipeline continues.
  - `extract_with_trafilatura(url)` - Primary method (fast, news-optimized). Note: `trafilatura.fetch_url()` called without explicit timeout; per-article timeout wrapper provides safety.
  - `extract_with_newspaper(url)` - Newspaper3k fallback. Now passes `request_timeout=self.timeout` to `NewspaperArticle` constructor.
  - `extract_with_cloudscraper(url)` - For anti-bot sites (e.g., politico.com)
  - `_extract_content_async(semaphore, article, idx, total)` - Async: acquires global semaphore → per-domain semaphore → wraps extraction with `asyncio.wait_for(..., timeout=PER_ARTICLE_TIMEOUT)` → delegates to `asyncio.to_thread(self.extract_content, url)`. Catches `asyncio.TimeoutError` separately.
  - `_extract_batch_async(articles)` - Async: concurrent extraction via `asyncio.gather()` with per-task timeouts (no global timeout on gather itself)
  - `extract_batch(articles)` - Sync wrapper (`asyncio.run()`) for standalone use only
  - `SCRAPLING_TIER1_DOMAINS` - curl_cffi WAF bypass: understandingwar.org, chathamhouse.org, kcl.ac.uk, **timesofisrael.com** (Fix 2, 2026-04-09: added for Cloudflare anti-bot)
  - Extraction strategy: Trafilatura → Newspaper3k → Cloudscraper → Scrapling Tier 1 (fallback)
  - **2-level PDF auto-detection** (integrated into RSS flow):
    - Level 1: Direct `.pdf` URL → routes to `PDFIngestor.extract_text()`
    - Level 2: Landing page scan → `_try_level2_pdf()` → `_find_pdf_link()` (think tank pattern: landing page → PDF download)
  - `_find_pdf_link(html, base_url)` - Scans HTML for `<a href="*.pdf">` with keyword-only matching (download, full report, etc.). The `len>5` fallback was removed to prevent footnote/citation PDFs from triggering.
  - `_try_level2_pdf(url, html_content, raw_html)` - Level 2 gated by domain allowlist only. If domain is in `PDF_LANDING_PAGE_DOMAINS` AND a PDF download link is found → PDF is the primary document, returned directly. If no PDF found → HTML content returned as fallback. No text-length gate: on known research domains a PDF download button always takes precedence over the HTML abstract.
  - `PDF_LANDING_PAGE_DOMAINS` - Allowlist: rand.org, ecb.europa.eu, imf.org, worldbank.org, oecd.org, brookings.edu, iiss.org, sipri.org, chathamhouse.org, rusi.org, csis.org, cfr.org, etc.
  - `_extract_pdf_content_sync(url)` - Downloads and extracts PDF text, uses same User-Agent as HTML crawler
  - Level 2 combines HTML abstract + PDF full text with `---` separator

- `pipeline.py` - Main orchestration
  - `IngestionPipeline` class - End-to-end workflow
  - `_run_async(category, extract_content, max_age_days)` - Async core: calls `_parse_all_feeds_async()` and `_extract_batch_async()` directly (no sync wrappers)
  - `run(category, max_age_days)` - Single `asyncio.run(self._run_async(...))` entry point
  - `deduplicate_by_quick_hash()` - MD5 hash(link + title) deduplication (Phase 1)
  - `get_summary()` - Statistics by category/source
  - Auto-saves JSON to `data/articles_{timestamp}.json`
  - `_BLOCKLIST_PATTERNS` - Filtro 1: regex compilate per bloccare articoli off-topic a livello di titolo.
    - **ICC escluso** dal gruppo sport: ambiguo con International Criminal Court.
    - **UFC escluso** dal gruppo sport: compare in contesti sanzionatori/geopolitici ("ex-UFC fighter + sanctions").
    - **Arsenal** non è nel pattern semplice: richiede esplicitamente "Arsenal F.C." / "Arsenal FC" per evitare falsi positivi con uso militare del termine ("arsenal of weapons", "The Arsenal as the Battlefield").
    - `_is_off_topic(title)` — restituisce il pattern che ha fatto match (per logging) o None se l'articolo va tenuto.

- **`pdf_ingestor.py`** - **PDF document ingestion** (rewritten for pymupdf4llm)
  - `PDFIngestor` class - Extracts text from PDF files as clean Markdown
  - Uses `pymupdf4llm.to_markdown()` (preferred) with fallback to raw PyMuPDF `fitz`
  - `extract_text(pdf_bytes, max_pages)` - Converts PDF bytes to Markdown via pymupdf4llm (headers, tables, no artifacts)
  - `download_pdf(url, headers)` - Downloads PDF bytes; accepts optional `headers` dict for User-Agent consistency with HTML crawler
  - `build_article_dict(...)` - Creates article dict with `is_long_document: True` and `extraction_method` field
  - `ingest_from_file(pdf_path, ...)` - Extract from local PDF file
  - `ingest_from_url_async(pdf_url, ...)` - Async download + extract from URL
  - Outputs article dicts compatible with existing NLP pipeline
  - **No longer uses `config/pdf_sources.yaml`** — PDFs enter via RSS flow (2-level auto-detection in content_extractor.py)

## Dependencies

- **Internal**: `src/utils/logger`
- **External**:
  - `aiohttp` - Async HTTP client for parallel feed fetching
  - `feedparser` - RSS/Atom parsing (sync, run via `asyncio.to_thread`)
  - `trafilatura` - News article extraction (primary, sync via `to_thread`)
  - `newspaper3k` - Fallback extraction (sync via `to_thread`)
  - `cloudscraper` - Anti-bot bypass (optional, sync via `to_thread`)
  - `beautifulsoup4` - HTML parsing for fallback scraping
  - `pyyaml` - Config loading
  - `requests` - HTTP client (sync session for standalone use)

## Data Flow

- **Input**:
  - `config/feeds.yaml` - RSS feed URLs and metadata (~33 feeds, includes think tank RSS: RAND, EveryCRSReport)
  - Live RSS/Atom feeds from web
  - Article URLs for full-text extraction

- **Output**:
  - `data/articles_{timestamp}.json` - Extracted articles with:
    - `title`, `link`, `published`, `source`, `category`, `subcategory`
    - `full_content.text` - Full article text
    - `extraction_success`, `extraction_method`
  - Statistics: total articles, by category, by source, extraction success rate
