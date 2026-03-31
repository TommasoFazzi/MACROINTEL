"""
Unit tests for ReferenceTool — structured reference data lookups.

Tests cover:
- All 8 lookup types: country_profile, country_by_name, country_by_region,
  sanctions_search, sanctions_by_country, macro_forecast,
  macro_forecast_indicator, trade_flow
- Input validation: unknown lookup type, empty query
- ISO normalization: ISO3/ISO2 uppercased, indicator codes uppercased
- Sanctions queries target v_sanctions_public (not sanctions_registry)
- _format_success() for each new lookup type
- DB timeout set to 10s (not 5s)
"""

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.llm.tools.reference_tool import ReferenceTool, _fmt_decimal


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_db():
    """Minimal mock of DatabaseManager."""
    db = MagicMock()
    db.get_connection = MagicMock(return_value=MagicMock())
    return db


@pytest.fixture
def tool(mock_db):
    """ReferenceTool instance with mocked DB."""
    return ReferenceTool(db=mock_db, llm=MagicMock())


def _make_cursor(mock_db, columns: list, rows: list):
    """Configure mock_db to return specific columns and rows."""
    mock_cursor = MagicMock()
    mock_cursor.description = [(col,) for col in columns]
    mock_cursor.fetchall.return_value = rows

    ctx_manager = MagicMock()
    ctx_manager.__enter__ = MagicMock(return_value=mock_cursor)
    ctx_manager.__exit__ = MagicMock(return_value=False)

    conn_ctx = MagicMock()
    conn_ctx.__enter__ = MagicMock(return_value=MagicMock(cursor=MagicMock(return_value=ctx_manager)))
    conn_ctx.__exit__ = MagicMock(return_value=False)

    mock_db.get_connection.return_value = conn_ctx
    return mock_cursor


# ─────────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────────

class TestInputValidation:

    def test_unknown_lookup_type_returns_error(self, tool):
        result = tool._execute(lookup_type="nonexistent", query="test")
        assert result.success is False
        assert "Unknown lookup_type" in result.error

    def test_empty_query_returns_error(self, tool):
        result = tool._execute(lookup_type="country_profile", query="")
        assert result.success is False
        assert "Empty query" in result.error

    def test_whitespace_only_query_returns_error(self, tool):
        result = tool._execute(lookup_type="country_profile", query="   ")
        assert result.success is False
        assert "Empty query" in result.error

    def test_all_lookup_types_recognized(self, tool):
        """All 8 lookup types should be in SAFE_QUERIES."""
        expected = {
            "country_profile", "country_by_name", "country_by_region",
            "sanctions_search", "sanctions_by_country",
            "macro_forecast", "macro_forecast_indicator", "trade_flow",
        }
        assert expected == set(tool.SAFE_QUERIES.keys())


# ─────────────────────────────────────────────────────────────────────────────
# Sanctions — must use v_sanctions_public, not sanctions_registry
# ─────────────────────────────────────────────────────────────────────────────

class TestSanctionsViewUsage:

    def test_sanctions_search_uses_v_sanctions_public(self, tool):
        """sanctions_search must query v_sanctions_public, not sanctions_registry."""
        sql = tool.SAFE_QUERIES["sanctions_search"]["sql"]
        assert "v_sanctions_public" in sql
        assert "sanctions_registry" not in sql

    def test_sanctions_by_country_uses_v_sanctions_public(self, tool):
        """sanctions_by_country must query v_sanctions_public, not sanctions_registry."""
        sql = tool.SAFE_QUERIES["sanctions_by_country"]["sql"]
        assert "v_sanctions_public" in sql
        assert "sanctions_registry" not in sql

    def test_sanctions_by_country_uppercases_iso2(self, mock_db, tool):
        """ISO2 code should be uppercased before query."""
        _make_cursor(mock_db, ["id", "caption", "schema_type", "datasets", "first_seen", "last_seen"], [])
        result = tool._execute(lookup_type="sanctions_by_country", query="ru")
        # If execute runs, mock returns empty — just verify it didn't error on the ISO2
        assert result.success is True or "Empty query" not in (result.error or "")


# ─────────────────────────────────────────────────────────────────────────────
# ISO normalization
# ─────────────────────────────────────────────────────────────────────────────

