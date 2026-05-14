#!/usr/bin/env python3
"""
OpenBB v4+ Integration for Financial Intelligence

Replaces MarketDataService (yfinance) with OpenBB unified API.

Features:
- Macro indicators (FRED, Yahoo): US 10Y, VIX, Brent Oil, EUR/USD
- Shipping data: Baltic Dry Index (BDI), container rates
- Equity prices: OHLCV quotes
- Company fundamentals: P/E, debt ratios, margins (cached 7 days)

Usage:
    from src.integrations.openbb_service import OpenBBMarketService

    service = OpenBBMarketService()
    service.ensure_daily_macro_data()  # Fetch and store macro indicators

    macro_text = service.get_macro_context_text(date.today())
    # Inject into LLM prompt
"""

import os
import time
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
from decimal import Decimal, InvalidOperation

from dotenv import load_dotenv
from pathlib import Path

from ..storage.database import DatabaseManager
from ..utils.logger import get_logger

# Load environment variables - explicitly find .env file
# Try multiple locations
env_paths = [
    Path(__file__).parent.parent.parent / '.env',  # INTELLIGENCE_ITA/.env
    Path.cwd() / '.env',
    Path.home() / '.env'
]
for env_path in env_paths:
    if env_path.exists():
        load_dotenv(env_path)
        break
else:
    load_dotenv()  # Default behavior

logger = get_logger(__name__)


def configure_openbb_credentials():
    """
    Configure OpenBB with API keys from environment variables.

    Supported providers:
    - FRED_API_KEY: Federal Reserve Economic Data (free)
    - FMP_API_KEY: Financial Modeling Prep (free tier available)
    - INTRINIO_API_KEY: Intrinio (premium)
    - POLYGON_API_KEY: Polygon.io (premium)
    """
    try:
        from openbb import obb

        configured = []

        # FRED API Key (most important for macro data)
        fred_key = os.getenv('FRED_API_KEY')
        logger.debug(f"FRED_API_KEY from env: {fred_key[:8]}..." if fred_key and len(fred_key) > 8 else f"FRED_API_KEY: {fred_key}")

        if fred_key and fred_key != 'your_fred_api_key_here':
            # Method 1: Set via obb.user.credentials
            try:
                obb.user.credentials.fred_api_key = fred_key
                configured.append('FRED')
            except AttributeError:
                pass

            # Method 2: Also set environment variable for OpenBB auto-detection
            os.environ['OPENBB_FRED_API_KEY'] = fred_key

        # FMP API Key (optional)
        fmp_key = os.getenv('FMP_API_KEY')
        if fmp_key and fmp_key != 'your_fmp_api_key_here':
            try:
                obb.user.credentials.fmp_api_key = fmp_key
                configured.append('FMP')
            except AttributeError:
                pass
            os.environ['OPENBB_FMP_API_KEY'] = fmp_key

        # Intrinio API Key (optional)
        intrinio_key = os.getenv('INTRINIO_API_KEY')
        if intrinio_key and intrinio_key != 'your_intrinio_api_key_here':
            try:
                obb.user.credentials.intrinio_api_key = intrinio_key
                configured.append('INTRINIO')
            except AttributeError:
                pass
            os.environ['OPENBB_INTRINIO_API_KEY'] = intrinio_key

        # Polygon API Key (optional)
        polygon_key = os.getenv('POLYGON_API_KEY')
        if polygon_key and polygon_key != 'your_polygon_api_key_here':
            try:
                obb.user.credentials.polygon_api_key = polygon_key
                configured.append('POLYGON')
            except AttributeError:
                pass
            os.environ['OPENBB_POLYGON_API_KEY'] = polygon_key

        if configured:
            logger.info(f"  API keys configured: {', '.join(configured)}")
        else:
            logger.warning("  No API keys configured - FRED data will not be available")

        return len(configured) > 0

    except Exception as e:
        logger.warning(f"Failed to configure OpenBB credentials: {e}")
        return False

# Lazy import OpenBB to handle missing dependency gracefully
_obb = None

def get_obb():
    """Lazy load OpenBB to avoid import errors if not installed."""
    global _obb
    if _obb is None:
        try:
            # Granular install: openbb-core, openbb-economy, openbb-equity
            from openbb import obb
            _obb = obb
            logger.info("OpenBB SDK loaded successfully (granular install)")

            # Configure API credentials from environment
            configure_openbb_credentials()

        except ImportError:
            try:
                # Alternative import for older versions
                from openbb_core.app.model.obbject import OBBject
                from openbb import obb
                _obb = obb
                logger.info("OpenBB SDK loaded (legacy import)")
                configure_openbb_credentials()
            except ImportError:
                logger.warning("OpenBB not installed. Install with: pip install openbb-core openbb-economy openbb-equity openbb-yfinance openbb-fred")
                _obb = False
    return _obb if _obb else None


