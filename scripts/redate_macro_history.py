#!/usr/bin/env python3
"""
Re-download and re-date the daily macro_indicators history for a past window.

WHY THIS EXISTS
---------------
Between 2026-01-12 and 2026-05-20 the live writer stored daily rows under the
fetch date instead of the trading date (fixed by a06de73 and 619055e). The row
for day t often holds the close of t-1, and Saturday rows are sometimes the only
copy of Friday's close. The shift is not uniform, so re-keying dates cannot fix
it. This script re-fetches the window from the same open sources the live writer
uses, with the same date convention, verifies it, and replaces it.

STAGES
------
    fetch         download candidates (Yahoo Close by trading day, FRED initial
                  release) -> candidates.csv, revisions.csv, dropped.csv
    check-public  public-source gates (G1, G3, G4, G5) -> redate_report.md
    apply         prod: snapshot + replace the approved series in one transaction

A private gate against licensed data runs outside the repo between check-public
and apply and outputs only the approved series names (one per line).

Usage:
    python scripts/redate_macro_history.py fetch --start 2026-01-12 --end 2026-05-31 --out-dir out/
"""

import argparse
import csv
import math
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, field_validator

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.integrations.openbb_service import OpenBBMarketService  # noqa: E402

FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
# output_type=4 (initial release only) needs a real-time period that covers every
# vintage of the requested observations, and FRED caps it at 2000 vintages. An
# observation is never released before its own date, so the period starts at the
# window start and stays open-ended.
FRED_REALTIME_END = "9999-12-31"

# Daily US keys left out on purpose: BITCOIN trades at weekends, URANIUM (SRUUF)
# has no reference source to verify against.
EXCLUDED_KEYS = {"BITCOIN", "URANIUM"}

CANDIDATE_FIELDS = ["indicator_key", "date", "value", "source", "symbol"]


class CandidateRow(BaseModel):
    indicator_key: str
    date: date
    value: float
    source: Literal["yahoo", "fred_initial"]
    symbol: str

    @field_validator("value")
    @classmethod
    def _finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("value must be finite")
        return v


class Dropped(BaseModel):
    indicator_key: str
    date: date
    reason: str


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------

def in_scope_series(indicators: Optional[dict] = None) -> Dict[str, Tuple[str, str]]:
    """Return {indicator_key: (source, symbol)} for the daily US series in scope.

    Mirrors the live writer's routing: fetch_category 'fred' goes to FRED (even when
    a Yahoo symbol exists as fallback, e.g. US_10Y_YIELD), everything else to Yahoo.
    FRED series are kept only when their frequency is daily.
    """
    indicators = indicators if indicators is not None else OpenBBMarketService.MACRO_INDICATORS
    freq = OpenBBMarketService.FRED_SERIES_FREQUENCY
    scope = {}
    for key, cfg in indicators.items():
        if key in EXCLUDED_KEYS or cfg.get("country_code", "US") != "US":
            continue
        if cfg.get("fetch_category") == "fred":
            series = cfg.get("fred_series")
            if series and freq.get(series) == "daily":
                scope[key] = ("fred_initial", series)
        elif cfg.get("symbol"):
            scope[key] = ("yahoo", cfg["symbol"])
    return scope


# ---------------------------------------------------------------------------
# Yahoo (same convention as OpenBBMarketService._fetch_indicator_yfinance)
# ---------------------------------------------------------------------------

def _yf_history(symbol: str, start: date, end_exclusive: date):
    import yfinance as yf
    return yf.Ticker(symbol).history(start=start.isoformat(), end=end_exclusive.isoformat())


def fetch_yahoo(
    key: str, symbol: str, start: date, end: date,
    history_fn: Callable = _yf_history,
) -> Tuple[List[CandidateRow], List[Dropped]]:
    """Daily Close per trading day in [start, end]. Non-finite and weekend bars are dropped."""
    import pandas as pd

    hist = history_fn(symbol, start, end + timedelta(days=1))  # yfinance end is exclusive
    rows: List[CandidateRow] = []
    dropped: List[Dropped] = []
    if hist is None or hist.empty:
        return rows, dropped
    try:
        hist.index = pd.to_datetime(hist.index).tz_localize(None).normalize()
    except TypeError:
        # Already timezone-naive
        hist.index = pd.to_datetime(hist.index).normalize()

    for ts, close in hist["Close"].items():
        d = ts.date()
        if not (start <= d <= end):
            continue
        value = float(close)
        if not math.isfinite(value):
            dropped.append(Dropped(indicator_key=key, date=d, reason="non-finite close"))
        elif d.weekday() >= 5:
            # The live writer skips weekends (a06de73)
            dropped.append(Dropped(indicator_key=key, date=d, reason="weekend bar"))
        else:
            rows.append(CandidateRow(indicator_key=key, date=d, value=value, source="yahoo", symbol=symbol))
    return rows, dropped


# ---------------------------------------------------------------------------
# FRED
# ---------------------------------------------------------------------------

def _fred_get(params: dict) -> dict:
    """GET FRED observations. Errors never carry the request URL (it holds the API key)."""
    import requests
    resp = requests.get(FRED_OBSERVATIONS_URL, params=params, timeout=30)
    if resp.status_code != 200:
        try:
            message = resp.json().get("error_message", "")
        except ValueError:
            message = ""
        raise RuntimeError(f"FRED {params.get('series_id')} HTTP {resp.status_code}: {message}")
    return resp.json()


