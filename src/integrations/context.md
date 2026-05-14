# Integrations Context

## Purpose
External service wrappers for market data and financial APIs. Provides unified interfaces for Yahoo Finance (price data) and OpenBB (fundamentals, macro indicators) with caching and database persistence.

## Architecture Role
Data acquisition layer for financial intelligence. Used by `src/finance/` for trade signal scoring and by `src/llm/report_generator.py` for macro context injection. All data persisted to PostgreSQL via `src/storage/`.

## Key Files

- `market_data.py` - Yahoo Finance integration
  - `MarketDataService` class - Price and technical data
  - `fetch_ticker_data(ticker, period)` - OHLCV with derived metrics
    - 7-day volatility (std dev of daily returns)
    - Relative volume (volume / 30-day average)
  - `fetch_batch(tickers)` - Batch fetching with rate limiting
  - `fetch_with_sma200(ticker)` - Price with 200-day SMA for technical analysis
  - 1-hour in-memory cache to avoid rate limits
  - Database persistence to `market_data` table
  - Uses yfinance 0.2.66+ with curl_cffi for anti-bot protection

- `openbb_service.py` - OpenBB v4+ integration
  - `OpenBBMarketService` class - Macro and fundamentals
  - **Macro Indicators** — 34 global + 8 Romania indicators (stored daily):
    - FRED daily: US_10Y_YIELD, US_2Y_YIELD, YIELD_CURVE_10Y_2Y, YIELD_CURVE_10Y_3M (T10Y3M — Fed NY recession indicator), REAL_RATE_10Y, BREAKEVEN_10Y, INFLATION_EXPECTATION_5Y, US_HY_SPREAD
    - FRED weekly: FIN_STRESS_INDEX
    - FRED monthly (structural context): NICKEL, US_CPI, US_UNEMPLOYMENT, US_INDUSTRIAL_PROD, CASS_FREIGHT_INDEX
    - yfinance daily futures: BRENT_OIL, WTI_OIL, GOLD, COPPER, SILVER, NATURAL_GAS, TTF_GAS (TTF=F — European gas benchmark, EUR/MWh), URANIUM, ALUMINUM (ALI=F), WHEAT (ZW=F)
    - yfinance equity/indices: SP500, NASDAQ, VIX
    - yfinance FX: EUR_USD, USD_JPY, DOLLAR_INDEX, USD_GBP (GBPUSD=X), USD_CNY (CNYUSD=X), USD_CNH (⚠️ restricted)
    - yfinance crypto: BITCOIN
    - **Removed**: TED_SPREAD (LIBOR→SOFR degraded), EPU_GLOBAL (4-6w lag), USD_RUB (bimodal post-sanctions)
    - **Fixed (Phase 1)**: ALUMINUM and WHEAT switched from FRED monthly to daily CME futures; USD_GBP and USD_CNY switched from FRED to yfinance daily
    - **Added (B2)**: TTF_GAS (European energy benchmark, separato da Henry Hub NG=F) and YIELD_CURVE_10Y_3M (Fed NY recession probability indicator). CFETS_RMB deferred to B3 — no public API found.
    - **Romania vertical (9 indicators, country_code='RO')**: EUR_RON (yfinance daily), BNR_RATE (OECD MEI_FIN IRSTCI, monthly), ROBOR_3M (**cursbnr.ro HTML scrape, daily**), RO_CPI_YOY (FRED CP0000ROM086NEST HICP YoY, monthly), RO_10Y_YIELD (**TVC:RO10Y via tvDatafeed, daily**; OECD IRLT fallback), RO_10Y_DE_SPREAD (**TVC:RO10Y−DE10Y, daily, bps**; OECD fallback), RO_CDS_5Y (WGB via scrapling StealthyFetcher, graceful None), RO_DEFICIT_GDP (Eurostat gov_10dd_edpt1, annual), BET_INDEX (Stooq ^bet CSV, daily)
  - `ensure_daily_macro_data()` - Fetch and persist macro indicators
    - FRED branch now uses `_fetch_indicator_openbb_fixed()` — saves with real `data_date` (not `target_date`). Fixes NICKEL/monthly mislabeling bug.
    - All fetch paths call `_upsert_indicator_metadata()` to track staleness and reliability.
  - `get_macro_context_text(date)` - **Phase 2 enhanced**: Formatted text for LLM prompt injection with:
    - Delta_type annotation (DoD/WoW/MoM) derived from `expected_frequency` in metadata (not gap days)
    - Freshness headers per category ("NICKEL: Feb 2026 (structural)", etc.)
    - ⚠️ warning for USD_CNH (restricted reliability, PBoC fixing)
    - Loads `macro_indicator_metadata` table for staleness + frequency context
  - **New methods (Phase 1)**:
    - `_fetch_indicator_openbb_fixed(fred_series, target_date)` → `(value, data_date, frequency) | None` — extracts real FRED data date; staleness check before saving
    - `_upsert_indicator_metadata(key, frequency, last_updated, ...)` — writes to `macro_indicator_metadata` table (migration 035)
    - `_fred_series_to_key(fred_series)` — reverse lookup FRED series → MACRO_INDICATORS key
  - **New methods (Phase 2)**:
    - `_last_date_with_fresh_data(key, before)` → `Optional[date]` — queries `macro_indicator_metadata` for most recent non-stale date
  - **Phase 3 fix (migration 036)**:
    - `_save_macro_indicator()` updated to populate `previous_value` column inline via scalar subquery — no extra round-trip. ON CONFLICT also updates `previous_value`. Prerequisite for Phase 3 indicator delta calculation (`_get_macro_indicators_for_screening`).
  - **Class-level constants**:
    - `FRED_SERIES_FREQUENCY` — maps FRED series ID to frequency (daily/weekly/monthly)
    - `MAX_STALENESS_BY_FREQUENCY` — max acceptable gap per frequency: `daily=2, weekly=10, monthly=75, 24_7=1`
      - Monthly raised from 45→75 to account for FRED publication lag (Cass Freight, Nickel: up to 60d lag)
      - Daily staleness for `frequency='daily'` uses NYSE business days (not calendar days) to avoid false stale flags on weekends/US holidays. Uses `last_nyse_trading_day(target_date)` from `market_calendar.py` as reference.
  - **Company Fundamentals** (7-day cache):
    - P/E ratio, forward P/E
    - Debt/Equity ratio
    - Sector classification
    - Profit margins
  - `fetch_fundamentals(ticker)` - Cached fundamental data
  - API key configuration via environment variables:
    - `FRED_API_KEY` - Federal Reserve Economic Data
    - `FMP_API_KEY` - Financial Modeling Prep (optional)
    - `INTRINIO_API_KEY` - Intrinio (optional)