class OpenBBMarketService:
    """
    OpenBB v4+ integration for Financial Intelligence.

    Moduli utilizzati:
    - obb.economy: Macro indicators (FRED, OECD)
    - obb.economy.shipping: Supply chain stress (BDI) - if available
    - obb.equity.price: Quote OHLCV
    - obb.equity.fundamental: Balance sheets, ratios

    Replaces MarketDataService (yfinance).
    """

    # Standard macro indicators to fetch
    # fetch_category controls behavior on NYSE holidays (weekday, market closed):
    #   'equity_etf'  — NYSE-listed equities and ETFs (SP500, VIX, URA, BDRY)
    #   'commodities' — Futures markets that follow NYSE holiday schedule (CME)
    #   'fred'        — Federal Reserve data series; available every weekday regardless of holidays
    #   'fx'          — Forex markets open 24/5 (Mon-Fri); unaffected by NYSE holidays
    #   'crypto'      — Always available (24/7)
    # On 'holiday' days, yfinance already returns the last available close via
    # ticker.history(period='5d'), so no special handling is needed for data retrieval.
    # The field is used for logging and future selective-fetch logic.
    MACRO_INDICATORS = {
        'US_10Y_YIELD': {
            'fred_series': 'DGS10',
            'symbol': '^TNX',  # CBOE 10-Year Treasury Note Yield (fallback)
            'unit': '%',
            'category': 'RATES',
            'description': 'US Treasury 10-Year Yield',
            'fetch_category': 'fred',
        },
        'US_2Y_YIELD': {
            'fred_series': 'DGS2',
            # No Yahoo symbol - FRED only (futures price != yield)
            'unit': '%',
            'category': 'RATES',
            'description': 'US Treasury 2-Year Yield',
            'fetch_category': 'fred',
        },
        'VIX': {
            'symbol': '^VIX',
            'unit': 'Points',
            'category': 'VOLATILITY',
            'description': 'CBOE Volatility Index',
            'fetch_category': 'equity_etf',
        },
        'BRENT_OIL': {
            'symbol': 'BZ=F',
            'unit': 'USD',
            'category': 'COMMODITIES',
            'description': 'Brent Crude Oil',
            'fetch_category': 'commodities',
        },
        'WTI_OIL': {
            'symbol': 'CL=F',
            'unit': 'USD',
            'category': 'COMMODITIES',
            'description': 'WTI Crude Oil',
            'fetch_category': 'commodities',
        },
        'GOLD': {
            'symbol': 'GC=F',
            'unit': 'USD',
            'category': 'COMMODITIES',
            'description': 'Gold Futures',
            'fetch_category': 'commodities',
        },
        'EUR_USD': {
            'symbol': 'EURUSD=X',
            'unit': 'Rate',
            'category': 'FX',
            'description': 'EUR/USD Exchange Rate',
            'fetch_category': 'fx',
        },
        'USD_JPY': {
            'symbol': 'JPY=X',
            'unit': 'Rate',
            'category': 'FX',
            'description': 'USD/JPY Exchange Rate',
            'fetch_category': 'fx',
        },
        'SP500': {
            'symbol': '^GSPC',
            'unit': 'Points',
            'category': 'INDICES',
            'description': 'S&P 500 Index',
            'fetch_category': 'equity_etf',
        },
        # --- CURVA DEI RENDIMENTI ---
        'YIELD_CURVE_10Y_2Y': {
            'fred_series': 'T10Y2Y',
            'unit': '%',
            'category': 'RATES',
            'description': '10Y-2Y Spread (Recession Indicator)',
            'fetch_category': 'fred',
        },
        # --- RISCHIO CREDITO ---
        'US_HY_SPREAD': {
            'fred_series': 'BAMLH0A0HYM2',
            'unit': '%',
            'category': 'CREDIT_RISK',
            'description': 'High Yield Option-Adjusted Spread',
            'fetch_category': 'fred',
        },
        # --- ECONOMIA REALE ---
        'COPPER': {
            'symbol': 'HG=F',
            'unit': 'USD',
            'category': 'COMMODITIES',
            'description': 'Copper Futures (Global Growth Proxy)',
            'fetch_category': 'commodities',
        },
        # --- ASPETTATIVE INFLAZIONE ---
        'INFLATION_EXPECTATION_5Y': {
            'fred_series': 'T5YIFR',
            'unit': '%',
            'category': 'INFLATION',
            'description': '5-Year Forward Inflation Expectation',
            'fetch_category': 'fred',
        },
        # --- FOREX ---
        'DOLLAR_INDEX': {
            'symbol': 'DX-Y.NYB',
            'unit': 'Points',
            'category': 'FX',
            'description': 'US Dollar Index (DXY)',
            'fetch_category': 'fx',
        },
        # --- SHIPPING / LOGISTICS ---
        'CASS_FREIGHT_INDEX': {
            'fred_series': 'FRGSHPUSM649NCIS',
            'unit': 'Index',
            'category': 'SHIPPING',
            'description': 'Cass Freight Shipments Index (US Logistics)',
            'fetch_category': 'fred',
        },
        # ================================================================
        # EXPANSION: 19 additional geopolitically relevant indicators
        # ================================================================
        # --- EXCHANGE RATES (yfinance daily — preferred over FRED monthly) ---
        'USD_CNY': {
            'symbol': 'CNYUSD=X',
            'unit': 'Rate',
            'category': 'FX',
            'description': 'USD/CNY Exchange Rate (China trade proxy)',
            'fetch_category': 'fx',
        },
        'USD_GBP': {
            'symbol': 'GBPUSD=X',
            'unit': 'Rate',
            'category': 'FX',
            'description': 'USD/GBP Exchange Rate',
            'fetch_category': 'fx',
        },
        # USD_RUB removed: bimodal post-sanctions, data unreliable
        # --- STRATEGIC COMMODITIES ---
        'NATURAL_GAS': {
            'symbol': 'NG=F',
            'unit': 'USD',
            'category': 'COMMODITIES',
            'description': 'Natural Gas Futures (Energy security proxy)',
            'fetch_category': 'commodities',
        },
        'WHEAT': {
            'symbol': 'ZW=F',
            'unit': 'USD',
            'category': 'COMMODITIES',
            'description': 'Wheat Futures (Food security indicator)',
            'fetch_category': 'commodities',
        },
        'NICKEL': {
            'fred_series': 'PNICKUSDM',
            'unit': 'USD',
            'category': 'COMMODITIES',
            'description': 'Nickel Price — FRED monthly, ~2mo lag (EV battery / critical minerals)',
            'fetch_category': 'fred',
        },
        'ALUMINUM': {
            'symbol': 'ALI=F',
            'unit': 'USD',
            'category': 'COMMODITIES',
            'description': 'Aluminum Futures — daily CME (Industrial / defense production)',
            'fetch_category': 'commodities',
        },
        'SILVER': {
            'symbol': 'SI=F',
            'unit': 'USD',
            'category': 'COMMODITIES',
            'description': 'Silver Futures (Industrial + safe haven)',
            'fetch_category': 'commodities',
        },
        'URANIUM': {
            'symbol': 'SRUUF',
            'unit': 'USD',
            'category': 'COMMODITIES',
            'description': 'Sprott Physical Uranium Trust (U₃O₈ spot price proxy)',
            'fetch_category': 'equity_etf',
        },
        # TED_SPREAD removed: LIBOR→SOFR transition 2023, series degraded
        # --- CREDIT RISK / FINANCIAL STRESS ---
        'FIN_STRESS_INDEX': {
            'fred_series': 'STLFSI4',
            'unit': 'Index',
            'category': 'CREDIT_RISK',
            'description': 'St. Louis Financial Stress Index',
            'fetch_category': 'fred',
        },
        # --- REAL ECONOMY ---
        'US_CPI': {
            'fred_series': 'CPIAUCSL',
            'unit': 'Index',
            'category': 'INFLATION',
            'description': 'US Consumer Price Index (All Items)',
            'fetch_category': 'fred',
        },
        'US_UNEMPLOYMENT': {
            'fred_series': 'UNRATE',
            'unit': '%',
            'category': 'ECONOMY',
            'description': 'US Unemployment Rate',
            'fetch_category': 'fred',
        },
        'US_INDUSTRIAL_PROD': {
            'fred_series': 'INDPRO',
            'unit': 'Index',
            'category': 'ECONOMY',
            'description': 'US Industrial Production Index',
            'fetch_category': 'fred',
        },
        # --- INFLATION EXPECTATIONS ---
        'BREAKEVEN_10Y': {
            'fred_series': 'T10YIE',
            'unit': '%',
            'category': 'INFLATION',
            'description': '10-Year Breakeven Inflation Rate',
            'fetch_category': 'fred',
        },
        'REAL_RATE_10Y': {
            'fred_series': 'DFII10',
            'unit': '%',
            'category': 'RATES',
            'description': '10-Year Real Interest Rate (TIPS)',
            'fetch_category': 'fred',
        },
        # EPU_GLOBAL removed: 4-6 week lag, not actionable daily
        # --- ADDITIONAL INDICES ---
        'NASDAQ': {
            'symbol': '^IXIC',
            'unit': 'Points',
            'category': 'INDICES',
            'description': 'NASDAQ Composite Index',
            'fetch_category': 'equity_etf',
        },
        # --- CRYPTO (RISK PROXY) ---
        'BITCOIN': {
            'symbol': 'BTC-USD',
            'unit': 'USD',
            'category': 'CRYPTO',
            'description': 'Bitcoin (Risk appetite / de-dollarization proxy)',
            'fetch_category': 'crypto',
        },
        # --- OFFSHORE YUAN ---
        'USD_CNH': {
            'symbol': 'USDCNH=X',
            'unit': 'Rate',
            'category': 'FX',
            'description': 'USD/CNH Exchange Rate (Offshore Yuan — free market rate)',
            'fetch_category': 'fx',
        },
        # --- EUROPEAN ENERGY ---
        'TTF_GAS': {
            'symbol': 'TTF=F',
            'unit': 'EUR',
            'category': 'COMMODITIES',
            'description': 'TTF Natural Gas Futures (European energy benchmark, EUR/MWh)',
            'fetch_category': 'commodities',
        },
        # --- YIELD CURVE (FED NY RECESSION INDICATOR) ---
        'YIELD_CURVE_10Y_3M': {
            'fred_series': 'T10Y3M',
            'unit': '%',
            'category': 'RATES',
            'description': '10Y-3M Spread (Fed NY recession probability indicator)',
            'fetch_category': 'fred',
        },
        # CFETS_RMB: deferred to B3 — no public API found (PBOC weekly, scraping non-goal)

        # ================================================================
        # ROMANIA VERTICAL — 8 indicators (country_code='RO')
        # ================================================================
        'EUR_RON': {
            'symbol': 'EURRON=X',
            'unit': 'Rate',
            'category': 'FX',
            'description': 'EUR/RON Exchange Rate (Romania)',
            'fetch_category': 'fx',
            'country_code': 'RO',
        },
        'BNR_RATE': {
            'unit': '%',
            'category': 'RATES',
            'description': 'Romania BNR Policy Rate ufficiale (Trading Economics, fallback OECD IRSTCI)',
            'fetch_category': 'trading_economics',
            'te_path': 'romania/interest-rate',
            'te_value_type': 'policy_rate',
            'oecd_measure': 'IRSTCI',
            'country_code': 'RO',
            'frequency': 'irregular',
        },
        'ROBOR_3M': {
            'unit': '%',
            'category': 'RATES',
            'description': 'Romania ROBOR 3M interbank rate (cursbnr.ro, daily)',
            'fetch_category': 'cursbnr',
            'country_code': 'RO',
            'frequency': 'daily',
        },
        'RO_CPI_YOY': {
            'fred_series': 'CP0000ROM086NEST',
            'unit': '%',
            'category': 'INFLATION',
            'description': 'Romania CPI YoY (Trading Economics, 1-2d lag; fallback FRED HICP)',
            'fetch_category': 'trading_economics',
            'te_path': 'romania/inflation-cpi',
            'te_value_type': 'cpi_yoy',
            'country_code': 'RO',
            'frequency': 'monthly',
        },
        'RO_10Y_YIELD': {
            'unit': '%',
            'category': 'RATES',
            'description': 'Romania 10Y Gov Bond Yield (TVC:RO10Y via TradingView, daily; fallback OECD IRLT)',
            'fetch_category': 'tradingview',
            'tv_symbol': 'RO10Y',
            'tv_exchange': 'TVC',
            'oecd_measure': 'IRLT',
            'country_code': 'RO',
            'frequency': 'daily',
        },
        'RO_10Y_DE_SPREAD': {
            'unit': 'bps',
            'category': 'RISK',
            'description': 'Romania 10Y spread vs Germania — rischio sovrano (TVC:RO10Y-DE10Y, daily; fallback OECD)',
            'fetch_category': 'derived_tradingview',
            'country_code': 'RO',
            'frequency': 'daily',
        },
        'RO_CDS_5Y': {
            'unit': 'bps',
            'category': 'RISK',
            'description': 'Romania CDS 5Y — rischio sovrano (WGB, richiede JS rendering)',
            'fetch_category': 'wgb_cds',
            'country_code': 'RO',
            'frequency': 'daily',
        },
        'RO_DEFICIT_GDP': {
            'unit': '% of GDP',
            'category': 'FISCAL',
            'description': 'Romania Fiscal Balance (% of GDP, Eurostat gov_10dd_edpt1)',
            'fetch_category': 'eurostat',
            'eurostat_dataset': 'gov_10dd_edpt1',
            'country_code': 'RO',
            'frequency': 'annual',
        },
        'BET_INDEX': {
            'unit': 'points',
            'category': 'EQUITY',
            'description': 'BET Index — Bursa de Valori Bucuresti (Stooq)',
            'fetch_category': 'stooq',
            'stooq_symbol': '^bet',
            'country_code': 'RO',
            'frequency': 'daily',
        },
    }

    def __init__(self, db: Optional[DatabaseManager] = None):
        """
        Initialize OpenBB market service.

        Args:
            db: DatabaseManager instance (creates new if None)
        """
        self.db = db or DatabaseManager()
        self._cache: Dict[str, Any] = {}
        self._cache_ttl = timedelta(hours=1)

        logger.info("OpenBBMarketService initialized")

    # ========================================================================
    # 1. MACRO CONTEXT (obb.economy + obb.equity.price)
    # ========================================================================

    def ensure_daily_macro_data(self, target_date: Optional[date] = None) -> bool:
        """
        Fetch and store macro indicators if missing for target_date.

        Uses a hybrid approach:
        1. Try OpenBB first (with yfinance provider)
        2. Fall back to direct yfinance for failed indicators

        Should be called BEFORE generate_report() each morning.

        Args:
            target_date: Date to fetch data for (default: today)

        Returns:
            True if data available (fetched or cached), False on error
        """
        target_date = target_date or date.today()

        # Weekend skip: markets are closed Sat/Sun — no new equity/commodity data to fetch.
        # The report step uses the most recent weekday record via get_macro_context_text fallback.
        if target_date.weekday() >= 5:
            logger.info(f"Skipping macro fetch for {target_date} (weekend — markets closed)")
            return True

        # Check if already have data
        if self._has_macro_data(target_date):
            logger.info(f"Macro data already present for {target_date}")
            return True

        # Holiday detection: log when fetching on a US market holiday (weekday, NYSE closed).
        # yfinance already returns last available close via history(period='5d') on holidays,
        # so equity/commodity data is collected with acceptable 1-2 day staleness.
        # FRED and FX indicators are unaffected by NYSE holidays.
        try:
            from src.integrations.market_calendar import fetch_mode, last_nyse_trading_day
            _fetch_mode = fetch_mode(target_date)
            if _fetch_mode == 'holiday':
                _last_trading = last_nyse_trading_day(before=target_date)
                logger.warning(
                    f"NYSE holiday on {target_date} — FRED/FX/crypto unaffected; "
                    f"equity/commodity data will reflect last trading day "
                    f"({_last_trading}) via yfinance history fallback"
                )
        except ImportError:
            pass  # pandas_market_calendars not installed — skip holiday check

        logger.info(f"Fetching macro data for {target_date}...")

        success_count = 0
        error_count = 0
        failed_indicators = []

        obb = get_obb()

        for key, config in self.MACRO_INDICATORS.items():
            value = None

            try:
                # For FRED series (rates, spreads): use OpenBB FRED with fixed date extraction
                # For symbols (commodities, FX, indices): use yfinance directly for real-time data
                if 'fred_series' in config:
                    # FRED data — use fixed method that extracts real data_date from FRED,
                    # not target_date (avoids NICKEL/monthly mislabeling bug)
                    result = self._fetch_indicator_openbb_fixed(config['fred_series'], target_date)
                    if result is not None:
                        value, data_date, frequency = result
                        self._save_macro_indicator(
                            data_date, key, value,           # data_date, not target_date
                            config['unit'], config['category'],
                            country_code=config.get('country_code', 'US'),
                        )
                        self._upsert_indicator_metadata(
                            key=key,
                            frequency=frequency,
                            last_updated=data_date,
                            last_source='fred',
                            is_stale=False,
                            staleness_days=(target_date - data_date).days,
                            fetch_attempted=True,
                            fetch_succeeded=True,
                        )
                        success_count += 1
                        logger.debug(f"  {key}: {value} (data_date={data_date})")
                    else:
                        failed_indicators.append(key)
                        error_count += 1
                elif 'symbol' in config:
                    # Market quotes - prefer yfinance direct for fresh real-time data
                    value = self._fetch_indicator_yfinance(config['symbol'])
                    # Fallback to OpenBB if yfinance fails
                    if value is None and obb:
                        value = self._fetch_indicator_openbb(obb, key, config, target_date)

                    if value is not None:
                        self._save_macro_indicator(
                            target_date, key, value,
                            config['unit'], config['category'],
                            country_code=config.get('country_code', 'US'),
                        )
                        frequency = config.get('frequency', 'daily')
                        self._upsert_indicator_metadata(
                            key=key,
                            frequency=frequency,
                            last_updated=target_date,
                            last_source='yfinance',
                            is_stale=False,
                            staleness_days=0,
                            fetch_attempted=True,
                            fetch_succeeded=True,
                        )
                        success_count += 1
                        logger.debug(f"  {key}: {value}")
                    else:
                        self._upsert_indicator_metadata(
                            key=key,
                            frequency=config.get('frequency', 'daily'),
                            last_updated=None,
                            last_source='yfinance',
                            is_stale=True,
                            staleness_days=None,
                            fetch_attempted=True,
                            fetch_succeeded=False,
                        )
                        failed_indicators.append(key)
                        error_count += 1

                # Rate limiting
                time.sleep(0.2)

            except Exception as e:
                logger.debug(f"Error fetching {key}: {e}")
                failed_indicators.append(key)
                error_count += 1

        if failed_indicators:
            logger.debug(f"Failed indicators: {', '.join(failed_indicators)}")

        if success_count > 0:
            logger.info(f"Macro data saved: {success_count} indicators, {error_count} errors")
            return True
        else:
            logger.error("Failed to fetch any macro data")
            return False

    def _fetch_indicator_openbb(
        self,
        obb,
        key: str,
        config: Dict[str, Any],
        target_date: date
    ) -> Optional[float]:
        """Fetch single indicator using OpenBB."""
        try:
            # FRED series (rates) - use economy.fred_series
            if 'fred_series' in config:
                try:
                    logger.debug(f"Fetching FRED series: {config['fred_series']}")
                    # Use 90-day window to capture monthly indicators (e.g., Cass Freight)
                    result = obb.economy.fred_series(
                        symbol=config['fred_series'],
                        start_date=(target_date - timedelta(days=90)).isoformat(),
                        end_date=target_date.isoformat(),
                        provider='fred'
                    )
                    if result.results:
                        last_item = result.results[-1]
                        value = None

                        # OpenBB FRED uses the series name as attribute (e.g., DGS10=4.19)
                        fred_series = config['fred_series']
                        if hasattr(last_item, fred_series):
                            value = getattr(last_item, fred_series)
                        else:
                            # Fallback: try common attribute names
                            for attr in ['value', 'close', 'data', 'y']:
                                if hasattr(last_item, attr):
                                    value = getattr(last_item, attr)
                                    break

                        if value is not None:
                            logger.debug(f"FRED value for {key}: {value}")
                            return float(value)
                        else:
                            logger.debug(f"FRED item attrs: {[a for a in dir(last_item) if not a.startswith('_')]}")
                    logger.debug(f"FRED returned empty/no-value results for {key}")
                except Exception as e:
                    logger.warning(f"OpenBB FRED failed for {key}: {type(e).__name__}: {e}")

            # Equity/commodity/forex quotes - use equity.price.quote with yfinance
            elif 'symbol' in config:
                try:
                    result = obb.equity.price.quote(
                        symbol=config['symbol'],
                        provider='yfinance'
                    )
                    if result.results:
                        r = result.results[0]
                        # Try different price fields
                        price = getattr(r, 'last_price', None) or \
                                getattr(r, 'price', None) or \
                                getattr(r, 'regular_market_price', None) or \
                                getattr(r, 'prev_close', None)
                        if price:
                            return float(price)
                except Exception as e:
                    logger.debug(f"OpenBB quote failed for {key}: {e}")

            return None

        except Exception as e:
            logger.debug(f"OpenBB fetch error for {key}: {e}")
            return None

    def _fetch_indicator_yfinance(self, symbol: str) -> Optional[float]:
        """Fetch single indicator using yfinance directly (real-time when available)."""
        try:
            import yfinance as yf

            ticker = yf.Ticker(symbol)

            # Try real-time price first via fast_info (uses attribute access, not dict)
            try:
                fi = ticker.fast_info
                # Try multiple attributes in order of preference
                for attr in ['last_price', 'lastPrice', 'regularMarketPrice', 'previous_close']:
                    if hasattr(fi, attr):
                        value = getattr(fi, attr)
                        if value is not None and value > 0:
                            logger.debug(f"Real-time yfinance value for {symbol}: {value}")
                            return float(value)
            except Exception:
                pass

            # Fallback to historical close
            hist = ticker.history(period='5d')
            if not hist.empty:
                value = float(hist['Close'].iloc[-1])
                logger.debug(f"Historical yfinance value for {symbol}: {value}")
                return value

            return None

        except Exception as e:
            logger.debug(f"yfinance fetch failed for {symbol}: {e}")
            return None

    # =========================================================================
    # FRED SERIES FREQUENCY MAP — used by _fetch_indicator_openbb_fixed()
    # =========================================================================
    FRED_SERIES_FREQUENCY = {
        'DGS10':             'daily',
        'DGS2':              'daily',
        'T10Y2Y':            'daily',
        'T10Y3M':            'daily',
        'DFII10':            'daily',
        'T10YIE':            'daily',
        'T5YIFR':            'daily',
        'BAMLH0A0HYM2':      'daily',
        'STLFSI4':           'weekly',
        'PNICKUSDM':         'monthly',
        'CPIAUCSL':          'monthly',
        'UNRATE':            'monthly',
        'INDPRO':            'monthly',
        'FRGSHPUSM649NCIS':  'monthly',
        # Romania
        'CP0000ROM086NEST':  'monthly',
    }

    MAX_STALENESS_BY_FREQUENCY = {
        'daily':   2,
        'weekly':  10,
        'monthly': 75,  # FRED monthly lag can reach 60d+ (Cass Freight, Nickel)
        '24_7':    1,
    }

    def _fetch_fred_direct(
        self,
        fred_series: str,
        target_date: date,
        key: str,
        frequency: str,
    ) -> Optional[tuple]:
        """Fetch FRED series via direct REST API — fallback when OpenBB is unavailable."""
        import requests, os
        from datetime import date as date_type
        api_key = os.environ.get("FRED_API_KEY", "")
        if not api_key:
            logger.warning(f"[FRED direct] FRED_API_KEY not set — cannot fetch {fred_series}")
            return None
        start = str(target_date - timedelta(days=180))
        url = (
            f"https://api.stlouisfed.org/fred/series/observations"
            f"?series_id={fred_series}&api_key={api_key}&file_type=json"
            f"&observation_start={start}&observation_end={target_date}"
            f"&sort_order=desc&limit=5"
        )
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            obs = [o for o in resp.json().get("observations", []) if o.get("value") != "."]
            if not obs:
                logger.warning(f"[FRED direct] {fred_series}: no observations")
                return None
            latest = obs[0]
            value = float(latest["value"])
            data_date = date_type.fromisoformat(latest["date"])
            staleness_days = (target_date - data_date).days
            max_staleness = self.MAX_STALENESS_BY_FREQUENCY.get(frequency, 75)
            if staleness_days > max_staleness:
                logger.warning(f"[FRED direct] {fred_series}: {staleness_days}d old, exceeds {max_staleness}d limit")
                return None
            logger.info(f"[FRED direct] {fred_series}: {value} (data_date={data_date})")
            return value, data_date, frequency
        except Exception as e:
            logger.error(f"[FRED direct] {fred_series} fetch failed: {e}")
            return None

    def _fetch_fred_hicp_yoy(self, fred_series: str, target_date: date) -> Optional[tuple]:
        """Fetch FRED HICP index series and compute YoY % change.

        Used for RO_CPI_YOY: CP0000ROM086NEST is an index (not % directly).
        YoY = (index_month / index_month_12m_ago - 1) * 100.
        """
        import requests, os
        from datetime import date as date_type
        api_key = os.environ.get("FRED_API_KEY", "")
        if not api_key:
            logger.warning(f"[HICP YoY] FRED_API_KEY not set")
            return None

        start = str(target_date - timedelta(days=450))
        url = (
            f"https://api.stlouisfed.org/fred/series/observations"
            f"?series_id={fred_series}&api_key={api_key}&file_type=json"
            f"&observation_start={start}&observation_end={target_date}"
            f"&sort_order=desc&limit=25"
        )
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            obs = [o for o in resp.json().get("observations", []) if o.get("value") != "."]
            if len(obs) < 13:
                logger.warning(f"[HICP YoY] {fred_series}: only {len(obs)} obs, need 13 for YoY")
                return None

            # obs[0] = most recent month, obs[12] = same month 1y ago (exact YoY)
            latest_val = float(obs[0]["value"])
            prev_val = float(obs[12]["value"])
            yoy = round((latest_val / prev_val - 1) * 100, 2)
            data_date = date_type.fromisoformat(obs[0]["date"])
            staleness = (target_date - data_date).days

            if staleness > 75:
                logger.warning(f"[HICP YoY] {fred_series}: {staleness}d stale, skipping")
                return None

            logger.info(f"[HICP YoY] {fred_series}: index={latest_val} YoY={yoy}% (data_date={data_date})")
            return yoy, data_date, 'monthly'
        except Exception as e:
            logger.error(f"[HICP YoY] {fred_series} failed: {e}")
            return None

    def _fetch_oecd_mei_fin(self, measure: str, target_date: date, country: str = 'ROU') -> Optional[tuple]:
        """Fetch interest rate indicator from OECD MEI_FIN dataset (SDMX-JSON v2).

        measure:  'IRLT'   → long-term 10Y government bond yield
                  'IRSTCI' → short-term overnight rate
                  'IR3TIB' → 3-month interbank rate
        country:  OECD 3-letter code, e.g. 'ROU' (Romania), 'DEU' (Germany)
        No API key required. Returns (value, data_date, 'monthly').
        """
        import requests, calendar
        from datetime import date as date_type

        start = str(target_date - timedelta(days=180))
        url = (
            f"https://stats.oecd.org/sdmx-json/data/MEI_FIN/{country}.M.{measure}.PA"
            f"?startTime={start}&endTime={target_date}"
        )
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            d = resp.json()

            structs = d['data']['structures'][0]
            times = structs['dimensions']['observation'][0]['values']
            areas = structs['dimensions']['series'][0]['values']
            measures = structs['dimensions']['series'][2]['values']
            units = structs['dimensions']['series'][3]['values']

            area_idx = next((i for i, v in enumerate(areas) if v['id'] == country), None)
            meas_idx = next((i for i, v in enumerate(measures) if v['id'] == measure), None)
            pa_idx = next((i for i, v in enumerate(units) if v['id'] == 'PA'), None)

            if area_idx is None or meas_idx is None:
                logger.warning(f"[OECD] {country} or {measure} not in dimension values")
                return None

            all_obs = {}
            for sk, sv in d['data']['dataSets'][0]['series'].items():
                parts = [int(x) for x in sk.split(':')]
                if parts[0] == area_idx and parts[2] == meas_idx and (pa_idx is None or parts[3] == pa_idx):
                    for t_str, obs_vals in sv.get('observations', {}).items():
                        t_idx = int(t_str)
                        if obs_vals and obs_vals[0] is not None:
                            all_obs[t_idx] = obs_vals[0]

            if not all_obs:
                logger.warning(f"[OECD] {measure} ROU: no observations found")
                return None

            latest_idx = max(all_obs.keys())
            value = float(all_obs[latest_idx])
            time_id = times[latest_idx]['id']  # '2026-02' or '2025-Q3'

            if 'Q' in time_id:
                year, q = time_id.split('-Q')
                last_month = int(q) * 3
                last_day = calendar.monthrange(int(year), last_month)[1]
                data_date = date_type(int(year), last_month, last_day)
            else:
                year, month = map(int, time_id.split('-'))
                last_day = calendar.monthrange(year, month)[1]
                data_date = date_type(year, month, last_day)

            staleness = (target_date - data_date).days
            if staleness > 120:
                logger.warning(f"[OECD] {measure} {country}: {staleness}d stale (data_date={data_date}), skipping")
                return None

            logger.info(f"[OECD] {measure} {country}: {value}% (data_date={data_date}, staleness={staleness}d)")
            return value, data_date, 'monthly'
        except Exception as e:
            logger.error(f"[OECD] {measure} ROU fetch failed: {e}")
            return None

    def _fetch_wgb_cds(self, target_date: date) -> Optional[tuple]:
        """Fetch Romania CDS 5Y from World Government Bonds using scrapling StealthyFetcher.

        WGB uses JavaScript to render data, so standard requests returns placeholder values.
        Uses scrapling StealthyFetcher (Chromium) when available; gracefully returns None if not.
        """
        import re
        from bs4 import BeautifulSoup

        url = "https://www.worldgovernmentbonds.com/country/romania/"
        html = None

        try:
            from scrapling.fetchers import StealthyFetcher
            page = StealthyFetcher.get(url, headless=True, disable_resources=True)
            html = page.content if hasattr(page, 'content') else str(page)
        except ImportError:
            logger.warning("[WGB CDS] scrapling.StealthyFetcher not available — skip CDS 5Y")
            return None
        except Exception as e:
            logger.warning(f"[WGB CDS] StealthyFetcher failed: {e}")
            return None

        if not html:
            return None

        soup = BeautifulSoup(html, 'html.parser')
        text = soup.get_text(' ')

        # WGB shows CDS as "Romania 5Y CDS: XXX bp"
        patterns = [
            r'5[\s\-]?Y(?:ear)?(?:\s+CDS)?[:\s]+(\d{2,4}(?:[.,]\d+)?)',
            r'CDS.*?(\d{2,4}(?:[.,]\d+)?)\s*(?:bp|bps|basis)',
            r'Credit\s+Default\s+Swap.*?(\d{2,4}(?:[.,]\d+)?)',
        ]
        for pattern in patterns:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                try:
                    val = float(m.group(1).replace(',', '.'))
                    if 10 < val < 5000:
                        logger.info(f"[WGB CDS] cds_5y={val} bps")
                        return val, target_date, 'daily'
                except ValueError:
                    continue

        logger.warning("[WGB CDS] could not parse CDS 5Y value from rendered page")
        return None

    def _fetch_eurostat_fiscal(self, target_date: date) -> Optional[tuple]:
        """Fetch Romania fiscal balance (% of GDP) from Eurostat REST API.

        Dataset: gov_10dd_edpt1 — government deficit/surplus
        No API key required. Returns most recent annual value.
        """
        import requests
        from datetime import date as date_type

        url = (
            "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/gov_10dd_edpt1"
            "?geo=RO&na_item=B9&unit=PC_GDP&sector=S13&format=JSON"
        )
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            time_dim = data.get('dimension', {}).get('time', {}).get('category', {}).get('index', {})
            values = data.get('value', {})

            if not time_dim or not values:
                logger.warning("[Eurostat] gov_10dd_edpt1: empty response")
                return None

            # time_dim maps year string → positional index
            sorted_years = sorted(time_dim.items(), key=lambda x: x[1], reverse=True)
            for year_str, pos in sorted_years:
                val = values.get(str(pos))
                if val is not None:
                    year = int(year_str)
                    data_date = date_type(year, 12, 31)
                    staleness = (target_date - data_date).days
                    if staleness > 550:
                        logger.warning(f"[Eurostat] fiscal data for {year} is {staleness}d old, skipping")
                        continue
                    logger.info(f"[Eurostat] RO fiscal balance={val}% GDP (year={year})")
                    return float(val), data_date, 'annual'

            logger.warning("[Eurostat] gov_10dd_edpt1: no usable values found")
            return None
        except Exception as e:
            logger.error(f"[Eurostat] fiscal fetch failed: {e}")
            return None

    def _fetch_dbnomics_daily(
        self, provider: str, dataset: str, series_code: str, target_date: date
    ) -> Optional[tuple]:
        """Fetch a daily time series from DBnomics (no API key required).

        Used for RO_10Y_YIELD and DE_10Y_YIELD (Eurostat irt_lt_mcby_d).
        Returns (value, data_date, 'daily') or None.
        """
        import requests
        from datetime import date as date_type

        url = (
            f"https://api.db.nomics.world/v22/series/{provider}/{dataset}/{series_code}"
            f"?observations=1&last_n_observations=30"
        )
        try:
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            d = resp.json()
            docs = d.get('series', {}).get('docs', [])
            if not docs:
                logger.warning(f"[DBnomics] {series_code}: no docs in response")
                return None
            doc = docs[0]
            periods = doc.get('period', [])
            values = doc.get('value', [])
            if not periods or not values:
                return None
            for period, value in zip(reversed(periods), reversed(values)):
                if value is not None:
                    data_date = date_type.fromisoformat(period)
                    staleness = (target_date - data_date).days
                    if staleness > 10:
                        logger.warning(f"[DBnomics] {series_code}: {staleness}d stale, skipping")
                        return None
                    logger.info(f"[DBnomics] {series_code}: {value} (data_date={data_date}, staleness={staleness}d)")
                    return float(value), data_date, 'daily'
            logger.warning(f"[DBnomics] {series_code}: all observations null")
            return None
        except Exception as e:
            logger.error(f"[DBnomics] {provider}/{dataset}/{series_code} failed: {e}")
            return None

    def _fetch_stooq(self, symbol: str, target_date: date) -> Optional[tuple]:
        """Fetch latest close from Stooq CSV endpoint.

        Used for BET_INDEX (^bet). No API key or extra library required.
        Returns (value, data_date, 'daily') or None.
        """
        import requests
        import csv
        from io import StringIO
        from datetime import date as date_type

        url = f"https://stooq.com/q/l/?s={symbol.lower()}&f=sd2ohlcv&h&e=csv"
        try:
            resp = requests.get(url, timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
            resp.raise_for_status()
            text = resp.text.strip()
            if not text or 'No data' in text or len(text) < 20:
                logger.warning(f"[Stooq] {symbol}: no data returned")
                return None
            reader = csv.DictReader(StringIO(text))
            rows = list(reader)
            if not rows:
                return None
            latest = rows[0]
            close = latest.get('Close') or latest.get('close')
            date_str = latest.get('Date') or latest.get('date')
            if not close or close in ('N/D', 'N/A', ''):
                logger.warning(f"[Stooq] {symbol}: close value is N/D")
                return None
            data_date = date_type.fromisoformat(date_str)
            staleness = (target_date - data_date).days
            if staleness > 7:
                logger.warning(f"[Stooq] {symbol}: {staleness}d stale, skipping")
                return None
            value = float(close)
            logger.info(f"[Stooq] {symbol}: {value} (data_date={data_date})")
            return value, data_date, 'daily'
        except Exception as e:
            logger.error(f"[Stooq] {symbol} failed: {e}")
            return None

    # Romanian month names for parsing cursbnr.ro dates
    _RO_MONTHS = {
        'Ianuarie': 1, 'Februarie': 2, 'Martie': 3, 'Aprilie': 4,
        'Mai': 5, 'Iunie': 6, 'Iulie': 7, 'August': 8,
        'Septembrie': 9, 'Octombrie': 10, 'Noiembrie': 11, 'Decembrie': 12,
    }

    def _parse_ro_date(self, date_str: str) -> Optional[date]:
        """Parse Romanian date string like '13 Mai 2026' into a date object."""
        parts = date_str.strip().split()
        if len(parts) != 3:
            return None
        try:
            day = int(parts[0])
            month = self._RO_MONTHS.get(parts[1])
            year = int(parts[2])
            if month is None:
                return None
            return date(year, month, day)
        except (ValueError, TypeError):
            return None

    def _fetch_cursbnr_robor(self, target_date: date) -> Optional[tuple]:
        """Fetch daily ROBOR 3M from cursbnr.ro HTML table.

        Source: https://www.cursbnr.ro/robor
        Table columns: Data | ROBOR 3M | Variație
        Date format: '13 Mai 2026' (Romanian month names)
        Returns (value, data_date, 'daily') or None.
        """
        import pandas as pd

        url = 'https://www.cursbnr.ro/robor'
        try:
            tables = pd.read_html(url, encoding='utf-8')
            if not tables:
                logger.warning("[cursbnr] No tables found on /robor page")
                return None

            df = tables[0]
            # Identify date and ROBOR 3M columns
            date_col = next((c for c in df.columns if 'Data' in str(c) or 'data' in str(c).lower()), None)
            rate_col = next((c for c in df.columns if 'ROBOR 3' in str(c) or '3M' in str(c)), None)

            if date_col is None or rate_col is None:
                logger.warning(f"[cursbnr] Unexpected columns: {list(df.columns)}")
                return None

            row = df.dropna(subset=[rate_col]).iloc[0]
            raw_date = str(row[date_col])
            raw_value = str(row[rate_col]).replace('%', '').replace(',', '.').strip()

            data_date = self._parse_ro_date(raw_date)
            if data_date is None:
                logger.warning(f"[cursbnr] Could not parse date: '{raw_date}'")
                return None

            value = float(raw_value)
            staleness = (target_date - data_date).days
            if staleness > 7:
                logger.warning(f"[cursbnr] ROBOR 3M: {staleness}d stale, skipping")
                return None

            logger.info(f"[cursbnr] ROBOR 3M: {value}% (data_date={data_date})")
            return value, data_date, 'daily'
        except Exception as e:
            logger.error(f"[cursbnr] ROBOR 3M fetch failed: {e}")
            return None

    def _fetch_trading_economics(self, te_path: str, value_type: str, target_date: date) -> Optional[tuple]:
        """Fetch a Romania macro indicator from Trading Economics via cloudscraper + DOM parsing.

        Parses table#calendar > tr.an-estimate-row — stable HTML structure, not editorial text.
        Each row: [release_date | GMT | event | reference | actual | previous | ...]
        Takes the last row where td[id=actual] is non-empty.

        value_type: 'policy_rate' | 'cpi_yoy'
          - policy_rate: data_date = release date (board decision date)
          - cpi_yoy:     data_date = release date (publication date of reference month)
            Both use the same DOM extraction path — value_type only controls frequency label.

        Falls back gracefully if cloudscraper not installed or table structure changes.
        Returns (value, data_date, frequency) or None.
        """
        from datetime import date as date_type

        try:
            import cloudscraper
            from bs4 import BeautifulSoup
        except ImportError as e:
            logger.warning(f"[TradingEcon] missing dependency: {e} — skipping")
            return None

        url = f"https://tradingeconomics.com/{te_path}"
        try:
            scraper = cloudscraper.create_scraper()
            resp = scraper.get(url, timeout=25)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')
        except Exception as e:
            logger.error(f"[TradingEcon] {te_path} fetch failed: {e}")
            return None

        try:
            cal_table = soup.find('table', id='calendar')
            if cal_table is None:
                logger.warning(f"[TradingEcon] {te_path}: table#calendar not found")
                return None

            rows = cal_table.find_all('tr', class_='an-estimate-row')
            if not rows:
                logger.warning(f"[TradingEcon] {te_path}: no data rows in calendar table")
                return None

            # Walk rows backwards to find the latest with a non-empty actual value
            latest_row = None
            for row in reversed(rows):
                actual_td = row.find('td', id='actual')
                if actual_td and actual_td.get_text(strip=True):
                    latest_row = row
                    break

            if latest_row is None:
                logger.warning(f"[TradingEcon] {te_path}: no row with actual value found")
                return None

            # Extract value from actual td (e.g. "10.7%" or "6.5%")
            actual_text = latest_row.find('td', id='actual').get_text(strip=True)
            value = float(actual_text.replace('%', '').replace(',', '.').strip())

            # Extract release date from first td (ISO format: "2026-05-13")
            tds = latest_row.find_all('td')
            release_date_str = tds[0].get_text(strip=True)
            data_date = date_type.fromisoformat(release_date_str)

            frequency = 'irregular' if value_type == 'policy_rate' else 'monthly'

            staleness = (target_date - data_date).days
            max_stale = self.MAX_STALENESS_BY_FREQUENCY.get(frequency, 75)
            if staleness > max_stale * 2:
                logger.warning(f"[TradingEcon] {te_path}: {staleness}d stale ({data_date}), skipping")
                return None

            logger.info(f"[TradingEcon] {te_path}: {value} (release_date={data_date}, staleness={staleness}d)")
            return value, data_date, frequency

        except Exception as e:
            logger.error(f"[TradingEcon] {te_path} parse failed: {e}")
            return None

    def _fetch_tradingview(self, symbol: str, exchange: str, target_date: date) -> Optional[tuple]:
        """Fetch latest daily close from TradingView via tvdatafeed (rongardF fork).

        Uses unauthenticated WebSocket connection (nologin).
        Falls back gracefully if library not installed.
        Returns (value, data_date, 'daily') or None.
        """
        try:
            from tvDatafeed import TvDatafeed, Interval
        except ImportError:
            logger.warning("[TradingView] tvDatafeed not installed — skipping")
            return None

        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                tv = TvDatafeed()

            df = tv.get_hist(symbol, exchange, interval=Interval.in_daily, n_bars=5)
            if df is None or df.empty:
                logger.warning(f"[TradingView] {exchange}:{symbol}: empty response")
                return None

            latest = df.iloc[-1]
            close = float(latest['close'])
            data_date = latest.name.date() if hasattr(latest.name, 'date') else target_date

            staleness = (target_date - data_date).days
            if staleness > 7:
                logger.warning(f"[TradingView] {exchange}:{symbol}: {staleness}d stale, skipping")
                return None

            logger.info(f"[TradingView] {exchange}:{symbol}: {close} (data_date={data_date})")
            return close, data_date, 'daily'
        except Exception as e:
            logger.error(f"[TradingView] {exchange}:{symbol} failed: {e}")
            return None

    def _fetch_indicator_openbb_fixed(
        self,
        fred_series: str,
        target_date: date,
    ) -> Optional[tuple]:
        """
        Corrected FRED fetch — extracts real data_date from FRED instead of
        using target_date. Fixes monthly indicator mislabeling bug (NICKEL, etc.)

        Returns
        -------
        (value: float, data_date: date, frequency: str) if data is acceptable.
        None if fetch fails, data absent, or staleness exceeds threshold.
        """
        from datetime import date as date_type
        frequency = self.FRED_SERIES_FREQUENCY.get(fred_series, 'monthly')
        max_staleness = self.MAX_STALENESS_BY_FREQUENCY[frequency]
        key = self._fred_series_to_key(fred_series)

        try:
            obb = get_obb()
            if not obb:
                # Fallback: direct FRED REST API (no OpenBB needed)
                return self._fetch_fred_direct(fred_series, target_date, key, frequency)

            result = obb.economy.fred_series(
                symbol=fred_series,
                start_date=str(target_date - timedelta(days=90)),
                end_date=str(target_date),
                provider='fred',
            )

            if not result or not result.results:
                logger.debug(f"FRED {fred_series}: no data returned")
                self._upsert_indicator_metadata(
                    key=key, frequency=frequency, last_updated=None,
                    last_source='fred', is_stale=True, staleness_days=None,
                    fetch_attempted=True, fetch_succeeded=False,
                )
                return None

            last_item = result.results[-1]

            # Extract value — OpenBB uses series name as attribute (e.g. DGS10=4.19)
            value = getattr(last_item, fred_series.lower(), None)
            if value is None:
                value = getattr(last_item, fred_series, None)
            if value is None:
                for attr in ['value', 'close', 'data', 'y']:
                    if hasattr(last_item, attr):
                        value = getattr(last_item, attr)
                        break
            if value is None:
                logger.warning(f"FRED {fred_series}: cannot extract value (attrs: {[a for a in dir(last_item) if not a.startswith('_')]})")
                return None

            # Extract REAL data date from FRED result
            data_date = getattr(last_item, 'date', None)
            if data_date is None:
                logger.warning(f"FRED {fred_series}: cannot extract date from result")
                return None
            if isinstance(data_date, str):
                data_date = date_type.fromisoformat(data_date[:10])

            # Staleness check: FRED daily indicators only publish on NYSE trading days,
            # so use last NYSE business day as reference instead of calendar days.
            # This prevents false stale flags on weekends and US federal holidays.
            if frequency == 'daily':
                try:
                    from src.integrations.market_calendar import last_nyse_trading_day
                    last_biz = last_nyse_trading_day(target_date)
                    staleness_days = max(0, (last_biz - data_date).days) if last_biz else (target_date - data_date).days
                except Exception:
                    staleness_days = (target_date - data_date).days
            else:
                staleness_days = (target_date - data_date).days

            if staleness_days > max_staleness:
                logger.warning(
                    f"FRED {fred_series}: data_date={data_date} is {staleness_days}d old "
                    f"(max={max_staleness} for {frequency}). Marked stale — not saved to macro_indicators."
                )
                self._upsert_indicator_metadata(
                    key=key, frequency=frequency, last_updated=data_date,
                    last_source='fred', is_stale=True, staleness_days=staleness_days,
                    fetch_attempted=True, fetch_succeeded=True,
                )
                return None

            logger.info(
                f"FRED {fred_series}: value={float(value):.4f} "
                f"data_date={data_date} staleness={staleness_days}d OK"
            )
            return float(value), data_date, frequency

        except Exception as e:
            logger.error(f"FRED {fred_series} fetch failed: {e}")
            self._upsert_indicator_metadata(
                key=key, frequency=frequency, last_updated=None,
                last_source='fred', is_stale=True, staleness_days=None,
                fetch_attempted=True, fetch_succeeded=False,
            )
            return None

    def _upsert_indicator_metadata(
        self,
        key: str,
        frequency: str,
        last_updated: Optional[date],
        last_source: str,
        is_stale: bool,
        staleness_days: Optional[int],
        fetch_attempted: bool,
        fetch_succeeded: bool,
    ) -> None:
        """
        Upsert data quality metadata for a macro indicator.
        Called after every fetch attempt — successful or not.
        Non-blocking: logs errors without raising.
        """
        from datetime import date as date_type
        today = date_type.today()

        indicator_config = self.MACRO_INDICATORS.get(key, {})
        expected_gap = {
            'daily': 1, 'weekly': 7, 'monthly': 35, '24_7': 1
        }.get(frequency, 1)
        reliability = indicator_config.get('reliability', 'normal')
        reliability_note = indicator_config.get('reliability_note')
        release_pattern = indicator_config.get('release_pattern')
        notes = indicator_config.get('notes')

        try:
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO macro_indicator_metadata (
                            key, expected_frequency, expected_gap_days,
                            last_updated, last_source,
                            staleness_days, is_stale,
                            last_fetch_date, fetch_attempted, fetch_succeeded,
                            reliability, reliability_note,
                            release_pattern, notes, updated_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s, NOW()
                        )
                        ON CONFLICT (key) DO UPDATE SET
                            last_updated       = EXCLUDED.last_updated,
                            last_source        = EXCLUDED.last_source,
                            staleness_days     = EXCLUDED.staleness_days,
                            is_stale           = EXCLUDED.is_stale,
                            last_fetch_date    = EXCLUDED.last_fetch_date,
                            fetch_attempted    = EXCLUDED.fetch_attempted,
                            fetch_succeeded    = EXCLUDED.fetch_succeeded,
                            updated_at         = NOW()
                    """, (
                        key, frequency, expected_gap,
                        last_updated, last_source,
                        staleness_days, is_stale,
                        today, fetch_attempted, fetch_succeeded,
                        reliability, reliability_note,
                        release_pattern, notes,
                    ))
                conn.commit()
        except Exception as e:
            logger.error(f"_upsert_indicator_metadata failed for {key}: {e}")

    def _last_date_with_fresh_data(self, key: str, before: date) -> Optional[date]:
        """
        Query macro_indicator_metadata for the most recent non-stale date for a key.

        Args:
            key: Indicator key (e.g., 'NICKEL', 'US_10Y_YIELD')
            before: Look for dates before this date (usually today)

        Returns:
            Most recent date where is_stale=FALSE, or None if never non-stale
        """
        try:
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT last_updated FROM macro_indicator_metadata
                        WHERE key = %s AND is_stale = FALSE
                          AND last_updated < %s
                        ORDER BY last_updated DESC
                        LIMIT 1
                    """, (key, before))
                    result = cur.fetchone()
                    return result[0] if result else None
        except Exception as e:
            logger.error(f"_last_date_with_fresh_data failed for {key}: {e}")
            return None

    def _fred_series_to_key(self, fred_series: str) -> str:
        """Reverse lookup: FRED series ID → MACRO_INDICATORS key."""
        for key, config in self.MACRO_INDICATORS.items():
            if config.get('fred_series') == fred_series:
                return key
        # Fallback: use the series ID itself (for removed/unknown series)
        return fred_series

    def _fetch_macro_fallback(self, target_date: date) -> bool:
        """
        Full fallback method using only yfinance.

        Args:
            target_date: Date to fetch data for

        Returns:
            True if successful, False otherwise
        """
        try:
            import yfinance as yf
            logger.info("Using yfinance-only fallback for macro data")

            success_count = 0
            for key, config in self.MACRO_INDICATORS.items():
                if 'symbol' not in config:
                    continue

                try:
                    ticker = yf.Ticker(config['symbol'])
                    hist = ticker.history(period='5d')
                    if not hist.empty:
                        value = float(hist['Close'].iloc[-1])
                        self._save_macro_indicator(
                            target_date, key, value,
                            config['unit'], config['category'],
                            country_code=config.get('country_code', 'US'),
                        )
                        success_count += 1
                        logger.debug(f"  {key}: {value}")
                    time.sleep(0.3)
                except Exception as e:
                    logger.debug(f"yfinance fallback failed for {key}: {e}")

            return success_count > 0

        except ImportError:
            logger.error("yfinance not available")
            return False

    def get_macro_context_text(self, target_date: Optional[date] = None, country_code: str = 'US') -> str:
        """
        Format macro indicators for LLM prompt injection.

        Returns formatted text block with indicators, day-over-day changes, delta_type annotation,
        and data freshness context.

        Args:
            target_date: Date to get context for (default: today)
            country_code: Filter indicators by country (default 'US' = global indicators)

        Returns:
            Formatted text for LLM prompt
        """
        from datetime import timedelta as _td
        target_date = target_date or date.today()
        indicators = self._get_macro_indicators(target_date, country_code)
        weekend_note = None

        # Weekend fallback: look back up to 5 days for most recent weekday record
        if not indicators and target_date.weekday() >= 5:
            for offset in range(1, 6):
                fallback_date = target_date - _td(days=offset)
                indicators = self._get_macro_indicators(fallback_date, country_code)
                if indicators:
                    weekend_note = (
                        f"[WEEKEND — markets closed. Data reflects last trading day: {fallback_date}. "
                        "DoD changes are from that session, not today.]"
                    )
                    logger.info(f"Weekend fallback: using macro data from {fallback_date}")
                    target_date = fallback_date  # use fallback date for DoD calculation
                    break

        if not indicators:
            return ""

        # Load metadata for all indicators (freshness, delta_type derivation)
        metadata_dict = {}
        try:
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT key, expected_frequency, is_stale, staleness_days, last_updated
                        FROM macro_indicator_metadata
                    """)
                    for row in cur.fetchall():
                        key, freq, is_stale, staleness_days, last_updated = row
                        metadata_dict[key] = {
                            'expected_frequency': freq,
                            'is_stale': is_stale,
                            'staleness_days': staleness_days,
                            'last_updated': last_updated
                        }
        except Exception as e:
            logger.warning(f"Failed to load macro_indicator_metadata: {e}")
            metadata_dict = {}

        # Get previous day for change calculation
        yesterday = target_date - timedelta(days=1)
        prev_indicators = self._get_macro_indicators(yesterday)
        prev_map = {i['indicator_key']: i['value'] for i in prev_indicators}

        def format_value(ind: Dict) -> str:
            """Format value with change indicator and delta_type annotation."""
            key = ind['indicator_key']
            value = float(ind['value'])
            unit = ind['unit'] or ''

            prev_value = prev_map.get(key)
            if prev_value and prev_value != 0:
                change = ((value - float(prev_value)) / float(prev_value)) * 100
                emoji = "" if abs(change) < 0.1 else ("" if change > 0 else "")
                change_str = f" ({emoji}{change:+.1f}%)" if abs(change) >= 0.1 else ""
            else:
                change_str = ""

            # Derive delta_type from metadata expected_frequency, NOT from gap days
            metadata = metadata_dict.get(key, {})
            freq = metadata.get('expected_frequency', 'daily')
            delta_type = {
                'daily': 'DoD',
                'weekly': 'WoW',
                'monthly': 'MoM',
                '24_7': 'DoD'
            }.get(freq, 'N/A')

            # Add freshness note for stale indicators
            freshness_note = ""
            if metadata.get('is_stale'):
                last_updated = metadata.get('last_updated')
                if last_updated:
                    freshness_note = f" [dato: {last_updated.strftime('%b %Y') if freq == 'monthly' else last_updated.strftime('%d/%m')} — contesto strutturale]"

            # Add ⚠️ for USD_CNH (restricted reliability)
            warning_marker = " ⚠️ [PBoC fixing]" if key == 'USD_CNH' else ""

            # Format based on unit
            if unit == '%':
                formatted = f"{value:.2f}%{change_str}"
            elif unit == 'USD':
                formatted = f"${value:,.2f}{change_str}"
            elif unit == 'Points':
                formatted = f"{value:,.1f}{change_str}"
            else:
                formatted = f"{value:.4f}{change_str}"

            # Add delta_type annotation and freshness/warning notes
            return f"{formatted} ({delta_type}){freshness_note}{warning_marker}"

        # Group by category
        by_category = {}
        for ind in indicators:
            cat = ind['category']
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(ind)

        # Build context text
        lines = [
            f"=== MACROECONOMIC CONTEXT ({target_date.strftime('%d/%m/%Y')}) ===",
            "(Use this data to correlate geopolitical events with market movements)",
            ""
        ]
        if weekend_note:
            lines.insert(2, weekend_note)
            lines.insert(3, "")

        category_emojis = {
            'RATES': '',
            'CREDIT_RISK': '',
            'RISK': '⚠️',
            'INFLATION': '',
            'FISCAL': '📊',
            'SHIPPING': '',
            'COMMODITIES': '',
            'FX': '',
            'VOLATILITY': '',
            'INDICES': '',
            'EQUITY': '📈',
            'CRYPTO': '',
        }

        freshness_by_category = {
            'RATES': 'FRED daily: current',
            'CREDIT_RISK': 'FRED daily: current',
            'RISK': 'daily: current',
            'INFLATION': 'NICKEL: Feb 2026 (structural); others: current',
            'FISCAL': 'Eurostat annual',
            'SHIPPING': 'Cass Freight: monthly structural',
            'COMMODITIES': 'Daily CME futures: current',
            'FX': 'yfinance: current',
            'VOLATILITY': 'VIX daily: current',
            'INDICES': 'Daily: current',
            'EQUITY': 'daily: current',
            'CRYPTO': 'daily: current',
        }

        # Priority order — any category not in this list is appended at the end
        _CATEGORY_ORDER = [
            'RATES', 'CREDIT_RISK', 'RISK', 'INFLATION', 'FISCAL',
            'SHIPPING', 'COMMODITIES', 'FX', 'VOLATILITY', 'INDICES', 'EQUITY', 'CRYPTO',
        ]
        ordered = [c for c in _CATEGORY_ORDER if c in by_category]
        remaining = sorted(c for c in by_category if c not in _CATEGORY_ORDER)
        for category in ordered + remaining:
            emoji = category_emojis.get(category, '')
            lines.append(f"{emoji} {category}: [{freshness_by_category.get(category, 'current')}]")
            for ind in by_category[category]:
                key = ind['indicator_key'].replace('_', ' ')
                lines.append(f"  - {key}: {format_value(ind)}")
            lines.append("")

        lines.extend([
            "INSTRUCTIONS:",
            "If a geopolitical event CONTRADICTS these indicators (e.g., oil crisis but stable prices),",
            "HIGHLIGHT the divergence as a strategic anomaly.",
            ""
        ])

        return "\n".join(lines)

    # ========================================================================
    # 2. EQUITY DATA (obb.equity)
    # ========================================================================

    def fetch_ticker_price(
        self,
        ticker: str,
        save_to_db: bool = True
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch OHLCV quote for ticker using OpenBB.

        Replaces MarketDataService.fetch_ticker_data (yfinance).

        Args:
            ticker: Stock ticker symbol (e.g., 'AAPL', 'TSM')
            save_to_db: Whether to save to market_data table

        Returns:
            Dictionary with price data or None on error
        """
        obb = get_obb()
        if not obb:
            return self._fetch_ticker_fallback(ticker, save_to_db)

        try:
            result = obb.equity.price.quote(symbol=ticker)

            if not result.results:
                logger.warning(f"No data found for ticker: {ticker}")
                return None

            quote = result.results[0]

            data = {
                'ticker': ticker,
                'date': date.today(),
                'open_price': Decimal(str(quote.open or 0)),
                'high_price': Decimal(str(quote.high or 0)),
                'low_price': Decimal(str(quote.low or 0)),
                'close_price': Decimal(str(quote.last_price or quote.price or 0)),
                'volume': int(quote.volume or 0),
                'source': 'openbb'
            }

            if save_to_db:
                self._save_market_data(data)

            logger.info(f"Fetched {ticker}: ${data['close_price']}")
            return data

        except Exception as e:
            logger.error(f"Error fetching price for {ticker}: {e}")
            return None

    def _fetch_ticker_fallback(
        self,
        ticker: str,
        save_to_db: bool = True
    ) -> Optional[Dict[str, Any]]:
        """Fallback to yfinance if OpenBB not available."""
        try:
            import yfinance as yf

            stock = yf.Ticker(ticker)
            hist = stock.history(period='5d')

            if hist.empty:
                return None

            latest = hist.iloc[-1]
            data = {
                'ticker': ticker,
                'date': date.today(),
                'open_price': Decimal(str(round(latest['Open'], 4))),
                'high_price': Decimal(str(round(latest['High'], 4))),
                'low_price': Decimal(str(round(latest['Low'], 4))),
                'close_price': Decimal(str(round(latest['Close'], 4))),
                'volume': int(latest['Volume']),
                'source': 'yfinance'
            }

            if save_to_db:
                self._save_market_data(data)

            return data

        except ImportError:
            logger.error("Neither OpenBB nor yfinance available")
            return None

    def fetch_fundamentals(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Fetch fundamental metrics with 7-day cache.

        Uses obb.equity.fundamental for ratios.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Dictionary with fundamental data or None on error
        """
        # Check cache
        cached = self._get_cached_fundamentals(ticker)
        if cached and cached.get('cache_expires_at'):
            # Use timezone-aware comparison (DB stores timezone-aware timestamps)
            cache_expiry = cached['cache_expires_at']
            now = datetime.now(timezone.utc)
            # Make cache_expiry timezone-aware if it isn't
            if cache_expiry.tzinfo is None:
                cache_expiry = cache_expiry.replace(tzinfo=timezone.utc)
            if cache_expiry > now:
                logger.debug(f"Fundamentals cache HIT: {ticker}")
                return cached

        obb = get_obb()
        if not obb:
            return self._fetch_fundamentals_fallback(ticker)

        try:
            # Try different OpenBB fundamental endpoints
            data = {'ticker': ticker}

            # Get company overview/profile
            try:
                profile = obb.equity.profile(symbol=ticker)
                if profile.results:
                    p = profile.results[0]
                    data.update({
                        'company_name': getattr(p, 'name', None),
                        'sector': getattr(p, 'sector', None),
                        'industry': getattr(p, 'industry', None),
                    })
            except Exception:
                pass

            # Get key metrics
            try:
                metrics = obb.equity.fundamental.metrics(symbol=ticker)
                if metrics.results:
                    m = metrics.results[0]
                    data.update({
                        'market_cap': getattr(m, 'market_cap', None),
                        'pe_ratio': self._safe_decimal(getattr(m, 'pe_ratio', None)),
                        'pb_ratio': self._safe_decimal(getattr(m, 'pb_ratio', None)),
                        'debt_to_equity': self._safe_decimal(getattr(m, 'debt_to_equity', None)),
                        'profit_margin': self._safe_decimal(getattr(m, 'profit_margin', None)),
                        'dividend_yield': self._safe_decimal(getattr(m, 'dividend_yield', None)),
                    })
            except Exception:
                pass

            # Fallback to yfinance for missing PE ratio (critical for scoring)
            if data.get('pe_ratio') is None:
                try:
                    import yfinance as yf
                    stock = yf.Ticker(ticker)
                    info = stock.info
                    pe = info.get('trailingPE') or info.get('forwardPE')
                    if pe:
                        data['pe_ratio'] = self._safe_decimal(pe)
                        logger.debug(f"Got PE ratio from yfinance fallback: {pe}")
                    # Also fill sector if missing
                    if not data.get('sector'):
                        data['sector'] = info.get('sector')
                except Exception as e:
                    logger.debug(f"yfinance PE fallback failed for {ticker}: {e}")

            data['cache_expires_at'] = datetime.now(timezone.utc) + timedelta(days=7)
            data['data_source'] = 'openbb+yfinance' if data.get('pe_ratio') else 'openbb'

            self._save_fundamentals(data)
            return data

        except Exception as e:
            logger.error(f"Error fetching fundamentals for {ticker}: {e}")
            return None

    def _fetch_fundamentals_fallback(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Fallback to yfinance for fundamentals."""
        try:
            import yfinance as yf

            stock = yf.Ticker(ticker)
            info = stock.info

            data = {
                'ticker': ticker,
                'company_name': info.get('longName'),
                'sector': info.get('sector'),
                'industry': info.get('industry'),
                'market_cap': info.get('marketCap'),
                'pe_ratio': self._safe_decimal(info.get('trailingPE')),
                'pb_ratio': self._safe_decimal(info.get('priceToBook')),
                'debt_to_equity': self._safe_decimal(info.get('debtToEquity')),
                'profit_margin': self._safe_decimal(info.get('profitMargins')),
                'dividend_yield': self._safe_decimal(info.get('dividendYield')),
                'cache_expires_at': datetime.now(timezone.utc) + timedelta(days=7),
                'data_source': 'yfinance'
            }

            self._save_fundamentals(data)
            return data

        except ImportError:
            return None

    # ========================================================================
    # 3. DATABASE OPERATIONS
    # ========================================================================

    def _has_macro_data(self, target_date: date, country_code: Optional[str] = None) -> bool:
        """Check if macro data exists for date, optionally scoped to a country_code."""
        try:
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    if country_code:
                        cur.execute(
                            "SELECT COUNT(*) FROM macro_indicators WHERE date = %s AND country_code = %s",
                            (target_date, country_code)
                        )
                        count = cur.fetchone()[0]
                        return count >= 1
                    else:
                        cur.execute(
                            "SELECT COUNT(*) FROM macro_indicators WHERE date = %s",
                            (target_date,)
                        )
                        count = cur.fetchone()[0]
                        return count >= 3
        except Exception as e:
            logger.debug(f"Error checking macro data: {e}")
            return False

    _RO_INDICATOR_KEYS = [
        "EUR_RON", "BNR_RATE", "ROBOR_3M",
        "RO_CPI_YOY", "RO_10Y_YIELD", "RO_10Y_DE_SPREAD",
        "RO_CDS_5Y", "RO_DEFICIT_GDP", "BET_INDEX",
    ]

    def fetch_ro_indicators(self, target_date: Optional[date] = None) -> bool:
        """Fetch all 8 Romania macro indicators, bypassing the global has_data check.

        Safe to call even when global US indicators already exist for today.
        Used by fetch_romania_macro.py and the Romania report pipeline.

        fetch_category dispatch:
          fx                  → yfinance symbol
          fred_hicp_yoy       → FRED index series → YoY computation
          trading_economics   → Trading Economics via cloudscraper (BNR_RATE=policy_rate, RO_CPI_YOY=cpi_yoy; fallback to oecd/fred)
          oecd                → OECD MEI_FIN SDMX-JSON (BNR_RATE fallback only)
          cursbnr             → cursbnr.ro HTML table (ROBOR_3M, daily)
          tradingview         → TVC via tvDatafeed (RO_10Y_YIELD, daily; OECD fallback)
          derived_tradingview → TVC RO10Y-DE10Y spread (RO_10Y_DE_SPREAD, daily; OECD fallback)
          wgb_cds             → World Government Bonds CDS via scrapling StealthyFetcher
          eurostat            → Eurostat REST API (fiscal balance)
        """
        target_date = target_date or date.today()
        success_count = 0

        for key in self._RO_INDICATOR_KEYS:
            config = self.MACRO_INDICATORS.get(key)
            if not config:
                logger.warning(f"[RO fetch] Unknown indicator key: {key}")
                continue

            fetch_cat = config.get('fetch_category', '')
            result = None
            source = 'unknown'

            try:
                if fetch_cat == 'fx':
                    value = self._fetch_indicator_yfinance(config['symbol'])
                    if value is not None:
                        result = (value, target_date, config.get('frequency', 'daily'))
                        source = 'yfinance'

                elif fetch_cat == 'trading_economics':
                    result = self._fetch_trading_economics(
                        config['te_path'], config['te_value_type'], target_date
                    )
                    source = 'trading_economics'
                    if result is None:
                        # Fallback: BNR_RATE → OECD IRSTCI; RO_CPI_YOY → FRED HICP
                        logger.info(f"  [RO] {key}: TE failed, using fallback")
                        if config.get('oecd_measure'):
                            result = self._fetch_oecd_mei_fin(config['oecd_measure'], target_date)
                            if result:
                                source = 'oecd_fallback'
                        elif config.get('fred_series'):
                            result = self._fetch_fred_hicp_yoy(config['fred_series'], target_date)
                            if result:
                                source = 'fred_hicp_fallback'

                elif fetch_cat == 'fred_hicp_yoy':
                    result = self._fetch_fred_hicp_yoy(config['fred_series'], target_date)
                    source = 'fred_hicp'

                elif fetch_cat == 'oecd':
                    result = self._fetch_oecd_mei_fin(config['oecd_measure'], target_date)
                    source = 'oecd'

                elif fetch_cat == 'dbnomics_daily':
                    result = self._fetch_dbnomics_daily(
                        config['dbnomics_provider'],
                        config['dbnomics_dataset'],
                        config['dbnomics_series'],
                        target_date,
                    )
                    source = 'dbnomics'

                elif fetch_cat == 'cursbnr':
                    result = self._fetch_cursbnr_robor(target_date)
                    source = 'cursbnr'

                elif fetch_cat == 'tradingview':
                    result = self._fetch_tradingview(
                        config['tv_symbol'], config['tv_exchange'], target_date
                    )
                    source = 'tradingview'
                    if result is None:
                        # Fallback to OECD monthly
                        logger.info(f"  [RO] {key}: TradingView failed, falling back to OECD")
                        result = self._fetch_oecd_mei_fin(config['oecd_measure'], target_date)
                        if result:
                            source = 'oecd_fallback'

                elif fetch_cat == 'derived_tradingview':
                    # RO_10Y_DE_SPREAD = (RO10Y - DE10Y) * 100 bps, daily via TradingView
                    ro_result = self._fetch_tradingview('RO10Y', 'TVC', target_date)
                    de_result = self._fetch_tradingview('DE10Y', 'TVC', target_date)
                    if ro_result and de_result:
                        ro_val, ro_date, _ = ro_result
                        de_val, de_date, _ = de_result
                        spread_bps = round((ro_val - de_val) * 100, 1)
                        result = (spread_bps, min(ro_date, de_date), 'daily')
                        source = 'tradingview_derived'
                    else:
                        # Fallback to OECD monthly spread
                        logger.info(f"  [RO] {key}: TradingView failed, falling back to OECD spread")
                        ro_oecd = self._fetch_oecd_mei_fin('IRLT', target_date, country='ROU')
                        de_oecd = self._fetch_oecd_mei_fin('IRLT', target_date, country='DEU')
                        if ro_oecd and de_oecd:
                            ro_val, ro_date, _ = ro_oecd
                            de_val, de_date, _ = de_oecd
                            spread_bps = round((ro_val - de_val) * 100, 1)
                            result = (spread_bps, min(ro_date, de_date), 'monthly')
                        source = 'oecd_derived_fallback'

                elif fetch_cat == 'derived_oecd_spread':
                    # Legacy — kept for backward compat; not used by current MACRO_INDICATORS
                    ro_result = self._fetch_oecd_mei_fin('IRLT', target_date, country='ROU')
                    de_result = self._fetch_oecd_mei_fin('IRLT', target_date, country='DEU')
                    if ro_result and de_result:
                        ro_val, ro_date, _ = ro_result
                        de_val, de_date, _ = de_result
                        spread_bps = round((ro_val - de_val) * 100, 1)
                        result = (spread_bps, min(ro_date, de_date), 'monthly')
                    source = 'oecd_derived'

                elif fetch_cat == 'stooq':
                    result = self._fetch_stooq(config['stooq_symbol'], target_date)
                    source = 'stooq'

                elif fetch_cat == 'wgb_cds':
                    result = self._fetch_wgb_cds(target_date)
                    source = 'wgb'

                elif fetch_cat == 'eurostat':
                    result = self._fetch_eurostat_fiscal(target_date)
                    source = 'eurostat'

                else:
                    logger.warning(f"  [RO] {key}: unknown fetch_category '{fetch_cat}'")
                    continue

                if result is not None:
                    value, data_date, frequency = result
                    self._save_macro_indicator(
                        data_date, key, value,
                        config['unit'], config['category'],
                        country_code='RO',
                    )
                    staleness = (target_date - data_date).days
                    self._upsert_indicator_metadata(
                        key=key, frequency=frequency, last_updated=data_date,
                        last_source=source, is_stale=staleness > self.MAX_STALENESS_BY_FREQUENCY.get(frequency, 75),
                        staleness_days=staleness,
                        fetch_attempted=True, fetch_succeeded=True,
                    )
                    success_count += 1
                    logger.info(f"  [RO] {key}: {value} (source={source}, data_date={data_date})")
                else:
                    logger.warning(f"  [RO] {key}: no data from {source}")
                    self._upsert_indicator_metadata(
                        key=key,
                        frequency=config.get('frequency', 'monthly'),
                        last_updated=None,
                        last_source=source,
                        is_stale=True,
                        staleness_days=None,
                        fetch_attempted=True,
                        fetch_succeeded=False,
                    )

            except Exception as e:
                logger.error(f"  [RO] {key} fetch error: {e}")

        logger.info(f"[RO fetch] {success_count}/{len(self._RO_INDICATOR_KEYS)} indicators fetched")
        return success_count > 0

    def _save_macro_indicator(
        self,
        target_date: date,
        key: str,
        value: float,
        unit: str,
        category: str,
        country_code: str = 'US',
    ) -> bool:
        """Save macro indicator with upsert, populating previous_value inline."""
        try:
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO macro_indicators
                            (date, indicator_key, value, unit, category, country_code, previous_value)
                        VALUES (%s, %s, %s, %s, %s, %s,
                            (SELECT value FROM macro_indicators
                             WHERE indicator_key = %s AND date < %s
                             ORDER BY date DESC LIMIT 1))
                        ON CONFLICT (date, indicator_key)
                        DO UPDATE SET
                            value = EXCLUDED.value,
                            country_code = EXCLUDED.country_code,
                            previous_value = EXCLUDED.previous_value,
                            updated_at = NOW()
                    """, (target_date, key, value, unit, category, country_code, key, target_date))
                    return True
        except Exception as e:
            logger.error(f"Error saving macro indicator: {e}")
            return False

    def _get_macro_indicators(self, target_date: date, country_code: str = 'US') -> List[Dict[str, Any]]:
        """Get macro indicators for date, filtered by country_code (default 'US' = global)."""
        try:
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT indicator_key, value, unit, category
                        FROM macro_indicators
                        WHERE date = %s
                          AND country_code = %s
                        ORDER BY category, indicator_key
                    """, (target_date, country_code))

                    return [
                        {
                            'indicator_key': row[0],
                            'value': row[1],
                            'unit': row[2],
                            'category': row[3]
                        }
                        for row in cur.fetchall()
                    ]
        except Exception as e:
            logger.error(f"Error getting macro indicators: {e}")
            return []

    def _save_market_data(self, data: Dict[str, Any]) -> bool:
        """Save market data to existing market_data table."""
        try:
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO market_data (
                            ticker, date,
                            open_price, high_price, low_price, close_price, volume,
                            created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                        ON CONFLICT (ticker, date) DO UPDATE SET
                            open_price = EXCLUDED.open_price,
                            high_price = EXCLUDED.high_price,
                            low_price = EXCLUDED.low_price,
                            close_price = EXCLUDED.close_price,
                            volume = EXCLUDED.volume,
                            updated_at = NOW()
                    """, (
                        data['ticker'], data['date'],
                        data['open_price'], data['high_price'],
                        data['low_price'], data['close_price'], data['volume']
                    ))
                    return True
        except Exception as e:
            logger.error(f"Error saving market data: {e}")
            return False

    def _get_cached_fundamentals(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Get cached fundamentals from database."""
        try:
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT ticker, company_name, sector, industry,
                               market_cap, pe_ratio, pb_ratio, debt_to_equity,
                               profit_margin, dividend_yield, cache_expires_at
                        FROM company_fundamentals
                        WHERE ticker = %s
                    """, (ticker,))

                    row = cur.fetchone()
                    if not row:
                        return None

                    return {
                        'ticker': row[0],
                        'company_name': row[1],
                        'sector': row[2],
                        'industry': row[3],
                        'market_cap': row[4],
                        'pe_ratio': row[5],
                        'pb_ratio': row[6],
                        'debt_to_equity': row[7],
                        'profit_margin': row[8],
                        'dividend_yield': row[9],
                        'cache_expires_at': row[10]
                    }
        except Exception as e:
            logger.debug(f"Error getting cached fundamentals: {e}")
            return None

    def _save_fundamentals(self, data: Dict[str, Any]) -> bool:
        """Save company fundamentals with upsert."""
        try:
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO company_fundamentals (
                            ticker, company_name, sector, industry,
                            market_cap, pe_ratio, pb_ratio, debt_to_equity,
                            profit_margin, dividend_yield,
                            data_source, last_updated, cache_expires_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)
                        ON CONFLICT (ticker) DO UPDATE SET
                            company_name = EXCLUDED.company_name,
                            sector = EXCLUDED.sector,
                            industry = EXCLUDED.industry,
                            market_cap = EXCLUDED.market_cap,
                            pe_ratio = EXCLUDED.pe_ratio,
                            pb_ratio = EXCLUDED.pb_ratio,
                            debt_to_equity = EXCLUDED.debt_to_equity,
                            profit_margin = EXCLUDED.profit_margin,
                            dividend_yield = EXCLUDED.dividend_yield,
                            data_source = EXCLUDED.data_source,
                            last_updated = NOW(),
                            cache_expires_at = EXCLUDED.cache_expires_at
                    """, (
                        data.get('ticker'),
                        data.get('company_name'),
                        data.get('sector'),
                        data.get('industry'),
                        data.get('market_cap'),
                        data.get('pe_ratio'),
                        data.get('pb_ratio'),
                        data.get('debt_to_equity'),
                        data.get('profit_margin'),
                        data.get('dividend_yield'),
                        data.get('data_source', 'openbb'),
                        data.get('cache_expires_at')
                    ))
                    return True
        except Exception as e:
            logger.error(f"Error saving fundamentals: {e}")
            return False

    # ========================================================================
    # 4. UTILITIES
    # ========================================================================

    @staticmethod
    def _safe_decimal(value: Any) -> Optional[Decimal]:
        """Safely convert value to Decimal."""
        if value is None:
            return None
        try:
            return Decimal(str(value))
        except (ValueError, TypeError, InvalidOperation):
            return None

    def clear_cache(self):
        """Clear in-memory cache."""
        self._cache.clear()
        logger.info("OpenBB service cache cleared")


# Standalone test
if __name__ == "__main__":
    service = OpenBBMarketService()

    print("=" * 80)
    print("Testing OpenBBMarketService")
    print("=" * 80)

    # Test macro data fetch
    print("\n1. Fetching macro data...")
    success = service.ensure_daily_macro_data()
    print(f"   Result: {'Success' if success else 'Failed'}")

    # Test macro context text
    print("\n2. Generating macro context text...")
    context = service.get_macro_context_text()
    if context:
        print(context[:500] + "..." if len(context) > 500 else context)
    else:
        print("   No macro context available")

    # Test ticker price
    print("\n3. Fetching AAPL price...")
    price_data = service.fetch_ticker_price("AAPL", save_to_db=False)
    if price_data:
        print(f"   AAPL: ${price_data['close_price']}")
    else:
        print("   Failed to fetch AAPL")

    print("\n" + "=" * 80)