def fetch_fred_observations(
    series: str, start: date, end: date, api_key: str,
    initial: bool, get_fn: Callable = _fred_get,
) -> Dict[date, Optional[float]]:
    """{date: value} from FRED; None where FRED publishes '.' (no observation)."""
    params = {
        "series_id": series, "api_key": api_key, "file_type": "json",
        "observation_start": start.isoformat(), "observation_end": end.isoformat(),
    }
    if initial:
        params.update(output_type=4, realtime_start=start.isoformat(), realtime_end=FRED_REALTIME_END)
    out: Dict[date, Optional[float]] = {}
    for obs in get_fn(params).get("observations", []):
        raw = obs.get("value")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = None
        out[date.fromisoformat(obs["date"])] = value
    return out


def fetch_fred(
    key: str, series: str, start: date, end: date, api_key: str,
    get_fn: Callable = _fred_get,
) -> Tuple[List[CandidateRow], List[Dropped], List[dict]]:
    """Initial-release candidates plus a revision audit against the latest values.

    Only the initial release is ever a candidate. A date where FRED has no
    observation ('.', e.g. a bond-market holiday) is skipped, as the live writer
    would never have stored it.
    """
    initial = fetch_fred_observations(series, start, end, api_key, initial=True, get_fn=get_fn)
    latest = fetch_fred_observations(series, start, end, api_key, initial=False, get_fn=get_fn)
    rows: List[CandidateRow] = []
    dropped: List[Dropped] = []
    revisions: List[dict] = []
    for d in sorted(initial):
        value = initial[d]
        if value is None:
            continue
        if not math.isfinite(value):
            dropped.append(Dropped(indicator_key=key, date=d, reason="non-finite value"))
            continue
        if d.weekday() >= 5:
            dropped.append(Dropped(indicator_key=key, date=d, reason="weekend observation"))
            continue
        rows.append(CandidateRow(indicator_key=key, date=d, value=value, source="fred_initial", symbol=series))
        last = latest.get(d)
        if last is not None and last != value:
            revisions.append({"indicator_key": key, "symbol": series, "date": d.isoformat(),
                              "initial": value, "latest": last})
    return rows, dropped, revisions


# ---------------------------------------------------------------------------
# CSV I/O
# ---------------------------------------------------------------------------

def write_candidates(path: Path, rows: List[CandidateRow]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CANDIDATE_FIELDS)
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r.indicator_key, r.date)):
            w.writerow({**r.model_dump(), "date": r.date.isoformat()})


def read_candidates(fh) -> List[CandidateRow]:
    return [CandidateRow(**row) for row in csv.DictReader(fh)]


def _write_dicts(path: Path, fieldnames: List[str], rows: List[dict]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------------------
# Candidate download (shared by fetch and the G4 overlap re-download)
# ---------------------------------------------------------------------------

class Download(BaseModel):
    candidates: List[CandidateRow] = []
    dropped: List[Dropped] = []
    revisions: List[dict] = []
    failed: Dict[str, str] = {}


def download_candidates(
    scope: Dict[str, Tuple[str, str]], start: date, end: date, api_key: str, verbose: bool = True,
) -> Download:
    result = Download()
    for key, (source, symbol) in sorted(scope.items()):
        try:
            if source == "yahoo":
                rows, drop = fetch_yahoo(key, symbol, start, end)
            else:
                rows, drop, revs = fetch_fred(key, symbol, start, end, api_key)
                result.revisions.extend(revs)
        except Exception as e:  # noqa: BLE001 — report and continue with the other series
            result.failed[key] = f"{type(e).__name__}: {e}"
            if verbose:
                print(f"  {key:<26} {symbol:<14} FAILED: {result.failed[key]}")
            continue
        if not rows:
            result.failed[key] = "no data"
        result.candidates.extend(rows)
        result.dropped.extend(drop)
        if verbose:
            print(f"  {key:<26} {symbol:<14} {source:<13} rows={len(rows):>4} dropped={len(drop)}")
    return result


# ---------------------------------------------------------------------------
# Public references (G1, G3)
# ---------------------------------------------------------------------------

HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (macrointel.net research)"}
TREASURY_CSV_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all?type={kind}&field_tdr_date_value={year}&page&_format=csv"
)
ECB_EXR_URL = "https://data-api.ecb.europa.eu/service/data/EXR/D.USD+JPY+GBP+CNY.EUR.SP00.A"
WESTMETALL_URL = "https://www.westmetall.com/en/markdaten.php?action=table&field={field}"

# Reference kinds: "level" = same instrument, relative tolerance; "level_pp" = same
# instrument in percentage points, absolute tolerance; "proxy" = related instrument
# (spot vs futures, other fixing time), daily-change correlation.
LEVEL_TOL = 0.001
LEVEL_PP_TOL = 0.02
PROXY_MIN_CORR = 0.9
LEVEL_MIN_SHARE = 0.99
LEVEL_MAX_MULTIPLE = 5