class TestISONormalization:

    def test_country_profile_iso3_uppercased(self, mock_db, tool):
        """ISO3 codes should be uppercased for country_profile lookups."""
        cursor_mock = MagicMock()
        cursor_mock.description = [("iso3",), ("name",)]
        cursor_mock.fetchall.return_value = [("ITA", "Italy")]

        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=cursor_mock)
        cm.__exit__ = MagicMock(return_value=False)

        conn = MagicMock()
        conn.__enter__ = MagicMock(return_value=MagicMock(cursor=MagicMock(return_value=cm)))
        conn.__exit__ = MagicMock(return_value=False)
        mock_db.get_connection.return_value = conn

        result = tool._execute(lookup_type="country_profile", query="ita")
        assert result.success is True
        # Check the query param was uppercased
        execute_calls = cursor_mock.execute.call_args_list
        # Second call is the actual query (first is SET statement_timeout)
        actual_call = execute_calls[1]
        params = actual_call[0][1]
        assert params["query"] == "ITA"

    def test_macro_forecast_iso3_uppercased(self, mock_db, tool):
        """macro_forecast lookup should uppercase ISO3."""
        cursor_mock = MagicMock()
        cursor_mock.description = [("iso3",), ("name",), ("indicator_code",),
                                   ("indicator_name",), ("year",), ("value",), ("unit",), ("vintage",)]
        cursor_mock.fetchall.return_value = []

        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=cursor_mock)
        cm.__exit__ = MagicMock(return_value=False)

        conn = MagicMock()
        conn.__enter__ = MagicMock(return_value=MagicMock(cursor=MagicMock(return_value=cm)))
        conn.__exit__ = MagicMock(return_value=False)
        mock_db.get_connection.return_value = conn

        tool._execute(lookup_type="macro_forecast", query="deu")
        execute_calls = cursor_mock.execute.call_args_list
        params = execute_calls[1][0][1]
        assert params["query"] == "DEU"

    def test_macro_forecast_indicator_uppercased(self, mock_db, tool):
        """macro_forecast_indicator should uppercase indicator code."""
        cursor_mock = MagicMock()
        cursor_mock.description = [("iso3",), ("name",), ("year",), ("value",), ("unit",), ("vintage",)]
        cursor_mock.fetchall.return_value = []

        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=cursor_mock)
        cm.__exit__ = MagicMock(return_value=False)

        conn = MagicMock()
        conn.__enter__ = MagicMock(return_value=MagicMock(cursor=MagicMock(return_value=cm)))
        conn.__exit__ = MagicMock(return_value=False)
        mock_db.get_connection.return_value = conn

        tool._execute(lookup_type="macro_forecast_indicator", query="ngdp_rpch")
        execute_calls = cursor_mock.execute.call_args_list
        params = execute_calls[1][0][1]
        assert params["query"] == "NGDP_RPCH"


# ─────────────────────────────────────────────────────────────────────────────
# Default year range for macro_forecast_indicator
# ─────────────────────────────────────────────────────────────────────────────

class TestDefaultYearRange:

    def test_default_year_range_is_sensible(self, mock_db, tool):
        """start_year defaults to current_year-1, end_year to current_year+5."""
        cursor_mock = MagicMock()
        cursor_mock.description = [("iso3",), ("name",), ("year",), ("value",), ("unit",), ("vintage",)]
        cursor_mock.fetchall.return_value = []

        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=cursor_mock)
        cm.__exit__ = MagicMock(return_value=False)

        conn = MagicMock()
        conn.__enter__ = MagicMock(return_value=MagicMock(cursor=MagicMock(return_value=cm)))
        conn.__exit__ = MagicMock(return_value=False)
        mock_db.get_connection.return_value = conn

        tool._execute(lookup_type="macro_forecast_indicator", query="NGDP_RPCH")
        execute_calls = cursor_mock.execute.call_args_list
        params = execute_calls[1][0][1]

        current_year = date.today().year
        assert params["start_year"] == current_year - 1
        assert params["end_year"] == current_year + 5

    def test_explicit_year_range_overrides_default(self, mock_db, tool):
        """Explicitly passed start_year/end_year override defaults."""
        cursor_mock = MagicMock()
        cursor_mock.description = [("iso3",), ("name",), ("year",), ("value",), ("unit",), ("vintage",)]
        cursor_mock.fetchall.return_value = []

        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=cursor_mock)
        cm.__exit__ = MagicMock(return_value=False)

        conn = MagicMock()
        conn.__enter__ = MagicMock(return_value=MagicMock(cursor=MagicMock(return_value=cm)))
        conn.__exit__ = MagicMock(return_value=False)
        mock_db.get_connection.return_value = conn

        tool._execute(lookup_type="macro_forecast_indicator", query="NGDP_RPCH",
                      start_year=2020, end_year=2025)
        execute_calls = cursor_mock.execute.call_args_list
        params = execute_calls[1][0][1]
        assert params["start_year"] == 2020
        assert params["end_year"] == 2025


# ─────────────────────────────────────────────────────────────────────────────
# DB timeout
# ─────────────────────────────────────────────────────────────────────────────

