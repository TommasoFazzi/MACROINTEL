"""Tests for scripts/export_formation_snapshot.py (storyline-formation-causal-review,
task 3.2): SELECT only, all files written, manifest consistent, undated and
unembedded articles kept."""

from __future__ import annotations

import csv
import json
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

import scripts.export_formation_snapshot as ex  # noqa: E402
from scripts.export_clustering_snapshot import _sha256  # noqa: E402

T0 = datetime(2026, 10, 1, 7, 0, tzinfo=timezone.utc)


def _article_rows():
    rows = []
    for i in range(6):
        vec = None if i == 4 else [float(i)] * 384          # one article not embedded
        published = None if i == 5 else T0                  # one undated article
        rows.append((100 + i, f"title {i}", published, T0, vec,
                     {"clean": {"all": ["Iran", "Hormuz"]}}, "geo", "Reuters"))
    return rows


class _FakeCursor:
    def __init__(self, log):
        self.log = log
        self._rows = []

    def execute(self, sql, params=None):
        self.log.append((sql, params))
        s = " ".join(sql.split())
        if s.startswith("SELECT current_setting"):
            self._rows = [("UTC", T0)]
        elif "FROM articles a WHERE" in s:
            self._rows = _article_rows()
        elif s.startswith("SELECT als.article_id"):
            self._rows = [(100, 7, True, datetime(2026, 10, 1, 7, 5), datetime(2026, 10, 1, 7, 5), "emerging", 2),
                          (101, 7, False, datetime(2026, 10, 1, 7, 5), datetime(2026, 10, 1, 7, 5), "emerging", 2)]
        elif s.startswith("SELECT s.id"):
            self._rows = [(7, "Hormuz", "Summary", "emerging", datetime(2026, 10, 1, 7, 5),
                           datetime(2026, 10, 1, 7, 5), 2)]
        elif "FROM narrative_run_metrics" in s:
            self._rows = [(datetime(2026, 10, 1, 7, 10), "r1", 300.0, '{"matched": 1}', '{"rule_1": 0}')]
        elif "FROM orphan_events" in s:
            self._rows = [(1, [102, 103], datetime(2026, 10, 1, 7, 6), 3, datetime(2026, 10, 2, 7, 6))]
        else:
            raise AssertionError(f"unexpected query: {s[:80]}")

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeDB:
    def __init__(self):
        self.log = []

    @contextmanager
    def get_connection(self):
        log = self.log

        class _Conn:
            def cursor(self):
                return _FakeCursor(log)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        yield _Conn()


@pytest.mark.unit
def test_export_select_only_and_files(tmp_path):
    db = _FakeDB()
    m = ex.export(db, tmp_path, "abc123", days=90)

    for sql, params in db.log:
        assert sql.lstrip().split(None, 1)[0].upper() in ("SELECT", "WITH")
        if "%s" in sql:
            assert params == (90,)
    for name in ex.DATA_FILES + ["manifest.json"]:
        assert (tmp_path / name).exists(), name
    for name, digest in m["file_sha256"].items():
        assert _sha256(tmp_path / name) == digest

    assert m["n_articles"] == 6
    assert m["n_articles_embedded"] == 5
    assert m["n_articles_undated"] == 1
    assert m["db_timezone"] == "UTC"
    assert json.loads((tmp_path / "manifest.json").read_text()) == m


@pytest.mark.unit
def test_export_keeps_undated_and_unembedded_articles(tmp_path):
    ex.export(_FakeDB(), tmp_path, "abc123", days=90)
    z = np.load(tmp_path / "articles.npz")
    assert z["ids"].tolist() == [100, 101, 102, 103, 104, 105]
    assert z["has_embedding"].tolist() == [True, True, True, True, False, True]
    assert z["embedding"].shape == (6, 384)
    assert z["embedding"][3, 0] == 3.0

    rows = [json.loads(line) for line in (tmp_path / "articles.jsonl").read_text().splitlines()]
    assert rows[5]["published_date"] is None
    assert rows[0]["entities"] == {"clean": {"all": ["Iran", "Hormuz"]}}

    with open(tmp_path / "orphans.csv") as f:
        orphan = list(csv.DictReader(f))[0]
    assert orphan["article_ids"] == "102 103"


@pytest.mark.unit
def test_execute_refuses_writes():
    class _Cur:
        def execute(self, *a):
            raise AssertionError("must not reach the database")

    with pytest.raises(RuntimeError):
        ex._execute(_Cur(), "DELETE FROM orphan_events")