# key -> [(reference name, kind)]. Keys absent here have no free public source
# (DOLLAR_INDEX, TTF_GAS, WHEAT, USD_CNH, US_HY_SPREAD): G3 is N/A for them.
PUBLIC_REFERENCES: Dict[str, List[Tuple[str, str]]] = {
    "SP500": [("FRED:SP500", "level")],
    "NASDAQ": [("FRED:NASDAQCOM", "level")],
    "VIX": [("FRED:VIXCLS", "level")],
    "EUR_USD": [("FRED:DEXUSEU", "proxy"), ("ECB:EUR_USD", "proxy")],
    "USD_JPY": [("FRED:DEXJPUS", "proxy"), ("ECB:USD_JPY", "proxy")],
    "USD_GBP": [("FRED:DEXUSUK", "proxy"), ("ECB:GBP_USD", "proxy")],
    "USD_CNY": [("FRED:DEXCHUS", "proxy"), ("ECB:USD_CNY", "proxy")],
    "BRENT_OIL": [("FRED:DCOILBRENTEU", "proxy")],
    "WTI_OIL": [("FRED:DCOILWTICO", "proxy")],
    "NATURAL_GAS": [("FRED:DHHNGSP", "proxy")],
    "GOLD": [("WESTMETALL:USD_ozt_London", "proxy")],
    "SILVER": [("WESTMETALL:Ag_usd", "proxy")],
    "COPPER": [("WESTMETALL:LME_Cu_cash", "proxy")],
    "ALUMINUM": [("WESTMETALL:LME_Al_cash", "proxy")],
    "US_10Y_YIELD": [("TREASURY:10Y", "level_pp")],
    "US_2Y_YIELD": [("TREASURY:2Y", "level_pp")],
    "YIELD_CURVE_10Y_2Y": [("TREASURY:10Y-2Y", "level_pp")],
    "YIELD_CURVE_10Y_3M": [("TREASURY:10Y-3M", "level_pp")],
    "REAL_RATE_10Y": [("TREASURY:REAL10Y", "level_pp")],
    "BREAKEVEN_10Y": [("TREASURY:BEI10Y", "level_pp")],
    "INFLATION_EXPECTATION_5Y": [("TREASURY:5Y5Y", "level_pp")],
}


def _http_get(url: str, **kwargs):
    import requests
    resp = requests.get(url, headers=HTTP_HEADERS, timeout=60, **kwargs)
    resp.raise_for_status()
    return resp


def fetch_treasury(start: date, end: date, kind: str):
    """Treasury daily par curve (kind 'daily_treasury_yield_curve' or '..._real_yield_curve')."""
    import io
    import pandas as pd
    frames = []
    for year in range(start.year, end.year + 1):
        text = _http_get(TREASURY_CSV_URL.format(year=year, kind=kind)).text
        frames.append(pd.read_csv(io.StringIO(text)))
    df = pd.concat(frames)
    df.index = pd.to_datetime(df.pop("Date"), format="%m/%d/%Y")
    return df.sort_index()


def fetch_ecb(start: date, end: date):
    """ECB reference rates, units of currency per EUR, columns USD/JPY/GBP/CNY."""
    import io
    import pandas as pd
    text = _http_get(ECB_EXR_URL, params={"startPeriod": start.isoformat(),
                                          "endPeriod": end.isoformat(), "format": "csvdata"}).text
    df = pd.read_csv(io.StringIO(text))
    wide = df.pivot(index="TIME_PERIOD", columns="CURRENCY", values="OBS_VALUE")
    wide.index = pd.to_datetime(wide.index)
    return wide


def fetch_westmetall(field: str):
    """First price column of a Westmetall table (whole current history).

    The table repeats its header row between years; those rows fail to parse and are dropped.
    """
    import io
    import pandas as pd
    table = pd.read_html(io.StringIO(_http_get(WESTMETALL_URL.format(field=field)).text))[0]
    s = pd.Series(pd.to_numeric(table.iloc[:, 1], errors="coerce").values,
                  index=pd.to_datetime(table["date"], format="%d. %B %Y", errors="coerce"))
    return s[s.index.notna()].dropna().sort_index()


def build_references(start: date, end: date, api_key: str):
    """{reference name: pd.Series} plus {reference name: error} for unavailable ones."""
    import pandas as pd
    refs, errors = {}, {}

    def attempt(names, loader):
        try:
            for name, series in loader().items():
                if name in names:
                    refs[name] = series.loc[pd.Timestamp(start):pd.Timestamp(end)].dropna()
        except Exception as e:  # noqa: BLE001 — an unavailable source is N/A, never a pass
            for name in names:
                errors[name] = f"{type(e).__name__}: {e}"

    wanted = {name for refs_ in PUBLIC_REFERENCES.values() for name, _ in refs_}

    def fred_loader(series_id):
        def load():
            obs = fetch_fred_observations(series_id, start, end, api_key, initial=False)
            return {f"FRED:{series_id}": pd.Series(
                {pd.Timestamp(d): v for d, v in obs.items() if v is not None}, dtype=float)}
        return load

    for name in sorted(n for n in wanted if n.startswith("FRED:")):
        attempt({name}, fred_loader(name.split(":", 1)[1]))

    def treasury():
        nom = fetch_treasury(start, end, "daily_treasury_yield_curve")
        real = fetch_treasury(start, end, "daily_treasury_real_yield_curve")
        bei10 = nom["10 Yr"] - real["10 YR"]
        bei5 = nom["5 Yr"] - real["5 YR"]
        # FRED's T5YIFR definition
        fwd = (((1 + bei10 / 100) ** 10 / (1 + bei5 / 100) ** 5) ** 0.2 - 1) * 100
        return {"TREASURY:10Y": nom["10 Yr"], "TREASURY:2Y": nom["2 Yr"],
                "TREASURY:10Y-2Y": nom["10 Yr"] - nom["2 Yr"],
                "TREASURY:10Y-3M": nom["10 Yr"] - nom["3 Mo"],
                "TREASURY:REAL10Y": real["10 YR"], "TREASURY:BEI10Y": bei10,
                "TREASURY:5Y5Y": fwd}
    attempt({n for n in wanted if n.startswith("TREASURY:")}, treasury)

    def ecb():
        fx = fetch_ecb(start, end)
        return {"ECB:EUR_USD": fx["USD"], "ECB:USD_JPY": fx["JPY"] / fx["USD"],
                "ECB:GBP_USD": fx["USD"] / fx["GBP"], "ECB:USD_CNY": fx["CNY"] / fx["USD"]}
    attempt({n for n in wanted if n.startswith("ECB:")}, ecb)

    def westmetall():
        # Fine silver is quoted in EUR/kg; convert with Westmetall's own ECB EUR/USD
        # column so the proxy follows USD moves like SI=F does.
        eur_usd = fetch_westmetall("Euro_EZB")
        return {"WESTMETALL:USD_ozt_London": fetch_westmetall("USD_ozt_London"),
                "WESTMETALL:Ag_usd": (fetch_westmetall("Ag") * eur_usd).dropna(),
                "WESTMETALL:LME_Cu_cash": fetch_westmetall("LME_Cu_cash"),
                "WESTMETALL:LME_Al_cash": fetch_westmetall("LME_Al_cash")}
    attempt({n for n in wanted if n.startswith("WESTMETALL:")}, westmetall)
    return refs, errors


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------

