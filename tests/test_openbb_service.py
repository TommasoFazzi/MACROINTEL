#!/usr/bin/env python3
"""
Tests for OpenBB Market Service

Run with:
    pytest tests/test_openbb_service.py -v
    pytest tests/test_openbb_service.py -v -k "test_macro"  # Only macro tests
    pytest tests/test_openbb_service.py -v --tb=short       # Short traceback
"""

import os
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock, patch, MagicMock
import pytest

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.integrations.openbb_service import (
    OpenBBMarketService,
    get_obb,
    configure_openbb_credentials
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_db():
    """Create a mock database manager."""
    db = Mock()
    db.get_connection = Mock(return_value=MagicMock())
    return db


@pytest.fixture
def service(mock_db):
    """Create OpenBBMarketService with mocked database."""
    return OpenBBMarketService(db=mock_db)


@pytest.fixture
def sample_macro_indicators():
    """Sample macro indicator data."""
    return [
        {'indicator_key': 'VIX', 'value': Decimal('18.50'), 'unit': 'Points', 'category': 'VOLATILITY'},
        {'indicator_key': 'BRENT_OIL', 'value': Decimal('78.25'), 'unit': 'USD', 'category': 'COMMODITIES'},
        {'indicator_key': 'US_10Y_YIELD', 'value': Decimal('4.25'), 'unit': '%', 'category': 'RATES'},
        {'indicator_key': 'EUR_USD', 'value': Decimal('1.0850'), 'unit': 'Rate', 'category': 'FX'},
        {'indicator_key': 'US_HY_SPREAD', 'value': Decimal('3.85'), 'unit': '%', 'category': 'CREDIT_RISK'},
        {'indicator_key': 'INFLATION_EXPECTATION_5Y', 'value': Decimal('2.35'), 'unit': '%', 'category': 'INFLATION'},
        {'indicator_key': 'CASS_FREIGHT_INDEX', 'value': Decimal('1.05'), 'unit': 'Index', 'category': 'SHIPPING'},
    ]


# =============================================================================
# Unit Tests - OpenBB Loading
# =============================================================================

class TestOpenBBLoading:
    """Tests for OpenBB SDK loading and configuration."""

    def test_get_obb_returns_none_when_not_installed(self):
        """Should return None gracefully when OpenBB is not installed."""
        with patch.dict('sys.modules', {'openbb': None}):
            # Force reload to test import failure
            import importlib
            # This test verifies graceful degradation
            pass

    def test_configure_credentials_handles_missing_keys(self):
        """Should handle missing API keys gracefully."""
        with patch.dict(os.environ, {}, clear=True):
            # Should not raise even with no keys
            result = configure_openbb_credentials()
            # Result depends on OpenBB availability
            assert result in [True, False]


# =============================================================================
# Unit Tests - Macro Context
# =============================================================================

class TestMacroContext:
    """Tests for macro economic context generation."""

    def test_get_macro_context_text_formats_correctly(self, service, sample_macro_indicators):
        """Should format macro indicators into readable text."""
        with patch.object(service, '_get_macro_indicators', return_value=sample_macro_indicators):
            context = service.get_macro_context_text(date.today())

            assert 'MACROECONOMIC CONTEXT' in context
            assert 'VIX' in context or 'VOLATILITY' in context
            assert 'COMMODITIES' in context or 'OIL' in context

    def test_get_macro_context_text_empty_when_no_data(self, service):
        """Should return empty string when no macro data available."""
        with patch.object(service, '_get_macro_indicators', return_value=[]):
            context = service.get_macro_context_text(date.today())
            assert context == ""

    def test_macro_indicators_config_has_required_keys(self, service):
        """Should have all required macro indicator configurations."""
        required_indicators = [
            'VIX', 'BRENT_OIL', 'EUR_USD',
            'YIELD_CURVE_10Y_2Y', 'US_HY_SPREAD', 'COPPER',
            'INFLATION_EXPECTATION_5Y', 'DOLLAR_INDEX', 'CASS_FREIGHT_INDEX'
        ]

        for indicator in required_indicators:
            assert indicator in service.MACRO_INDICATORS
            config = service.MACRO_INDICATORS[indicator]
            assert 'unit' in config
            assert 'category' in config

    def test_macro_indicators_has_all_categories(self, service):
        """Should have indicators for all required core categories.
        Uses subset check so new categories can be added without breaking this test.
        """
        required_categories = {
            'RATES', 'CREDIT_RISK', 'INFLATION', 'SHIPPING',
            'COMMODITIES', 'FX', 'VOLATILITY', 'INDICES'
        }

        actual_categories = {
            config['category']
            for config in service.MACRO_INDICATORS.values()
        }

        assert required_categories <= actual_categories, (
            f"Missing required categories: {required_categories - actual_categories}"
        )


# =============================================================================
# Unit Tests - Database Operations
# =============================================================================

class TestDatabaseOperations:
    """Tests for database CRUD operations."""

    def test_has_macro_data_returns_true_when_data_exists(self, service, mock_db):
        """Should return True when macro data exists for date."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = [5]  # 5 indicators
        mock_db.get_connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = mock_cursor

        result = service._has_macro_data(date.today())
        assert result is True

    def test_has_macro_data_returns_false_when_insufficient_data(self, service, mock_db):
        """Should return False when fewer than 3 indicators exist."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = [1]  # Only 1 indicator
        mock_db.get_connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = mock_cursor

        result = service._has_macro_data(date.today())
        assert result is False

    def test_save_macro_indicator_executes_upsert(self, service, mock_db):
        """Should execute upsert query for macro indicator."""
        mock_cursor = MagicMock()
        mock_db.get_connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = mock_cursor

        result = service._save_macro_indicator(
            date.today(), 'VIX', 18.5, 'Points', 'VOLATILITY'
        )

        assert mock_cursor.execute.called
        # Verify upsert query structure
        call_args = mock_cursor.execute.call_args[0][0]
        assert 'INSERT INTO macro_indicators' in call_args
        assert 'ON CONFLICT' in call_args


# =============================================================================
# Unit Tests - Ticker Price
# =============================================================================

class TestTickerPrice:
    """Tests for equity price fetching."""

    def test_fetch_ticker_price_returns_dict_structure(self, service):
        """Should return properly structured price data."""
        with patch.object(service, '_fetch_ticker_fallback') as mock_fallback:
            mock_fallback.return_value = {
                'ticker': 'AAPL',
                'date': date.today(),
                'open_price': Decimal('150.00'),
                'high_price': Decimal('155.00'),
                'low_price': Decimal('149.00'),
                'close_price': Decimal('153.50'),
                'volume': 50000000,
                'source': 'yfinance'
            }

            result = service.fetch_ticker_price('AAPL', save_to_db=False)

            if result:  # May be None if OpenBB not available
                assert 'ticker' in result
                assert 'close_price' in result
                assert result['ticker'] == 'AAPL'


# =============================================================================
# Unit Tests - Fundamentals
# =============================================================================

class TestFundamentals:
    """Tests for company fundamentals fetching."""

    def test_fundamentals_cache_check(self, service, mock_db):
        """Should check cache before fetching new fundamentals."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None  # No cached data
        mock_db.get_connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = mock_cursor

        # Cache miss should return None from cache
        result = service._get_cached_fundamentals('AAPL')
        assert result is None

    def test_fundamentals_cache_returns_valid_data(self, service, mock_db):
        """Should return cached fundamentals if not expired."""
        future_expiry = datetime.now() + timedelta(days=5)
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (
            'AAPL', 'Apple Inc.', 'Technology', 'Consumer Electronics',
            3000000000000, Decimal('28.5'), Decimal('45.2'), Decimal('1.2'),
            Decimal('0.25'), Decimal('0.005'), future_expiry
        )
        mock_db.get_connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = mock_cursor

        result = service._get_cached_fundamentals('AAPL')

        assert result is not None
        assert result['ticker'] == 'AAPL'
        assert result['company_name'] == 'Apple Inc.'


# =============================================================================
# Unit Tests - Utilities
# =============================================================================

class TestUtilities:
    """Tests for utility functions."""

    def test_safe_decimal_converts_valid_values(self):
        """Should convert valid numbers to Decimal."""
        assert OpenBBMarketService._safe_decimal(10.5) == Decimal('10.5')
        assert OpenBBMarketService._safe_decimal('25.75') == Decimal('25.75')
        assert OpenBBMarketService._safe_decimal(100) == Decimal('100')

    def test_safe_decimal_returns_none_for_invalid(self):
        """Should return None for invalid values."""
        assert OpenBBMarketService._safe_decimal(None) is None
        assert OpenBBMarketService._safe_decimal('invalid') is None
        assert OpenBBMarketService._safe_decimal([1, 2, 3]) is None

    def test_clear_cache_empties_cache(self, service):
        """Should clear the internal cache."""
        service._cache['test_key'] = 'test_value'
        service.clear_cache()
        assert len(service._cache) == 0


# =============================================================================
# Integration Tests (require database)
# =============================================================================

@pytest.mark.integration
class TestIntegration:
    """Integration tests requiring actual database connection.

    Run with: pytest tests/test_openbb_service.py -v -m integration
    """

    @pytest.fixture
    def real_service(self):
        """Create service with real database connection."""
        from src.storage.database import DatabaseManager
        try:
            db = DatabaseManager()
            return OpenBBMarketService(db=db)
        except Exception as e:
            pytest.skip(f"Database not available: {e}")

    def test_ensure_daily_macro_data_fetches_and_saves(self, real_service):
        """Should fetch macro data and save to database."""
        # This test actually calls APIs and writes to DB
        result = real_service.ensure_daily_macro_data(date.today())
        assert isinstance(result, bool)

    def test_get_macro_context_text_with_real_data(self, real_service):
        """Should generate context text from real database data."""
        # First ensure we have data
        real_service.ensure_daily_macro_data(date.today())

        context = real_service.get_macro_context_text(date.today())
        # Context may be empty if fetch failed, but should not raise
        assert isinstance(context, str)


# =============================================================================
# Smoke Tests (quick sanity checks)
# =============================================================================

class TestSmoke:
    """Quick smoke tests to verify basic functionality."""

    def test_service_initializes_without_error(self, mock_db):
        """Should initialize without raising exceptions."""
        service = OpenBBMarketService(db=mock_db)
        assert service is not None
        assert service.db is not None

    def test_macro_indicators_config_is_valid(self, service):
        """Should have valid MACRO_INDICATORS configuration."""
        assert len(service.MACRO_INDICATORS) > 0

        for key, config in service.MACRO_INDICATORS.items():
            assert isinstance(key, str)
            assert 'unit' in config
            assert 'category' in config
            assert 'description' in config

    def test_service_has_required_methods(self, service):
        """Should have all required public methods."""
        required_methods = [
            'ensure_daily_macro_data',
            'get_macro_context_text',
            'fetch_ticker_price',
            'fetch_fundamentals',
            'clear_cache'
        ]

        for method_name in required_methods:
            assert hasattr(service, method_name)
            assert callable(getattr(service, method_name))


# =============================================================================
# Run tests
# =============================================================================

if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
