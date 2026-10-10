"""scripts/redate_macro_history.py: candidate download (scope, Yahoo convention, FRED initial release)."""
import io
import math
from datetime import date

import pandas as pd
import pytest

import scripts.redate_macro_history as rd

pytestmark = pytest.mark.unit

START, END = date(2026, 3, 9), date(2026, 3, 16)


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------

def test_scope_routes_fred_daily_and_yahoo_only():
    indicators = {
        "SP500": {"symbol": "^GSPC", "fetch_category": "equity_etf"},
        "US_10Y_YIELD": {"fred_series": "DGS10", "symbol": "^TNX", "fetch_category": "fred"},
        "US_CPI": {"fred_series": "CPIAUCSL", "fetch_category": "fred"},  # monthly
        "FIN_STRESS_INDEX": {"fred_series": "STLFSI4", "fetch_category": "fred"},  # weekly
        "BITCOIN": {"symbol": "BTC-USD", "fetch_category": "crypto"},
        "URANIUM": {"symbol": "SRUUF", "fetch_category": "equity_etf"},
        "EUR_RON": {"symbol": "EURRON=X", "fetch_category": "fx", "country_code": "RO"},
    }
    assert rd.in_scope_series(indicators) == {
        "SP500": ("yahoo", "^GSPC"),
        "US_10Y_YIELD": ("fred_initial", "DGS10"),  # FRED wins over the Yahoo fallback, as live
    }


def test_scope_on_real_indicators():
    scope = rd.in_scope_series()
    assert len(scope) == 26
    assert scope["USD_GBP"] == ("yahoo", "GBPUSD=X")
    assert scope["US_HY_SPREAD"] == ("fred_initial", "BAMLH0A0HYM2")
    for key in ("BITCOIN", "URANIUM", "US_CPI", "NICKEL", "FIN_STRESS_INDEX", "EUR_RON"):
        assert key not in scope


# ---------------------------------------------------------------------------
# Yahoo
# ---------------------------------------------------------------------------

def _history(closes, tz="America/New_York"):
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in closes], tz=tz)
    return pd.DataFrame({"Close": list(closes.values())}, index=idx)


def test_yahoo_stamps_trading_date_and_end_is_inclusive():
    seen = {}

    def history_fn(symbol, start, end_exclusive):
        seen.update(symbol=symbol, start=start, end=end_exclusive)
        # Friday 13 March close at 00:00 New York time must stay on the 13th
        return _history({"2026-03-12": 6600.0, "2026-03-13": 6650.5, "2026-03-16": 6700.0})

    rows, dropped = rd.fetch_yahoo("SP500", "^GSPC", START, END, history_fn=history_fn)
    assert seen == {"symbol": "^GSPC", "start": START, "end": date(2026, 3, 17)}
    assert [(r.date, r.value) for r in rows] == [
        (date(2026, 3, 12), 6600.0), (date(2026, 3, 13), 6650.5), (date(2026, 3, 16), 6700.0)]
    assert all(r.source == "yahoo" and r.symbol == "^GSPC" for r in rows)
    assert dropped == []


def test_yahoo_drops_non_finite_and_weekend_bars():
    hist = _history({"2026-03-12": float("nan"), "2026-03-13": float("inf"),
                     "2026-03-14": 1.08, "2026-03-16": 1.09}, tz=None)
    rows, dropped = rd.fetch_yahoo("EUR_USD", "EURUSD=X", START, END, history_fn=lambda *a: hist)
    assert [r.date for r in rows] == [date(2026, 3, 16)]
    assert [(d.date, d.reason) for d in dropped] == [
        (date(2026, 3, 12), "non-finite close"),
        (date(2026, 3, 13), "non-finite close"),
        (date(2026, 3, 14), "weekend bar"),
    ]


def test_yahoo_empty_history():
    assert rd.fetch_yahoo("X", "X", START, END, history_fn=lambda *a: pd.DataFrame()) == ([], [])


# ---------------------------------------------------------------------------
# FRED
# ---------------------------------------------------------------------------

def _fred_stub(initial, latest):
    calls = []

    def get_fn(params):
        calls.append(params)
        values = initial if params.get("output_type") == 4 else latest
        return {"observations": [{"date": d, "value": v} for d, v in values.items()]}

    return get_fn, calls


