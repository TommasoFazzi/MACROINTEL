"""Tests for scripts/export_clustering_snapshot.py (clustering-offline-validation-study,
task 4.3): read-only statements, all files written, manifest validates."""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

import scripts.export_clustering_snapshot as ex  # noqa: E402
from src.nlp.clustering_validation import SnapshotManifest  # noqa: E402


def _storyline_rows(n=40, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        center = np.zeros(384)
        center[i % 4] = 5.0
        cur = (center + rng.normal(scale=0.1, size=384)).tolist()
        summ = rng.normal(size=384).tolist() if i % 10 else None   # some missing summary vectors
        cur = None if i == 39 else cur                               # one missing embedding
        rows.append((i + 1, f"title {i}", "summary", ["iran"], 3, "active",
                     datetime(2026, 9, 1), cur, summ, i % 5, i % 18, None))
    return rows


class _FakeCursor:
    def __init__(self, log):
        self.log = log
        self._rows = []

    def execute(self, sql, params=None):
        self.log.append(sql)
        s = " ".join(sql.split())
        if s.startswith("SELECT id, title"):
            self._rows = _storyline_rows()
        elif "jsonb_array_elements" in s:
            self._rows = [(datetime(2026, 10, 1), "r1", "community_detection", "kmeans_embedding", "silhouette", "0.17")]
        elif "FROM narrative_themes" in s:
            self._rows = [(1, "Iran", "active", [0.0] * 384)]
        elif "date_trunc('month'" in s:
            self._rows = [("active", "2026-09", "3-5", 40)]
        elif "LEFT JOIN" in s:
            self._rows = [(0,)]
        elif s.startswith("WITH orphaned"):
            self._rows = [("2026-09-28", 100, 80, 10, 5, 4, 1)]
        elif "FROM orphan_events" in s:
            self._rows = [(12, [3.0, 11.0], [2.0, 9.0], 15)]
        elif "pipeline_step = 'narrative_processing'" in s:
            self._rows = [(datetime(2026, 10, 1), "r2", 3000, 2300, 4, "{}", "{}", 120.0)]
        else:
            raise AssertionError(f"unexpected query: {s[:80]}")

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, log):
        self.log = log

    def cursor(self):
        return _FakeCursor(self.log)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeDB:
    def __init__(self):
        self.log = []

    @contextmanager
    def get_connection(self):
        yield _FakeConn(self.log)


def test_export_is_select_only_and_writes_all_files(tmp_path):
    db = _FakeDB()
    manifest = ex.export(db, tmp_path / "snap", git_sha="abc123")

    assert db.log, "no statement executed"
    for sql in db.log:
        assert sql.lstrip().split(None, 1)[0].upper() in ("SELECT", "WITH")

    out = tmp_path / "snap"
    for name in ["snapshot.npz", "storylines.jsonl", "run_metrics.csv", "themes.jsonl",
                 "l1_baseline.json", "l1_run_metrics.csv", "manifest.json"]:
        assert (out / name).exists(), name

    m = SnapshotManifest.model_validate(json.loads((out / "manifest.json").read_text()))
    assert m.n_storylines == 40
    assert m.n_missing_current_embedding == 1
    assert m.n_missing_summary_vector == 4
    assert m.n_active_themes == 1
    assert len(m.reference_fit.labels) == 39
    assert m.file_sha256["snapshot.npz"] == ex._sha256(out / "snapshot.npz")
    assert manifest["git_sha"] == "abc123"


def test_snapshot_masks_missing_embeddings_and_keeps_null_ids_as_nan(tmp_path):
    ex.export(_FakeDB(), tmp_path, git_sha="x")
    snap = np.load(tmp_path / "snapshot.npz")
    assert not snap["has_current_embedding"][-1]
    assert np.all(snap["current_embedding"][-1] == 0)
    assert np.all(np.isnan(snap["community_id_shadow"]))
    lines = (tmp_path / "storylines.jsonl").read_text().splitlines()
    assert len(lines) == 40                         # storyline without embedding is kept


def test_reference_fit_inertia_matches_kmeans(tmp_path):
    from sklearn.cluster import KMeans
    manifest = ex.export(_FakeDB(), tmp_path, git_sha="x")
    snap = np.load(tmp_path / "snapshot.npz")
    X = snap["current_embedding"][snap["has_current_embedding"]]
    km = KMeans(n_clusters=ex.REFERENCE_K, random_state=ex.REFERENCE_RANDOM_STATE, n_init=10).fit(X)
    assert manifest["reference_fit"]["inertia"] == pytest.approx(km.inertia_, rel=1e-4)


def test_execute_refuses_writes():
    with pytest.raises(RuntimeError):
        ex._execute(_FakeCursor([]), "UPDATE storylines SET title = 'x'")


def test_canonical_hash_ignores_label_names():
    assert ex.canonical_labels_sha256([3, 3, 7, 1]) == ex.canonical_labels_sha256([0, 0, 5, 9])
    assert ex.canonical_labels_sha256([0, 1, 0]) != ex.canonical_labels_sha256([0, 0, 1])
