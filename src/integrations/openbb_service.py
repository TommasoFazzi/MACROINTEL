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
            'symbol': 'URA',
            'unit': 'USD',
            'category': 'COMMODITIES',
            'description': 'Global X Uranium ETF (Nuclear energy proxy)',
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
                            config['unit'], config['category']
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
                            config['unit'], config['category']
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
    }

    MAX_STALENESS_BY_FREQUENCY = {
        'daily':   2,
        'weekly':  10,
        'monthly': 45,
        '24_7':    1,
    }

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
                self._upsert_indicator_metadata(
                    key=key, frequency=frequency, last_updated=None,
                    last_source='fred', is_stale=True, staleness_days=None,
                    fetch_attempted=True, fetch_succeeded=False,
                )
                return None

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

            # Staleness check against the real data date
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
                            config['unit'], config['category']
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

    def get_macro_context_text(self, target_date: Optional[date] = None) -> str:
        """
        Format macro indicators for LLM prompt injection.

        Returns formatted text block with indicators, day-over-day changes, delta_type annotation,
        and data freshness context.

        Args:
            target_date: Date to get context for (default: today)

        Returns:
            Formatted text for LLM prompt
        """
        target_date = target_date or date.today()
        indicators = self._get_macro_indicators(target_date)

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

        category_emojis = {
            'RATES': '',
            'CREDIT_RISK': '',
            'INFLATION': '',
            'SHIPPING': '',
            'COMMODITIES': '',
            'FX': '',
            'VOLATILITY': '',
            'INDICES': ''
        }

        # Add freshness header per category
        freshness_by_category = {
            'RATES': 'FRED daily: current',
            'CREDIT_RISK': 'FRED daily: current',
            'INFLATION': 'NICKEL: Feb 2026 (structural); others: current',
            'SHIPPING': 'Cass Freight: monthly structural',
            'COMMODITIES': 'Daily CME futures: current',
            'FX': 'yfinance: current',
            'VOLATILITY': 'VIX daily: current',
            'INDICES': 'Daily: current'
        }

        for category in ['RATES', 'CREDIT_RISK', 'INFLATION', 'SHIPPING', 'COMMODITIES', 'FX', 'VOLATILITY', 'INDICES']:
            if category in by_category:
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

    def _has_macro_data(self, target_date: date) -> bool:
        """Check if macro data exists for date."""
        try:
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COUNT(*) FROM macro_indicators WHERE date = %s",
                        (target_date,)
                    )
                    count = cur.fetchone()[0]
                    return count >= 3  # At least 3 indicators
        except Exception as e:
            logger.debug(f"Error checking macro data: {e}")
            return False

    def _save_macro_indicator(
        self,
        target_date: date,
        key: str,
        value: float,
        unit: str,
        category: str
    ) -> bool:
        """Save macro indicator with upsert, populating previous_value inline."""
        try:
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO macro_indicators (date, indicator_key, value, unit, category, previous_value)
                        VALUES (%s, %s, %s, %s, %s,
                            (SELECT value FROM macro_indicators
                             WHERE indicator_key = %s AND date < %s
                             ORDER BY date DESC LIMIT 1))
                        ON CONFLICT (date, indicator_key)
                        DO UPDATE SET
                            value = EXCLUDED.value,
                            previous_value = EXCLUDED.previous_value,
                            updated_at = NOW()
                    """, (target_date, key, value, unit, category, key, target_date))
                    return True
        except Exception as e:
            logger.error(f"Error saving macro indicator: {e}")
            return False

    def _get_macro_indicators(self, target_date: date) -> List[Dict[str, Any]]:
        """Get all macro indicators for date."""
        try:
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT indicator_key, value, unit, category
                        FROM macro_indicators
                        WHERE date = %s
                        ORDER BY category, indicator_key
                    """, (target_date,))

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