PASS, FAIL, NA = "PASS", "FAIL", "N/A"

# Exchange holidays inside the supported range (2026). Yahoo futures, indices and
# DX-Y.NYB follow NYSE; FRED rates follow the SIFMA bond calendar; Yahoo FX (=X)
# trades every weekday.
NYSE_HOLIDAYS_2026 = {date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
                      date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
                      date(2026, 11, 26), date(2026, 12, 25)}
BOND_HOLIDAYS_2026 = {date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 5, 25),
                      date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 10, 12),
                      date(2026, 11, 11), date(2026, 11, 26), date(2026, 12, 25)}

OVERLAP_TOL = 1e-4
OVERLAP_MIN_SHARE = 0.99


def is_rate(key: str) -> bool:
    """Rates and spreads move in percentage points: use differences, not log returns."""
    return OpenBBMarketService.MACRO_INDICATORS.get(key, {}).get("unit") == "%"


def calendar_for(source: str, symbol: str, start: date, end: date) -> List[date]:
    if start.year != 2026 or end.year != 2026:
        raise ValueError("holiday calendars are defined for 2026 only")
    if source == "fred_initial":
        holidays = BOND_HOLIDAYS_2026
    elif symbol.endswith("=X"):
        holidays = set()
    else:
        holidays = NYSE_HOLIDAYS_2026
    days, d = [], start
    while d <= end:
        if d.weekday() < 5 and d not in holidays:
            days.append(d)
        d += timedelta(days=1)
    return days


def daily_changes(series, diff: bool):
    """Change between consecutive observations, indexed by the later date."""
    import numpy as np
    s = series.sort_index().astype(float)
    return (s.diff() if diff else np.log(s).diff()).dropna()


def lag_correlations(cand, ref, diff: bool, lags=range(-2, 3), min_obs: int = 20) -> Dict[int, float]:
    """Correlation of daily changes with the reference dates shifted by `lag` business days.

    Best lag 0 means the candidate's day t moves with the reference's day t. A
    candidate dated one day late (fetch-dated) peaks at lag +1.
    """
    from pandas.tseries.offsets import BDay
    rc, rr = daily_changes(cand, diff), daily_changes(ref, diff)
    out = {}
    for lag in lags:
        shifted = rr.copy()
        shifted.index = shifted.index + BDay(lag)
        joined = rc.to_frame("c").join(shifted.to_frame("r"), how="inner")
        if len(joined) >= min_obs:
            out[lag] = float(joined["c"].corr(joined["r"]))
    return out


def gate_g1(cand, refs: Dict[str, object], diff: bool) -> Tuple[str, str]:
    if not refs:
        return NA, "no public reference"
    notes, ok = [], True
    for name, ref in refs.items():
        corrs = lag_correlations(cand, ref, diff)
        if not corrs:
            notes.append(f"{name}: too few common days")
            ok = False
            continue
        best = max(corrs, key=corrs.get)
        ok &= best == 0
        notes.append(f"{name}: best lag {best:+d} (r0={corrs.get(0, float('nan')):.3f})")
    return (PASS if ok else FAIL), "; ".join(notes)


def level_check(cand, ref, tol: float, absolute: bool) -> dict:
    joined = cand.to_frame("c").join(ref.to_frame("r"), how="inner")
    if absolute:
        err = (joined["c"] - joined["r"]).abs()
    else:
        err = (joined["c"] / joined["r"] - 1).abs()
    within = float((err <= tol + 1e-12).mean()) if len(err) else 0.0
    worst = err.sort_values(ascending=False).head(5)
    passed = (len(err) > 0 and within >= LEVEL_MIN_SHARE
              and float(err.max()) <= LEVEL_MAX_MULTIPLE * tol + 1e-12)
    return {"passed": passed, "n": len(err), "within": within,
            "worst": [(d.date().isoformat(), float(e)) for d, e in worst.items() if e > tol]}