- `market_calendar.py` - NYSE holiday-aware scheduling utility
  - `is_nyse_open(target_date)` — True if NYSE is open that day (False for weekends AND US holidays)
  - `last_nyse_trading_day(before)` — most recent NYSE trading day before a given date; used as reference when fetching on a holiday
  - `fetch_mode(target_date)` — returns `'normal'` | `'holiday'` | `'skip'` (weekend)
  - Backed by `pandas_market_calendars` NYSE calendar (accurate US holiday schedule)
  - Used by `scripts/fetch_daily_market_data.py` backfill logic and `ensure_daily_macro_data()` for holiday logging
  - **MACRO_INDICATORS `fetch_category` field**: each indicator has a `fetch_category` key:
    - `equity_etf` — NYSE/OTC-listed (SP500, VIX, NASDAQ, SRUUF — Sprott Physical Uranium Trust)
    - `commodities` — CME futures that follow NYSE holidays (Oil, Gold, Copper, Gas, Silver)
    - `fred` — Federal Reserve data (available every weekday regardless of holidays)
    - `fx` — Forex 24/5 (EUR/USD, DXY, CNH; EUR_RON via yfinance)
    - `crypto` — Always available (BTC)
    - `fred_hicp_yoy` — FRED index series with YoY computation (RO_CPI_YOY via `_fetch_fred_hicp_yoy()`)
    - `oecd` — OECD MEI_FIN SDMX-JSON API via `_fetch_oecd_mei_fin(measure, country)`. No API key. BNR_RATE=IRSTCI. Accepts `country` param (ROU/DEU). Dynamic area_idx lookup.
    - `cursbnr` — cursbnr.ro HTML table via `_fetch_cursbnr_robor()`. No API key. ROBOR_3M (daily). Romanian date parsing via hardcoded `_RO_MONTHS` dict.
    - `tradingview` — TVC:RO10Y via tvDatafeed (rongardF fork, `git+https://github.com/rongardF/tvdatafeed.git`). Daily. RO_10Y_YIELD. Falls back to OECD IRLT if library unavailable or fetch fails.
    - `derived_tradingview` — TVC:RO10Y − TVC:DE10Y × 100, computed inline via `_fetch_tradingview()`. Daily, bps. RO_10Y_DE_SPREAD. Falls back to OECD derived spread.
    - `stooq` — Stooq CSV endpoint via `_fetch_stooq(symbol)`. No API key. BET_INDEX=^bet (daily).
    - `wgb_cds` — World Government Bonds CDS 5Y via scrapling StealthyFetcher; gracefully returns None if Chromium unavailable
    - `eurostat` — Eurostat REST API JSON via `_fetch_eurostat_fiscal()` (RO_DEFICIT_GDP, annual data)

## Dependencies

- **Internal**: `src/storage/database`, `src/utils/logger`
- **External**:
  - `yfinance` (0.2.66+) - Yahoo Finance with curl_cffi
  - `openbb` (v4+) - OpenBB unified API
  - `pandas` - Data manipulation (also used for `read_html` on cursbnr.ro)
  - `pandas-market-calendars` (>=4.3) - NYSE holiday calendar
  - `tvDatafeed` (`git+https://github.com/rongardF/tvdatafeed.git`) - TradingView WebSocket client (rongardF fork; not on PyPI)
  - `python-dotenv` - Environment configuration

## Data Flow

- **Input**:
  - Ticker symbols from trade signals
  - API requests with caching

- **Output**:
  - `market_data` table - OHLCV time series
  - `macro_indicators` table - Daily macro snapshots
  - `ticker_fundamentals` table - Cached fundamentals
  - In-memory cache for rate limit protection
