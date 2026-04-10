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
  - **Macro Indicators** — 30 active indicators (stored daily):
    - FRED daily: US_10Y_YIELD, US_2Y_YIELD, YIELD_CURVE_10Y_2Y, REAL_RATE_10Y, BREAKEVEN_10Y, INFLATION_EXPECTATION_5Y, US_HY_SPREAD
    - FRED weekly: FIN_STRESS_INDEX
    - FRED monthly (structural context): NICKEL, US_CPI, US_UNEMPLOYMENT, US_INDUSTRIAL_PROD, CASS_FREIGHT_INDEX
    - yfinance daily futures: BRENT_OIL, WTI_OIL, GOLD, COPPER, SILVER, NATURAL_GAS, URANIUM, ALUMINUM (ALI=F), WHEAT (ZW=F)
    - yfinance equity/indices: SP500, NASDAQ, VIX
    - yfinance FX: EUR_USD, USD_JPY, DOLLAR_INDEX, USD_GBP (GBPUSD=X), USD_CNY (CNYUSD=X), USD_CNH (⚠️ restricted)
    - yfinance crypto: BITCOIN
    - **Removed**: TED_SPREAD (LIBOR→SOFR degraded), EPU_GLOBAL (4-6w lag), USD_RUB (bimodal post-sanctions)
    - **Fixed (Phase 1)**: ALUMINUM and WHEAT switched from FRED monthly to daily CME futures; USD_GBP and USD_CNY switched from FRED to yfinance daily
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
  - **Class-level constants**:
    - `FRED_SERIES_FREQUENCY` — maps FRED series ID to frequency (daily/weekly/monthly)
    - `MAX_STALENESS_BY_FREQUENCY` — max acceptable gap per frequency
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
  - **MACRO_INDICATORS `fetch_category` field**: each of the 38 indicators has a `fetch_category` key:
    - `equity_etf` — NYSE-listed (SP500, VIX, NASDAQ, URA)
    - `commodities` — CME futures that follow NYSE holidays (Oil, Gold, Copper, Gas, Silver)
    - `fred` — Federal Reserve data (available every weekday regardless of holidays)
    - `fx` — Forex 24/5 (EUR/USD, DXY, RUB, CNH; unaffected by NYSE holidays)
    - `crypto` — Always available (BTC)

## Dependencies

- **Internal**: `src/storage/database`, `src/utils/logger`
- **External**:
  - `yfinance` (0.2.66+) - Yahoo Finance with curl_cffi
  - `openbb` (v4+) - OpenBB unified API
  - `pandas` - Data manipulation
  - `pandas-market-calendars` (>=4.3) - NYSE holiday calendar
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