def proxy_check(cand, ref, diff: bool) -> dict:
    corr = lag_correlations(cand, ref, diff, lags=[0]).get(0)
    rc, rr = daily_changes(cand, diff), daily_changes(ref, diff)
    joined = rc.to_frame("c").join(rr.to_frame("r"), how="inner")
    outliers = []
    if len(joined) > 2:
        resid = joined["c"] - joined["r"]
        z = (resid - resid.mean()) / resid.std()
        outliers = [d.date().isoformat() for d in z[z.abs() > 4].index]
    return {"passed": corr is not None and corr >= PROXY_MIN_CORR, "corr": corr, "outliers": outliers}


def gate_g3(key: str, cand, refs: Dict[str, object], diff: bool) -> Tuple[str, str]:
    """Pass when at least one available reference passes; every reference is reported."""
    if not refs:
        return NA, "no public reference available"
    kinds = dict(PUBLIC_REFERENCES.get(key, []))
    notes, any_pass = [], False
    for name, ref in refs.items():
        kind = kinds[name]
        if kind == "proxy":
            r = proxy_check(cand, ref, diff)
            corr = "n/a" if r["corr"] is None else f"{r['corr']:.3f}"
            note = f"{name}: change corr {corr}"
            if r["outliers"]:
                note += f", outliers {', '.join(r['outliers'])}"
        else:
            absolute = kind == "level_pp"
            r = level_check(cand, ref, LEVEL_PP_TOL if absolute else LEVEL_TOL, absolute)
            note = f"{name}: {r['within']:.1%} of {r['n']} days within tolerance"
            if r["worst"]:
                note += ", worst " + ", ".join(f"{d} ({e:.4g})" for d, e in r["worst"])
        any_pass |= r["passed"]
        notes.append(("ok " if r["passed"] else "KO ") + note)
    return (PASS if any_pass else FAIL), "; ".join(notes)


def gate_g5(rows: List[CandidateRow], calendar: List[date]) -> Tuple[str, str]:
    """Completeness. Missing trading days fail; bars on calendar holidays are only listed."""
    dates = [r.date for r in rows]
    if not dates:
        return FAIL, "no rows"
    problems, notes = [], []
    dup = sorted({d for d in dates if dates.count(d) > 1})
    weekend = sorted(d for d in set(dates) if d.weekday() >= 5)
    missing = sorted(set(calendar) - set(dates))
    extra = sorted(set(dates) - set(calendar) - set(weekend))
    if dup:
        problems.append(f"duplicate dates {', '.join(map(str, dup))}")
    if weekend:
        problems.append(f"weekend dates {', '.join(map(str, weekend))}")
    if missing:
        problems.append(f"missing {len(missing)} trading days: {', '.join(map(str, missing[:10]))}")
    if extra:
        notes.append(f"bars on holidays: {', '.join(map(str, extra))}")
    status = FAIL if problems else PASS
    return status, "; ".join(problems + notes) or f"{len(set(dates))} trading days"


def gate_g4(cand, prod) -> Tuple[str, str]:
    """Overlap: re-downloaded values reproduce prod on the dates both have."""
    if prod is None or len(prod) == 0:
        return NA, "no prod rows for the overlap window"
    if cand is None or len(cand) == 0:
        return FAIL, "re-download returned no rows"
    joined = cand.round(4).to_frame("c").join(prod.to_frame("p"), how="inner")
    if joined.empty:
        return FAIL, "no common dates"
    rel = (joined["c"] / joined["p"] - 1).abs()
    within = float((rel <= OVERLAP_TOL).mean())
    status = PASS if within >= OVERLAP_MIN_SHARE else FAIL
    note = f"{within:.1%} of {len(joined)} common days within {OVERLAP_TOL:g}"
    bad = rel[rel > OVERLAP_TOL].sort_values(ascending=False).head(5)
    if len(bad):
        note += ", worst " + ", ".join(f"{d.date()} ({e:.2e})" for d, e in bad.items())
    prod_only = sorted(set(prod.index) - set(cand.index))
    cand_only = sorted(set(cand.index) - set(prod.index))
    if prod_only:
        note += f"; prod-only dates: {', '.join(str(d.date()) for d in prod_only)}"
    if cand_only:
        note += f"; missing in prod: {', '.join(str(d.date()) for d in cand_only)}"
    return status, note


def rows_to_series(rows: List[CandidateRow]):
    import pandas as pd
    return pd.Series({pd.Timestamp(r.date): r.value for r in rows}, dtype=float).sort_index()


def load_prod_csv(path: Path, start: date, end: date) -> Dict[str, object]:
    """Prod extract (columns date, key|indicator_key, value) -> {key: series} in the window."""
    import pandas as pd
    df = pd.read_csv(path)
    key_col = "indicator_key" if "indicator_key" in df.columns else "key"
    df["date"] = pd.to_datetime(df["date"])
    df = df[(df["date"] >= pd.Timestamp(start)) & (df["date"] <= pd.Timestamp(end))]
    return {k: g.set_index("date")["value"].astype(float).sort_index() for k, g in df.groupby(key_col)}


# ---------------------------------------------------------------------------
# Apply / rollback (prod, one transaction)
# ---------------------------------------------------------------------------

SNAPSHOT_PREFIX = "macro_indicators_redate_"


class ApplyError(Exception):
    pass