def test_fred_writes_initial_release_and_audits_revisions():
    get_fn, calls = _fred_stub(
        initial={"2026-03-12": "3.10", "2026-03-13": "3.20", "2026-03-16": "."},
        latest={"2026-03-12": "3.10", "2026-03-13": "3.25", "2026-03-16": "3.30"},
    )
    rows, dropped, revisions = rd.fetch_fred("US_HY_SPREAD", "BAMLH0A0HYM2", START, END, "k", get_fn=get_fn)

    assert [(r.date, r.value, r.source) for r in rows] == [
        (date(2026, 3, 12), 3.10, "fred_initial"), (date(2026, 3, 13), 3.20, "fred_initial")]
    assert revisions == [{"indicator_key": "US_HY_SPREAD", "symbol": "BAMLH0A0HYM2",
                          "date": "2026-03-13", "initial": 3.20, "latest": 3.25}]
    assert dropped == []

    initial_call = next(c for c in calls if c.get("output_type") == 4)
    assert initial_call["realtime_start"] == START.isoformat()
    assert initial_call["realtime_end"] == "9999-12-31"
    assert any("output_type" not in c for c in calls)


def test_fred_drops_weekend_observation():
    get_fn, _ = _fred_stub(initial={"2026-02-28": "2.90"}, latest={"2026-02-28": "2.90"})
    rows, dropped, _ = rd.fetch_fred("US_HY_SPREAD", "BAMLH0A0HYM2",
                                     date(2026, 2, 23), date(2026, 3, 1), "k", get_fn=get_fn)
    assert rows == []
    assert [(d.date, d.reason) for d in dropped] == [(date(2026, 2, 28), "weekend observation")]


def test_fred_error_does_not_leak_api_key(monkeypatch):
    class Resp:
        status_code = 400

        def json(self):
            return {"error_message": "Bad Request."}

    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp())
    with pytest.raises(RuntimeError) as exc:
        rd._fred_get({"series_id": "DGS10", "api_key": "SECRET123"})
    assert "SECRET123" not in str(exc.value)
    assert "DGS10" in str(exc.value)


# ---------------------------------------------------------------------------
# CSV round trip
# ---------------------------------------------------------------------------

def test_candidates_csv_round_trip(tmp_path):
    rows = [rd.CandidateRow(indicator_key="SP500", date=date(2026, 3, 13), value=6650.5,
                            source="yahoo", symbol="^GSPC")]
    path = tmp_path / "candidates.csv"
    rd.write_candidates(path, rows)
    assert path.read_text().splitlines()[0] == "indicator_key,date,value,source,symbol"
    with open(path) as f:
        assert rd.read_candidates(f) == rows


def test_candidate_rejects_non_finite():
    with pytest.raises(ValueError):
        rd.CandidateRow(indicator_key="X", date=date(2026, 3, 13), value=math.nan,
                        source="yahoo", symbol="X")


# ---------------------------------------------------------------------------
# Gates (synthetic series)
# ---------------------------------------------------------------------------

def _walk(n=80, seed=1, start="2026-01-12", level=100.0):
    import numpy as np
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n)
    return pd.Series(level * np.exp(np.cumsum(rng.normal(0, 0.01, n))), index=idx)


def test_g1_detects_one_day_late_series():
    ref = _walk()
    late = ref.shift(1).dropna()  # day t holds the value of t-1 (fetch-dated)
    assert rd.gate_g1(ref, {"REF": ref}, diff=False)[0] == rd.PASS
    status, note = rd.gate_g1(late, {"REF": ref}, diff=False)
    assert status == rd.FAIL and "best lag +1" in note


def test_g1_without_reference_is_na():
    assert rd.gate_g1(_walk(), {}, diff=False)[0] == rd.NA


def test_level_check_tolerance_and_outlier_cap():
    ref = _walk()
    assert rd.level_check(ref * 1.0005, ref, rd.LEVEL_TOL, absolute=False)["passed"]
    spiked = ref.copy()
    spiked.iloc[10] *= 1.01  # one day at 10x the tolerance breaks the 5x cap
    r = rd.level_check(spiked, ref, rd.LEVEL_TOL, absolute=False)
    assert not r["passed"] and r["worst"][0][0] == ref.index[10].date().isoformat()


def test_level_check_absolute_pp():
    ref = _walk(level=4.0)
    assert rd.level_check(ref + 0.01, ref, rd.LEVEL_PP_TOL, absolute=True)["passed"]
    assert not rd.level_check(ref + 0.05, ref, rd.LEVEL_PP_TOL, absolute=True)["passed"]


def test_g3_passes_if_one_reference_passes(monkeypatch):
    ref = _walk()
    monkeypatch.setitem(rd.PUBLIC_REFERENCES, "TEST", [("A", "level"), ("B", "proxy")])
    status, note = rd.gate_g3("TEST", ref, {"A": ref * 1.05, "B": ref * 1.02}, diff=False)
    assert status == rd.PASS and note.startswith("KO A") and "ok B" in note
    assert rd.gate_g3("TEST", ref, {}, diff=False)[0] == rd.NA


