"""Unit tests for the local study script (clustering-offline-validation-study,
tasks 6.1, 6.8, 6.8b). The full run is exercised manually on a snapshot."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

import scripts.clustering_validation_study as study  # noqa: E402


def test_check_versions_aborts_on_mismatch(tmp_path):
    req = tmp_path / "requirements-prod.txt"
    req.write_text("scikit-learn==0.0.1\numap-learn==0.5.12  # pinned\nnumpy==2.3.5\n")
    with pytest.raises(SystemExit) as exc:
        study.check_versions(req)
    assert "scikit-learn" in str(exc.value) and "0.0.1" in str(exc.value)


def test_check_versions_passes_on_prod_pins():
    versions = study.check_versions()
    assert {"scikit-learn", "umap-learn", "numpy", "python"} <= set(versions)


def test_palette_overflow_counts_storylines_beyond_15_largest():
    # 17 clusters: sizes 10 (x15), then 3 and 2; plus 5 noise points
    part, sid = {}, 0
    for lab, size in enumerate([10] * 15 + [3, 2]):
        for _ in range(size):
            part[sid] = lab
            sid += 1
    for _ in range(5):
        part[sid] = -1
        sid += 1
    assert study.palette_overflow(part, len(part)) == pytest.approx(5 / len(part))


def test_subsamples_are_fixed_and_sized():
    ids = list(range(100))
    a, b = study.subsample_ids(ids), study.subsample_ids(ids)
    assert a == b
    assert len(a) == study.N_SUBSAMPLES and all(len(s) == 80 for s in a)


def test_coassignment_shares_over_runs_containing_both():
    pytest.importorskip("matplotlib")
    from scripts import clustering_study_figures as figs
    runs = [{1: 0, 2: 0, 3: 1}, {1: 0, 2: 1, 3: 1}, {1: 5, 3: 5}]
    C = figs.coassignment([1, 2, 3], runs)
    assert C[0, 1] == pytest.approx(0.5)      # together in 1 of the 2 runs with both
    assert C[0, 2] == pytest.approx(1 / 3)
    assert C[1, 2] == pytest.approx(0.5)


def test_coassignment_ignores_noise():
    pytest.importorskip("matplotlib")
    from scripts import clustering_study_figures as figs
    C = figs.coassignment([1, 2], [{1: -1, 2: -1}])
    assert C[0, 1] == 0.0
    assert np.isfinite(C).all()
