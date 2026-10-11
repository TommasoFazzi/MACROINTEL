"""Evening macro fetch: settlement refresh, ECB FX, NYSE holiday rule, no fallback with invented dates."""
import sys
from contextlib import contextmanager
from datetime import date
from unittest.mock import MagicMock

import pytest

import src.integrations.openbb_service as obs
from src.integrations.openbb_service import OpenBBMarketService, RefreshChange, ecb_cross_rates, refresh_decision

pytestmark = pytest.mark.unit

TUE = date(2026, 6, 23)


def _service():
    svc = object.__new__(OpenBBMarketService)
    svc.db = MagicMock()
    return svc


# ---------------------------------------------------------------------------
# refresh_decision
# ---------------------------------------------------------------------------

def test_decision_insert_overwrite_skip_and_equal():
    assert refresh_decision("SILVER", TUE, None, 62.02).action == "insert"
    c = refresh_decision("SILVER", TUE, 61.40, 62.02)
    assert (c.action, c.old_value, c.new_value) == ("overwrite", 61.40, 62.02)
    assert refresh_decision("SILVER", TUE, 61.40, 70.0).action == "skip_outlier"  # +14%
    assert refresh_decision("SILVER", TUE, 62.02, 62.020001) is None  # equal after 4-dp rounding


# ---------------------------------------------------------------------------
# refresh_recent_sessions
# ---------------------------------------------------------------------------

def _refresh_setup(monkeypatch, closes, stored):
    svc = _service()
    monkeypatch.setattr(svc, "_refreshable_keys", lambda: ["SILVER"])
    monkeypatch.setattr(OpenBBMarketService, "_yf_daily_closes", staticmethod(lambda sym, s, e: closes))
    monkeypatch.setattr(svc, "_stored_values", lambda key, dates: {d: v for d, v in stored.items() if d in dates})
    written = []
    monkeypatch.setattr(svc, "_upsert_rows_and_recompute", lambda key, rows: written.append((key, rows)))
    return svc, written


CLOSES = {date(2026, 6, 18): 61.0, date(2026, 6, 19): 61.5, date(2026, 6, 22): 61.8, date(2026, 6, 23): 62.02}


def test_refresh_write_overwrites_last_sessions_before_target(monkeypatch):
    stored = {date(2026, 6, 19): 61.5, date(2026, 6, 22): 61.2}  # 06-23 missing, 06-22 wrong
    svc, written = _refresh_setup(monkeypatch, CLOSES, stored)
    changes = svc.refresh_recent_sessions(date(2026, 6, 24), lookback=3, mode="write")
    assert [(c.date, c.action) for c in changes] == [(date(2026, 6, 22), "overwrite"), (date(2026, 6, 23), "insert")]
    assert written == [("SILVER", {date(2026, 6, 22): 61.8, date(2026, 6, 23): 62.02})]


def test_refresh_log_mode_writes_nothing(monkeypatch):
    svc, written = _refresh_setup(monkeypatch, CLOSES, {date(2026, 6, 22): 61.2})
    changes = svc.refresh_recent_sessions(date(2026, 6, 24), mode="log")
    assert changes and written == []


def test_refresh_outlier_not_written(monkeypatch):
    stored = {date(2026, 6, 19): 61.5, date(2026, 6, 22): 61.8, date(2026, 6, 23): 50.0}
    svc, written = _refresh_setup(monkeypatch, CLOSES, stored)
    changes = svc.refresh_recent_sessions(date(2026, 6, 24), mode="write")
    assert [c.action for c in changes] == ["skip_outlier"]
    assert written == []


def test_refresh_off_and_bad_mode(monkeypatch):
    svc, written = _refresh_setup(monkeypatch, CLOSES, {})
    assert svc.refresh_recent_sessions(date(2026, 6, 24), mode="off") == []
    with pytest.raises(ValueError):
        svc.refresh_recent_sessions(date(2026, 6, 24), mode="yes")


def test_refreshable_keys_scope():
    keys = _service()._refreshable_keys()
    assert {"SILVER", "GOLD", "WTI_OIL", "TTF_GAS", "VIX", "DOLLAR_INDEX", "SP500"} <= set(keys)
    for excluded in ("BITCOIN", "URANIUM", "USD_CNH", "EUR_USD", "US_10Y_YIELD", "EUR_RON"):
        assert excluded not in keys


# ---------------------------------------------------------------------------
# ECB FX
# ---------------------------------------------------------------------------

def test_ecb_cross_rates():
    out = ecb_cross_rates({"USD": 1.16, "JPY": 185.0, "GBP": 0.86, "CNY": 8.0})
    assert out["EUR_USD"] == 1.16
    assert round(out["USD_JPY"], 4) == 159.4828
    assert round(out["USD_GBP"], 4) == round(1.16 / 0.86, 4)  # USD per GBP
    assert round(out["USD_CNY"], 4) == round(8.0 / 1.16, 4)
    assert ecb_cross_rates({"JPY": 185.0}) == {}