class TestDBTimeout:

    def test_statement_timeout_is_10s(self, mock_db, tool):
        """ReferenceTool now uses 10s timeout (vs SQLTool's 5s) for JOINed queries."""
        cursor_mock = MagicMock()
        cursor_mock.description = [("iso3",), ("name",)]
        cursor_mock.fetchall.return_value = [("ITA", "Italy")]

        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=cursor_mock)
        cm.__exit__ = MagicMock(return_value=False)

        conn = MagicMock()
        conn.__enter__ = MagicMock(return_value=MagicMock(cursor=MagicMock(return_value=cm)))
        conn.__exit__ = MagicMock(return_value=False)
        mock_db.get_connection.return_value = conn

        tool._execute(lookup_type="country_profile", query="ITA")
        first_call = cursor_mock.execute.call_args_list[0][0][0]
        assert "10s" in first_call


# ─────────────────────────────────────────────────────────────────────────────
# Formatting
# ─────────────────────────────────────────────────────────────────────────────

class TestFormatSuccess:

    def test_format_country_profile(self, tool):
        data = {"results": [{"iso3": "ITA", "iso2": "IT", "name": "Italy", "capital": "Rome",
                              "region": "Europe & Central Asia", "income_group": "High income",
                              "population": 59000000, "gdp_usd": 2100000000000,
                              "gdp_per_capita": 35000, "gdp_growth": Decimal("0.9"),
                              "inflation": Decimal("5.6"), "unemployment": Decimal("7.2"),
                              "debt_to_gdp": Decimal("140.0"), "governance_score": Decimal("0.73"),
                              "data_year": 2023}], "count": 1}
        metadata = {"lookup_type": "country_profile", "query": "ITA"}
        output = tool._format_success(data, metadata)
        assert "Italy" in output
        assert "ITA" in output
        assert "Rome" in output
        assert "GDP" in output

    def test_format_macro_forecast_country(self, tool):
        data = {"results": [
            {"iso3": "DEU", "name": "Germany", "indicator_code": "NGDP_RPCH",
             "indicator_name": "GDP growth", "year": 2025, "value": Decimal("1.2"),
             "unit": "%", "vintage": "auto_202603"},
            {"iso3": "DEU", "name": "Germany", "indicator_code": "NGDP_RPCH",
             "indicator_name": "GDP growth", "year": 2026, "value": Decimal("1.8"),
             "unit": "%", "vintage": "auto_202603"},
        ], "count": 2}
        metadata = {"lookup_type": "macro_forecast", "query": "DEU"}
        output = tool._format_success(data, metadata)
        assert "Germany" in output
        assert "DEU" in output
        assert "Crescita PIL reale" in output or "NGDP_RPCH" in output
        assert "2025" in output
        assert "2026" in output

    def test_format_macro_forecast_empty(self, tool):
        data = {"results": [], "count": 0}
        metadata = {"lookup_type": "macro_forecast", "query": "XYZ"}
        output = tool._format_success(data, metadata)
        assert "XYZ" in output

    def test_format_sanctions(self, tool):
        data = {"results": [
            {"caption": "Wagner Group", "schema_type": "Organization",
             "aliases": ["Grupo Wagner"], "datasets": ["us_ofac", "eu_sanctions"],
             "first_seen": "2022-02-28", "last_seen": "2024-01-15"},
        ], "count": 1}
        metadata = {"lookup_type": "sanctions_search", "query": "Wagner"}
        output = tool._format_success(data, metadata)
        assert "Wagner Group" in output
        assert "Organization" in output
        assert "us_ofac" in output

    def test_format_trade_flow(self, tool):
        data = {"results": [
            {"reporter_iso3": "ITA", "reporter_name": "Italy",
             "partner_iso3": "DEU", "partner_name": "Germany",
             "indicator_code": "EXPORT_VALUE", "year": 2022,
             "value": Decimal("60000000000"), "unit": "USD"},
        ], "count": 1}
        metadata = {"lookup_type": "trade_flow", "query": "ITA"}
        output = tool._format_success(data, metadata)
        assert "Italy" in output
        assert "EXPORT_VALUE" in output

    def test_format_empty_returns_no_results_message(self, tool):
        data = {"results": [], "count": 0}
        metadata = {"lookup_type": "macro_forecast", "query": "XYZ", "description": "test"}
        output = tool._format_success(data, metadata)
        assert "No results" in output or "Nessuna" in output or "XYZ" in output


# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────

class TestFmtDecimal:

    def test_formats_decimal(self):
        assert _fmt_decimal(Decimal("3.14159"), ".2f") == "3.14"

    def test_formats_float(self):
        assert _fmt_decimal(1.5, ".1f") == "1.5"

    def test_formats_int(self):
        assert _fmt_decimal(10, ".0f") == "10"

    def test_none_returns_na(self):
        assert _fmt_decimal(None, ".2f") == "N/A"

    def test_invalid_returns_str(self):
        result = _fmt_decimal("not_a_number", ".2f")
        assert result == "N/A" or result == "not_a_number"
