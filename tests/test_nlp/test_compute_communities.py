"""
Unit tests for scripts/compute_communities.py — focused on `_persist_run_metrics`.

Regression test for the 2026-08-12 fix: `drift_signals` (migration 043) was
never wired into `_persist_run_metrics`'s base_cols/base_vals, so the column
stayed permanently NULL for every caller (Louvain and theme_clustering.py
alike). Since `_is_refit_due()`/`count_consecutive_drift_signals()` in
theme_clustering.py both query this column, the bug silently forced
`refit_due` to always be True (bootstrap fallback) and made k-retune
permanently unreachable — for a month, every day took the expensive periodic
re-fit path instead of the cheap daily nearest-centroid path.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import scripts.compute_communities as cc  # noqa: E402


class _RecordingCursor:
    def __init__(self):
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _RecordingConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _RecordingDB:
    def __init__(self):
        self._cursor = _RecordingCursor()

    def get_connection(self):
        return _RecordingConn(self._cursor)


def test_persist_run_metrics_writes_drift_signals_column():
    db = _RecordingDB()
    drift_result = {
        "drift": True,
        "reasons": ["tcs_drop"],
        "latest": {"tcs": 0.5, "coherence_med": 0.4, "epr": 0.9},
        "baseline": {"tcs_p50": 0.8, "coherence_med_p50": 0.45, "epr_p50": 0.9},
    }
    stats = {
        "run_id": "test-run-id",
        "storylines_total": 100,
        "nodes": 100,
        "drift_signals": drift_result,
    }

    cc._persist_run_metrics(db, stats, dry_run=False)

    query, params = db._cursor.executed[0]
    assert "drift_signals" in query
    col_names = [c.strip() for c in query.split("(")[1].split(")")[0].split(",")]
    assert "drift_signals" in col_names
    idx = col_names.index("drift_signals")
    # psycopg2.extras.Json wraps the dict — compare its .adapted payload, not identity.
    assert params[idx].adapted == drift_result


def test_persist_run_metrics_drift_signals_null_when_absent():
    """Louvain's own calls never set stats["drift_signals"] — must write NULL,
    not raise, matching pre-fix behavior for every other optional field."""
    db = _RecordingDB()
    stats = {"run_id": "test-run-id-2", "storylines_total": 50, "nodes": 50}

    cc._persist_run_metrics(db, stats, dry_run=False)

    query, params = db._cursor.executed[0]
    col_names = [c.strip() for c in query.split("(")[1].split(")")[0].split(",")]
    idx = col_names.index("drift_signals")
    assert params[idx] is None


def test_persist_run_metrics_dry_run_is_noop():
    db = _RecordingDB()
    cc._persist_run_metrics(db, {"run_id": "x"}, dry_run=True)
    assert db._cursor.executed == []