def validate_apply_input(candidates: List[CandidateRow], keys: List[str], start: date, end: date) -> List[CandidateRow]:
    """Candidates for `keys` only, after refusing anything that should never reach prod."""
    scope = in_scope_series()
    out_of_scope = sorted(set(keys) - scope.keys())
    if out_of_scope:
        raise ApplyError(f"not in scope: {', '.join(out_of_scope)}")
    wanted = set(keys)
    rows = [r for r in candidates if r.indicator_key in wanted]
    missing = sorted(set(keys) - {r.indicator_key for r in rows})
    if missing:
        raise ApplyError(f"no candidates for: {', '.join(missing)}")
    seen = set()
    for r in rows:
        if not (start <= r.date <= end):
            raise ApplyError(f"{r.indicator_key} {r.date} outside window {start}..{end}")
        if r.date.weekday() >= 5:
            raise ApplyError(f"{r.indicator_key} {r.date} is a weekend date")
        if (r.indicator_key, r.date) in seen:
            raise ApplyError(f"duplicate candidate {r.indicator_key} {r.date}")
        seen.add((r.indicator_key, r.date))
    return rows


def _window_counts(cur, keys: List[str], start: date, end: date) -> Dict[str, Tuple[int, int]]:
    """{key: (rows, weekend rows)} for US rows of `keys` in the window."""
    cur.execute("""
        SELECT indicator_key, COUNT(*), COUNT(*) FILTER (WHERE EXTRACT(ISODOW FROM date) >= 6)
        FROM macro_indicators
        WHERE country_code = 'US' AND indicator_key = ANY(%s) AND date BETWEEN %s AND %s
        GROUP BY indicator_key
    """, (keys, start, end))
    return {k: (n, w) for k, n, w in cur.fetchall()}


def _table_exists(cur, name: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (name,))
    return bool(cur.fetchone()[0])


def apply_redate(cur, rows: List[CandidateRow], keys: List[str], start: date, end: date,
                 snapshot: str) -> List[dict]:
    """Snapshot -> delete window rows -> insert candidates -> delete leftover weekend rows.

    Runs on the caller's transaction; the caller commits or rolls back. Returns per-key counts.
    """
    from psycopg2 import sql
    from psycopg2.extras import execute_values

    if not snapshot.startswith(SNAPSHOT_PREFIX):
        raise ApplyError(f"snapshot name must start with {SNAPSHOT_PREFIX}")
    if _table_exists(cur, snapshot):
        raise ApplyError(f"snapshot table {snapshot} already exists")

    before = _window_counts(cur, keys, start, end)
    cur.execute(sql.SQL("""
        CREATE TABLE {} AS SELECT * FROM macro_indicators
        WHERE country_code = 'US' AND indicator_key = ANY(%s) AND date BETWEEN %s AND %s
    """).format(sql.Identifier(snapshot)), (keys, start, end))
    snapshot_rows = cur.rowcount
    if snapshot_rows != sum(n for n, _ in before.values()):
        raise ApplyError(f"snapshot has {snapshot_rows} rows, expected {sum(n for n, _ in before.values())}")

    cur.execute("""
        DELETE FROM macro_indicators
        WHERE country_code = 'US' AND indicator_key = ANY(%s) AND date BETWEEN %s AND %s
    """, (keys, start, end))

    cfg = OpenBBMarketService.MACRO_INDICATORS
    execute_values(cur, """
        INSERT INTO macro_indicators (date, indicator_key, value, unit, category, country_code)
        VALUES %s
    """, [(r.date, r.indicator_key, r.value, cfg[r.indicator_key]["unit"],
           cfg[r.indicator_key]["category"], "US") for r in rows])

    cur.execute("""
        DELETE FROM macro_indicators
        WHERE country_code = 'US' AND indicator_key = ANY(%s) AND date BETWEEN %s AND %s
          AND EXTRACT(ISODOW FROM date) >= 6
    """, (keys, start, end))
    weekend_left = cur.rowcount
    if weekend_left:
        raise ApplyError(f"{weekend_left} weekend rows remained after insert")

    after = _window_counts(cur, keys, start, end)
    expected = {k: sum(1 for r in rows if r.indicator_key == k) for k in keys}
    report = []
    for k in sorted(keys):
        n_after = after.get(k, (0, 0))[0]
        if n_after != expected[k]:
            raise ApplyError(f"{k}: {n_after} rows after insert, expected {expected[k]}")
        n_before, w_before = before.get(k, (0, 0))
        report.append({"indicator_key": k, "rows_before": n_before, "weekend_before": w_before,
                       "rows_after": n_after})
    return report


def rollback_redate(cur, snapshot: str, start: date, end: date) -> List[dict]:
    """Restore the snapshot's series in the window exactly as they were."""
    from psycopg2 import sql

    if not snapshot.startswith(SNAPSHOT_PREFIX):
        raise ApplyError(f"snapshot name must start with {SNAPSHOT_PREFIX}")
    if not _table_exists(cur, snapshot):
        raise ApplyError(f"snapshot table {snapshot} does not exist")
    cur.execute(sql.SQL("SELECT DISTINCT indicator_key FROM {}").format(sql.Identifier(snapshot)))
    keys = sorted(k for (k,) in cur.fetchall())
    if not keys:
        raise ApplyError(f"snapshot table {snapshot} is empty")

    before = _window_counts(cur, keys, start, end)
    cur.execute("""
        DELETE FROM macro_indicators
        WHERE country_code = 'US' AND indicator_key = ANY(%s) AND date BETWEEN %s AND %s
    """, (keys, start, end))
    cur.execute(sql.SQL("INSERT INTO macro_indicators SELECT * FROM {}").format(sql.Identifier(snapshot)))
    after = _window_counts(cur, keys, start, end)
    return [{"indicator_key": k, "rows_before": before.get(k, (0, 0))[0], "weekend_before": before.get(k, (0, 0))[1],
             "rows_after": after.get(k, (0, 0))[0]} for k in keys]


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def _load_env_and_key() -> str:
    from dotenv import load_dotenv
    load_dotenv(project_root / ".env")
    return os.environ.get("FRED_API_KEY", "").strip()