def test_g5_missing_days_fail_holiday_bars_listed():
    cal = rd.calendar_for("yahoo", "^GSPC", date(2026, 5, 18), date(2026, 5, 29))
    assert date(2026, 5, 25) not in cal and len(cal) == 9

    def rows(days):
        return [rd.CandidateRow(indicator_key="X", date=d, value=1.0, source="yahoo", symbol="X") for d in days]

    assert rd.gate_g5(rows(cal), cal)[0] == rd.PASS
    status, note = rd.gate_g5(rows(cal + [date(2026, 5, 25)]), cal)
    assert status == rd.PASS and "2026-05-25" in note
    status, note = rd.gate_g5(rows(cal[1:]), cal)
    assert status == rd.FAIL and "missing 1" in note


def test_calendars():
    days = rd.calendar_for("yahoo", "EURUSD=X", date(2026, 5, 25), date(2026, 5, 29))
    assert len(days) == 5  # FX trades on US holidays
    bond = rd.calendar_for("fred_initial", "DGS10", date(2026, 3, 30), date(2026, 4, 3))
    assert date(2026, 4, 3) in bond  # Good Friday: bond market open, NYSE closed
    with pytest.raises(ValueError):
        rd.calendar_for("yahoo", "^GSPC", date(2025, 12, 1), date(2026, 1, 5))


def test_g4_overlap_compares_common_dates_only():
    prod = _walk().round(4)
    cand = prod.drop(prod.index[5])  # 2026-01-19
    holiday = pd.Series({pd.Timestamp("2026-05-25"): 1.0})
    status, note = rd.gate_g4(cand, pd.concat([prod, holiday]))
    assert status == rd.PASS and "prod-only dates: 2026-01-19, 2026-05-25" in note
    assert rd.gate_g4(cand * 1.01, prod)[0] == rd.FAIL
    assert rd.gate_g4(cand, None)[0] == rd.NA


# ---------------------------------------------------------------------------
# Apply (mocked cursor)
# ---------------------------------------------------------------------------

def _cand(key, d, v=1.0):
    return rd.CandidateRow(indicator_key=key, date=d, value=v, source="yahoo", symbol="X")


class FakeCursor:
    """Records statements; answers the few SELECTs apply_redate issues."""

    def __init__(self, exists=False, before=None, after=None, snapshot_rows=None, weekend_left=0):
        self.sql, self.exists = [], exists
        self.counts = [before or [], after or []]
        self.snapshot_rows = snapshot_rows
        self.weekend_left = weekend_left
        self.rowcount, self._result = 0, None

    def execute(self, query, params=None):
        q = query if isinstance(query, str) else repr(query)
        self.sql.append(q)
        if "to_regclass" in q:
            self._result = [(self.exists,)]
        elif "COUNT(*)" in q:
            self._result = self.counts.pop(0)
        elif "CREATE TABLE" in q:
            self.rowcount = self.snapshot_rows
        elif "ISODOW FROM date) >= 6" in q and q.lstrip().startswith("DELETE"):
            self.rowcount = self.weekend_left

    def fetchone(self):
        return self._result[0]

    def fetchall(self):
        return self._result


@pytest.fixture
def no_execute_values(monkeypatch):
    import psycopg2.extras
    inserted = []
    monkeypatch.setattr(psycopg2.extras, "execute_values", lambda cur, q, rows: (cur.sql.append(q), inserted.extend(rows)))
    return inserted


W0, W1 = date(2026, 1, 12), date(2026, 5, 31)
SNAP = "macro_indicators_redate_20261011"


def test_validate_refuses_bad_input():
    good = [_cand("SP500", date(2026, 3, 13))]
    assert rd.validate_apply_input(good + [_cand("NASDAQ", date(2026, 3, 13))], ["SP500"], W0, W1) == good
    for rows, keys, msg in [
        (good, ["BITCOIN"], "not in scope"),
        (good, ["SP500", "NASDAQ"], "no candidates for: NASDAQ"),
        ([_cand("SP500", date(2026, 6, 1))], ["SP500"], "outside window"),
        ([_cand("SP500", date(2026, 3, 14))], ["SP500"], "weekend"),
        (good + good, ["SP500"], "duplicate"),
    ]:
        with pytest.raises(rd.ApplyError, match=msg):
            rd.validate_apply_input(rows, keys, W0, W1)