def test_save_ecb_fx_writes_last_sessions_only(monkeypatch):
    svc = _service()
    data = {date(2026, 4, 1): {"EUR_USD": 1.07}, date(2026, 4, 2): {"EUR_USD": 1.08},
            date(2026, 4, 7): {"EUR_USD": 1.09}, date(2026, 4, 8): {"EUR_USD": 1.10}}
    monkeypatch.setattr(obs, "fetch_ecb_fx", lambda s, e: data)
    monkeypatch.setattr(svc, "_upsert_indicator_metadata", lambda **kw: None)
    written = {}
    monkeypatch.setattr(svc, "_upsert_rows_and_recompute", lambda key, rows: written.__setitem__(key, rows))
    assert svc._save_ecb_fx(date(2026, 4, 8)) is False  # JPY/GBP/CNY missing in this stub
    assert written["EUR_USD"] == {date(2026, 4, 2): 1.08, date(2026, 4, 7): 1.09, date(2026, 4, 8): 1.10}


def test_save_ecb_fx_api_failure(monkeypatch):
    svc = _service()

    def boom(s, e):
        raise ConnectionError("down")

    monkeypatch.setattr(obs, "fetch_ecb_fx", boom)
    assert svc._save_ecb_fx(date(2026, 4, 8)) is False


# ---------------------------------------------------------------------------
# ensure_daily_macro_data: holiday rule, ECB keys, no fallback
# ---------------------------------------------------------------------------

def _ensure_setup(monkeypatch, nyse_open, yf_result):
    svc = _service()
    monkeypatch.setattr(svc, "_has_macro_data", lambda d, country_code=None: False)
    monkeypatch.setattr(svc, "_save_ecb_fx", lambda d: True)
    monkeypatch.setattr(svc, "_fetch_indicator_openbb_fixed", lambda series, d: None)
    monkeypatch.setattr(svc, "_upsert_indicator_metadata", lambda **kw: None)
    monkeypatch.setattr(obs.time, "sleep", lambda s: None)
    quotes = []
    monkeypatch.setattr(svc, "_fetch_indicator_openbb", lambda obb, key, cfg, d: quotes.append(key) or 6.7)
    monkeypatch.setattr(obs, "get_obb", lambda: object())
    yf_calls = []
    monkeypatch.setattr(svc, "_fetch_indicator_yfinance",
                        lambda sym, target_date=None: yf_calls.append(sym) or yf_result(sym, target_date))
    saved = []
    monkeypatch.setattr(svc, "_save_macro_indicator", lambda d, key, *a, **k: saved.append((key, d)) or True)
    import src.integrations.market_calendar as mc
    monkeypatch.setattr(mc, "is_nyse_open", lambda d: nyse_open)
    return svc, saved, yf_calls, quotes


def test_holiday_skips_futures_indices_vix_dxy(monkeypatch):
    svc, saved, yf_calls, quotes = _ensure_setup(monkeypatch, nyse_open=False,
                                                 yf_result=lambda s, d: (1.0, d))
    svc.ensure_daily_macro_data(date(2026, 6, 19))
    keys = {k for k, _ in saved}
    assert not keys & {"GOLD", "SP500", "VIX", "DOLLAR_INDEX", "WTI_OIL"}
    assert "EUR_RON" in keys and "BITCOIN" in keys  # =X FX and crypto still written
    assert not {"EURUSD=X", "JPY=X", "GBPUSD=X", "CNY=X"} & set(yf_calls)  # ECB keys not read from Yahoo


def test_no_fallback_save_when_yahoo_fails(monkeypatch):
    svc, saved, yf_calls, quotes = _ensure_setup(monkeypatch, nyse_open=True, yf_result=lambda s, d: None)
    svc.ensure_daily_macro_data(TUE)
    assert quotes == ["USD_CNH"]  # only the declared quote series uses the live quote
    assert [k for k, _ in saved] == ["USD_CNH"]
    assert saved[0][1] == TUE


# ---------------------------------------------------------------------------
# fetch_daily_market_data --refresh-mode
# ---------------------------------------------------------------------------

def test_cli_passes_refresh_mode(monkeypatch):
    import scripts.fetch_daily_market_data as fd
    seen = {}
    monkeypatch.setattr(fd, "fetch_macro_data", lambda d, dry, force, mode: seen.update(mode=mode) or True)
    monkeypatch.setattr(sys, "argv", ["x", "--date", "2026-06-23", "--refresh-mode", "log"])
    assert fd.main() == 0 and seen["mode"] == "log"
    monkeypatch.setattr(sys, "argv", ["x", "--date", "2026-06-23"])
    fd.main()
    assert seen["mode"] == "off"


def test_refresh_runs_after_fetch(monkeypatch):
    import scripts.fetch_daily_market_data as fd
    svc = MagicMock()
    svc.ensure_daily_macro_data.return_value = True
    svc.fetch_ro_indicators.return_value = True
    svc.get_macro_context_text.return_value = ""
    monkeypatch.setattr(obs, "OpenBBMarketService", MagicMock(return_value=svc))
    monkeypatch.setattr(fd, "DatabaseManager", MagicMock())
    assert fd.fetch_macro_data(TUE, refresh_mode="log") is True
    svc.refresh_recent_sessions.assert_called_once_with(TUE, mode="log")
    svc.refresh_recent_sessions.reset_mock()
    fd.fetch_macro_data(TUE)
    svc.refresh_recent_sessions.assert_not_called()