def cmd_fetch(args) -> int:
    scope = in_scope_series()
    if args.keys:
        wanted = set(args.keys.split(","))
        unknown = wanted - scope.keys()
        if unknown:
            print(f"Not in scope: {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
        scope = {k: v for k, v in scope.items() if k in wanted}

    api_key = _load_env_and_key()
    if any(src == "fred_initial" for src, _ in scope.values()) and not api_key:
        print("FRED_API_KEY is not set", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dl = download_candidates(scope, args.start, args.end, api_key)

    write_candidates(out_dir / "candidates.csv", dl.candidates)
    _write_dicts(out_dir / "dropped.csv", ["indicator_key", "date", "reason"],
                 [{**d.model_dump(), "date": d.date.isoformat()} for d in dl.dropped])
    _write_dicts(out_dir / "revisions.csv", ["indicator_key", "symbol", "date", "initial", "latest"],
                 dl.revisions)
    print(f"\n{len(dl.candidates)} candidate rows, {len(dl.dropped)} dropped, "
          f"{len(dl.revisions)} FRED revisions -> {out_dir}")
    if dl.failed:
        print(f"No data for: {', '.join(sorted(dl.failed))}")
        return 1
    return 0


def cmd_check_public(args) -> int:
    api_key = _load_env_and_key()
    out_dir = Path(args.out_dir)
    with open(out_dir / "candidates.csv") as f:
        candidates = read_candidates(f)
    by_key: Dict[str, List[CandidateRow]] = {}
    for r in candidates:
        by_key.setdefault(r.indicator_key, []).append(r)

    scope = in_scope_series()
    print("Public references...")
    refs, ref_errors = build_references(args.start, args.end, api_key)
    for name, err in sorted(ref_errors.items()):
        print(f"  unavailable {name}: {err}")

    print(f"Overlap re-download {args.overlap_start} -> {args.overlap_end}...")
    overlap = download_candidates(scope, args.overlap_start, args.overlap_end, api_key, verbose=False)
    overlap_by_key: Dict[str, List[CandidateRow]] = {}
    for r in overlap.candidates:
        overlap_by_key.setdefault(r.indicator_key, []).append(r)
    prod = load_prod_csv(Path(args.prod_csv), args.overlap_start, args.overlap_end) if args.prod_csv else {}

    revisions_by_key: Dict[str, int] = {}
    revisions_path = out_dir / "revisions.csv"
    if revisions_path.exists():
        with open(revisions_path) as f:
            for row in csv.DictReader(f):
                revisions_by_key[row["indicator_key"]] = revisions_by_key.get(row["indicator_key"], 0) + 1

    results = []
    for key, (source, symbol) in sorted(scope.items()):
        rows = by_key.get(key, [])
        cand = rows_to_series(rows)
        key_refs = {n: refs[n] for n, _ in PUBLIC_REFERENCES.get(key, []) if n in refs}
        diff = is_rate(key)
        unavailable = [n for n, _ in PUBLIC_REFERENCES.get(key, []) if n in ref_errors]
        if rows:
            g1 = gate_g1(cand, key_refs, diff)
            g3 = gate_g3(key, cand, key_refs, diff)
        else:
            g1 = g3 = (FAIL, "no candidates")
        g4 = gate_g4(rows_to_series(overlap_by_key.get(key, [])), prod.get(key))
        g5 = gate_g5(rows, calendar_for(source, symbol, args.start, args.end))
        if unavailable:
            g3 = (g3[0], g3[1] + f"; unavailable: {', '.join(unavailable)}")
        verdict = FAIL if FAIL in (g1[0], g3[0], g4[0], g5[0]) else PASS
        results.append({"indicator_key": key, "source": source, "symbol": symbol, "rows": len(rows),
                        "g1": g1, "g3": g3, "g4": g4, "g5": g5, "verdict": verdict,
                        "revisions": revisions_by_key.get(key, 0)})

    _write_dicts(out_dir / "public_gates.csv",
                 ["indicator_key", "g1", "g3", "g4", "g5", "public_verdict"],
                 [{"indicator_key": r["indicator_key"], "g1": r["g1"][0], "g3": r["g3"][0],
                   "g4": r["g4"][0], "g5": r["g5"][0], "public_verdict": r["verdict"]} for r in results])
    write_report(out_dir / "redate_report.md", results, args, ref_errors, overlap.failed)
    for r in results:
        print(f"  {r['indicator_key']:<26} G1={r['g1'][0]:<4} G3={r['g3'][0]:<4} "
              f"G4={r['g4'][0]:<4} G5={r['g5'][0]:<4} -> {r['verdict']}")
    print(f"\nReport: {out_dir / 'redate_report.md'}")
    return 0


def write_report(path: Path, results: List[dict], args, ref_errors: Dict[str, str],
                 overlap_failed: Dict[str, str]) -> None:
    lines = [
        "# Macro history re-date: public gates",
        "",
        f"Window {args.start} → {args.end}. Overlap (G4) {args.overlap_start} → {args.overlap_end}.",
        "G2/G2f (private) are not part of this report. N/A is never a pass: a series",
        "with N/A here must pass the private gate.",
        "",
        "| Series | Source | Rows | G1 date | G3 public | G4 overlap | G5 complete | Public |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(f"| {r['indicator_key']} | {r['source']} {r['symbol']} | {r['rows']} | "
                     f"{r['g1'][0]} | {r['g3'][0]} | {r['g4'][0]} | {r['g5'][0]} | **{r['verdict']}** |")
    lines += ["", "## Details", ""]
    for r in results:
        lines.append(f"### {r['indicator_key']}")
        for gate in ("g1", "g3", "g4", "g5"):
            lines.append(f"- {gate.upper()} {r[gate][0]}: {r[gate][1]}")
        if r["revisions"]:
            lines.append(f"- FRED revisions since initial release: {r['revisions']} (see revisions.csv)")
        lines.append("")
    if ref_errors:
        lines += ["## Unavailable references", ""]
        lines += [f"- {n}: {e}" for n, e in sorted(ref_errors.items())] + [""]
    if overlap_failed:
        lines += ["## Overlap re-download failures", ""]
        lines += [f"- {k}: {e}" for k, e in sorted(overlap_failed.items())] + [""]
    path.write_text("\n".join(lines))


def _print_counts(report: List[dict]) -> None:
    print(f"  {'series':<26} {'rows before':>11} {'weekend before':>14} {'rows after':>10}")
    for r in report:
        print(f"  {r['indicator_key']:<26} {r['rows_before']:>11} {r['weekend_before']:>14} {r['rows_after']:>10}")
    print(f"  {'TOTAL':<26} {sum(r['rows_before'] for r in report):>11} "
          f"{sum(r['weekend_before'] for r in report):>14} {sum(r['rows_after'] for r in report):>10}")


def cmd_apply(args) -> int:
    from src.storage.database import DatabaseManager

    mode = "DRY-RUN (rolled back, nothing written)" if args.dry_run else "REAL RUN (commits)"
    db = DatabaseManager()
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                if args.rollback:
                    print(f"ROLLBACK from {args.rollback}, window {args.start}..{args.end} — {mode}")
                    report = rollback_redate(cur, args.rollback, args.start, args.end)
                    next_keys = [r["indicator_key"] for r in report]
                else:
                    keys = [k.strip() for k in args.series.split(",") if k.strip()]
                    fh = sys.stdin if args.candidates == "-" else open(args.candidates)
                    with fh:
                        rows = validate_apply_input(read_candidates(fh), keys, args.start, args.end)
                    snapshot = args.snapshot or SNAPSHOT_PREFIX + date.today().strftime("%Y%m%d")
                    print(f"APPLY {len(rows)} rows, {len(keys)} series, window {args.start}..{args.end}, "
                          f"snapshot {snapshot} — {mode}")
                    report = apply_redate(cur, rows, keys, args.start, args.end, snapshot)
                    next_keys = keys
                _print_counts(report)
                if args.dry_run:
                    conn.rollback()
                    print("\n[DRY-RUN] Rolled back — no data written.")
                    return 0
        print("\nCommitted. Next: recompute derived columns (dry-run first) for each series:")
        for k in next_keys:
            print(f"  python scripts/recompute_macro_derived.py --indicator {k} --since 2026-01-01 --dry-run")
        return 0
    except (ApplyError, ValueError) as e:
        print(f"REFUSED, nothing written: {e}", file=sys.stderr)
        return 2


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Re-date the daily macro history of a past window")
    sub = parser.add_subparsers(dest="command", required=True)

    def window(p, out_dir=True):
        p.add_argument("--start", type=_parse_date, default=date(2026, 1, 12))
        p.add_argument("--end", type=_parse_date, default=date(2026, 5, 31))
        if out_dir:
            p.add_argument("--out-dir", required=True)

    p = sub.add_parser("fetch", help="Download candidates from Yahoo and FRED (initial release)")
    window(p)
    p.add_argument("--keys", default=None, help="Comma-separated subset of in-scope keys")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("check-public", help="Public gates G1, G3, G4, G5 on out-dir/candidates.csv")
    window(p)
    p.add_argument("--prod-csv", default=None,
                   help="Prod extract (date, key|indicator_key, value) covering the overlap window")
    p.add_argument("--overlap-start", type=_parse_date, default=date(2026, 6, 1))
    p.add_argument("--overlap-end", type=_parse_date, default=date(2026, 9, 30))
    p.set_defaults(func=cmd_check_public)

    p = sub.add_parser("apply", help="Prod: snapshot and replace the approved series (one transaction)")
    window(p, out_dir=False)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--series", help="Comma-separated approved series (names only)")
    group.add_argument("--rollback", metavar="TABLE", help="Restore a snapshot table instead")
    p.add_argument("--candidates", default="-", help="candidates.csv path, or - for stdin (default)")
    p.add_argument("--snapshot", default=None, help=f"Snapshot table (default {SNAPSHOT_PREFIX}<today>)")
    p.add_argument("--dry-run", action="store_true", help="Do everything, print counts, roll back")
    p.set_defaults(func=cmd_apply)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