def test_apply_statement_order_and_counts(no_execute_values):
    rows = [_cand("SP500", date(2026, 3, 12), 6600.0), _cand("SP500", date(2026, 3, 13), 6650.5)]
    cur = FakeCursor(before=[("SP500", 3, 1)], after=[("SP500", 2, 0)], snapshot_rows=3)
    report = rd.apply_redate(cur, rows, ["SP500"], W0, W1, SNAP)

    writes = [q.lstrip().split()[0] for q in cur.sql if q.lstrip().startswith(("DELETE", "INSERT"))]
    assert writes == ["DELETE", "INSERT", "DELETE"]  # window delete, insert, leftover weekend delete
    create_idx = next(i for i, q in enumerate(cur.sql) if "CREATE TABLE" in q)
    first_delete = next(i for i, q in enumerate(cur.sql) if q.lstrip().startswith("DELETE"))
    assert create_idx < first_delete  # snapshot before any delete
    assert no_execute_values == [(date(2026, 3, 12), "SP500", 6600.0, "Points", "INDICES", "US"),
                                 (date(2026, 3, 13), "SP500", 6650.5, "Points", "INDICES", "US")]
    assert report == [{"indicator_key": "SP500", "rows_before": 3, "weekend_before": 1, "rows_after": 2}]


def test_apply_refuses_existing_snapshot(no_execute_values):
    cur = FakeCursor(exists=True)
    with pytest.raises(rd.ApplyError, match="already exists"):
        rd.apply_redate(cur, [_cand("SP500", date(2026, 3, 13))], ["SP500"], W0, W1, SNAP)
    assert not any(q.lstrip().startswith(("DELETE", "INSERT")) for q in cur.sql)


def test_apply_refuses_bad_snapshot_name(no_execute_values):
    with pytest.raises(rd.ApplyError, match="must start with"):
        rd.apply_redate(FakeCursor(), [], ["SP500"], W0, W1, "macro_indicators")


def test_apply_raises_on_count_mismatch(no_execute_values):
    cur = FakeCursor(before=[("SP500", 3, 1)], after=[("SP500", 1, 0)], snapshot_rows=3)
    with pytest.raises(rd.ApplyError, match="expected 2"):
        rd.apply_redate(cur, [_cand("SP500", date(2026, 3, 12)), _cand("SP500", date(2026, 3, 13))],
                        ["SP500"], W0, W1, SNAP)


def test_apply_raises_on_snapshot_mismatch(no_execute_values):
    cur = FakeCursor(before=[("SP500", 3, 1)], snapshot_rows=2)
    with pytest.raises(rd.ApplyError, match="snapshot has 2 rows"):
        rd.apply_redate(cur, [_cand("SP500", date(2026, 3, 13))], ["SP500"], W0, W1, SNAP)


def _run_apply_cli(monkeypatch, cursor_factory, argv):
    """cmd_apply with a fake DatabaseManager; returns (exit code, connection mock)."""
    from contextlib import contextmanager
    from unittest.mock import MagicMock
    import src.storage.database as database

    conn = MagicMock()
    events = []
    conn.rollback.side_effect = lambda: events.append("rollback")
    conn.cursor.return_value.__enter__.return_value = cursor_factory()

    class FakeDB:
        @contextmanager
        def get_connection(self):
            try:
                yield conn
                events.append("commit")
            except Exception:
                events.append("rollback")
                raise

    monkeypatch.setattr(database, "DatabaseManager", FakeDB)
    return rd.main(argv), events


def test_cli_dry_run_rolls_back(monkeypatch, tmp_path, no_execute_values):
    path = tmp_path / "c.csv"
    rd.write_candidates(path, [_cand("SP500", date(2026, 3, 13))])
    code, events = _run_apply_cli(
        monkeypatch, lambda: FakeCursor(before=[("SP500", 2, 1)], after=[("SP500", 1, 0)], snapshot_rows=2),
        ["apply", "--series", "SP500", "--candidates", str(path), "--snapshot", SNAP, "--dry-run"])
    assert code == 0 and events[0] == "rollback"


def test_cli_failure_rolls_back_and_exits_2(monkeypatch, tmp_path, no_execute_values):
    path = tmp_path / "c.csv"
    rd.write_candidates(path, [_cand("SP500", date(2026, 3, 13))])
    code, events = _run_apply_cli(
        monkeypatch, lambda: FakeCursor(exists=True),
        ["apply", "--series", "SP500", "--candidates", str(path), "--snapshot", SNAP])
    assert code == 2 and events == ["rollback"]


def test_rollback_restores_snapshot_series():
    class RbCursor(FakeCursor):
        def execute(self, query, params=None):
            super().execute(query, params)
            if "SELECT DISTINCT" in repr(query):
                self._result = [("SP500",), ("NASDAQ",)]

    cur = RbCursor(exists=True, before=[("NASDAQ", 96, 0), ("SP500", 96, 0)],
                   after=[("NASDAQ", 100, 20), ("SP500", 100, 20)])
    report = rd.rollback_redate(cur, SNAP, W0, W1)
    stmts = [q for q in cur.sql if "DELETE" in q or "INSERT" in q]
    assert "DELETE" in stmts[0] and "INSERT INTO macro_indicators SELECT" in stmts[1]
    assert [r["indicator_key"] for r in report] == ["NASDAQ", "SP500"]
    assert report[0]["rows_after"] == 100
