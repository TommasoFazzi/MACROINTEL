"""Tests for src/nlp/clustering_validation.py (clustering-offline-validation-study,
tasks 3.1-3.8)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.nlp import clustering_validation as cv  # noqa: E402


# ---------------------------------------------------------------------------
# Stability (3.1)
# ---------------------------------------------------------------------------

def test_pairwise_ari_identical_and_relabelled_partitions_are_perfect():
    p = {i: i // 5 for i in range(20)}
    relabelled = {i: 9 - (i // 5) for i in range(20)}
    stats = cv.pairwise_ari_on_intersection([p, p, relabelled])
    assert stats.n_pairs == 3
    assert stats.mean_ari == pytest.approx(1.0)
    assert stats.p10_ari == pytest.approx(1.0)


def test_pairwise_ari_uses_id_intersection_and_skips_tiny_overlaps():
    a = {i: i // 5 for i in range(20)}
    b = {i: i // 5 for i in range(10, 30)}       # overlap 10..19, same structure
    c = {100: 0, 101: 1}                           # no overlap with a or b
    stats = cv.pairwise_ari_on_intersection([a, b, c])
    assert stats.n_pairs == 1
    assert stats.mean_ari == pytest.approx(1.0)


def test_hdbscan_stability_mostly_noise_shows_low_coverage():
    # 90% noise in every run; the 10% core is perfectly stable
    runs = []
    for _ in range(3):
        p = {i: cv.NOISE for i in range(100)}
        p.update({i: i // 5 for i in range(10)})
        runs.append(p)
    stats = cv.pairwise_ari_hdbscan(runs)
    assert stats.mean_ari == pytest.approx(1.0)
    assert stats.coverage_mean == pytest.approx(0.10)
    assert stats.noise_ratio_p90 == pytest.approx(0.90)
    assert stats.noise_agreement == pytest.approx(1.0)


def test_hdbscan_stability_noise_jaccard_on_shared_ids():
    a = {0: cv.NOISE, 1: cv.NOISE, 2: 0, 3: 0, 4: 1, 5: 1}
    b = {0: cv.NOISE, 1: 0, 2: 0, 3: cv.NOISE, 4: 1, 5: 1}
    stats = cv.pairwise_ari_hdbscan([a, b])
    # noise sets {0,1} vs {0,3}: Jaccard 1/3
    assert stats.noise_agreement == pytest.approx(1 / 3)


# ---------------------------------------------------------------------------
# DBCV (3.2, 3.4)
# ---------------------------------------------------------------------------

def _blobs(seed=0, sep=10.0, n=40, dim=2):
    rng = np.random.default_rng(seed)
    X = np.vstack([rng.normal(loc=c * sep, scale=0.5, size=(n, dim)) for c in range(3)])
    y = np.repeat(np.arange(3), n)
    return X, y


def test_core_distance_log_space_matches_naive_formula():
    rng = np.random.default_rng(1)
    X = rng.normal(size=(15, 3))
    from scipy.spatial.distance import pdist, squareform
    dist = squareform(pdist(X))
    dim = 3
    naive = dist.copy()
    naive[naive != 0] = (1.0 / naive[naive != 0]) ** dim
    naive = (naive.sum(axis=1) / (dist.shape[0] - 1)) ** (-1.0 / dim)
    assert cv._all_points_core_distance(dist, dim) == pytest.approx(naive, rel=1e-10)


def test_core_distance_no_overflow_in_ten_dimensions():
    rng = np.random.default_rng(2)
    X = rng.normal(scale=1e-6, size=(30, 10))     # tiny distances: d^-10 overflows naively
    from scipy.spatial.distance import pdist, squareform
    core = cv._all_points_core_distance(squareform(pdist(X)), 10)
    assert np.all(np.isfinite(core)) and np.all(core > 0)


def test_dbcv_well_separated_beats_touching_and_lies_in_range():
    X_far, y = _blobs(sep=10.0)
    X_near, _ = _blobs(sep=1.0)
    far, near = cv.dbcv(X_far, y), cv.dbcv(X_near, y)
    assert -1.0 <= near < far <= 1.0
    assert far > 0.5


def test_dbcv_noise_counts_in_denominator():
    X, y = _blobs()
    clean = cv.dbcv(X, y)
    y_noise = np.concatenate([y, np.full(60, cv.NOISE)])
    X_noise = np.vstack([X, np.random.default_rng(5).uniform(-5, 25, size=(60, 2))])
    # same clusters, 60 extra noise points out of 180: score shrinks by 120/180
    assert cv.dbcv(X_noise, y_noise) == pytest.approx(clean * 120 / 180, rel=0.05)


@pytest.mark.parametrize("labels", [np.full(30, cv.NOISE), np.zeros(30, dtype=int)])
def test_dbcv_degenerate_returns_none(labels):
    X = np.random.default_rng(3).normal(size=(30, 2))
    assert cv.dbcv(X, labels) is None


def test_dbcv_drops_duplicate_rows_without_failing():
    X, y = _blobs()
    X_dup = np.vstack([X, X[:5]])
    y_dup = np.concatenate([y, y[:5]])
    assert cv.dbcv(X_dup, y_dup) == pytest.approx(cv.dbcv(X, y))


# Reference values from hdbscan.validity.validity_index (task 3.3), computed
# 2026-10-15 in a throwaway venv (hdbscan from PyPI, scikit-learn 1.9.1,
# Python 3.12) on the datasets below, saved from this venv so both sides read
# identical arrays. Ours matched to ~1e-14. The real 500-point UMAP sample is
# added after the snapshot export (task 4.6).
HDBSCAN_REFERENCE: dict[str, float] = {
    "separated": 0.9419576255046156,
    "touching": 0.2397988349322782,
    "uniform": -0.47823199827844953,
    "separated_10d": 0.7707563520035423,
}


@pytest.mark.skipif(not HDBSCAN_REFERENCE, reason="reference values not recorded yet (task 3.3)")
@pytest.mark.parametrize("name", sorted(HDBSCAN_REFERENCE) or ["placeholder"])
def test_dbcv_matches_hdbscan_reference(name):
    X, y = _reference_dataset(name)
    assert cv.dbcv(X, y) == pytest.approx(HDBSCAN_REFERENCE[name], abs=1e-3)


def _reference_dataset(name):
    if name == "separated":
        return _blobs(seed=10, sep=10.0)
    if name == "touching":
        return _blobs(seed=11, sep=1.5)
    if name == "separated_10d":
        return _blobs(seed=13, sep=3.0, dim=10)
    if name == "uniform":
        rng = np.random.default_rng(12)
        return rng.uniform(size=(120, 2)), np.repeat(np.arange(3), 40)
    raise KeyError(name)


# ---------------------------------------------------------------------------
# Pair metrics and bootstrap (3.5)
# ---------------------------------------------------------------------------

def test_pair_metrics_hand_computed():
    partition = {1: 0, 2: 0, 3: 1, 4: 1, 5: cv.NOISE}
    pairs = cv.LabelledPairs(
        a=[1, 3, 1, 5, 2],
        b=[2, 4, 3, 1, 4],
        stratum=["S-A"] * 5,
        gold=[True, False, True, True, False],
    )
    pred = cv.predict_same(partition, pairs)
    assert pred.tolist() == [True, True, False, False, False]
    m = cv.point_metrics(pred, pairs)["S-A"]
    # TP=1 (1-2), FP=1 (3-4), FN=2 (1-3, 5-1)
    assert m["precision"] == pytest.approx(0.5)
    assert m["recall"] == pytest.approx(1 / 3)
    assert m["f1"] == pytest.approx(2 * 1 / (2 * 1 + 1 + 2))


def test_noise_pair_labelled_same_is_false_negative():
    pairs = cv.LabelledPairs(a=[1], b=[2], stratum=["S-N"], gold=[True])
    pred = cv.predict_same({1: 0, 2: cv.NOISE}, pairs)
    m = cv.point_metrics(pred, pairs)["S-N"]
    assert pred.tolist() == [False]
    assert m["recall"] == 0.0 and m["precision"] is None


def test_macro_excludes_s_n():
    strata = ["S-A"] * 4 + ["S-B"] * 4 + ["S-C"] * 4 + ["S-N"] * 4
    gold = [True, True, False, False] * 4
    pairs = cv.LabelledPairs(a=list(range(16)), b=list(range(16)), stratum=strata, gold=gold)
    pred = np.array(gold)
    pred[12:] = ~pred[12:]                         # S-N completely wrong
    m = cv.point_metrics(pred, pairs)
    assert m["macro"]["f1"] == pytest.approx(1.0)
    assert m["S-N"]["f1"] == pytest.approx(0.0)


def _paired_scenario(extra_errors_per_stratum=3, seed=7):
    rng = np.random.default_rng(seed)
    strata, gold = [], []
    for s in cv.MACRO_STRATA:
        strata += [s] * 50
        gold += list(rng.random(50) < 0.5)
    gold = np.array(gold)
    pairs = cv.LabelledPairs(a=list(range(150)), b=list(range(150)), stratum=strata, gold=gold)
    pred_a = gold.copy()
    flip = rng.choice(150, size=15, replace=False)
    pred_a[flip] = ~pred_a[flip]
    pred_b = pred_a.copy()
    for s in range(3):                              # B adds misses on correct A positives
        idx = [i for i in range(s * 50, s * 50 + 50) if gold[i] and pred_a[i]]
        pred_b[idx[:extra_errors_per_stratum]] = False
    return pairs, pred_a, pred_b


def test_paired_interval_excludes_zero_while_marginal_intervals_overlap():
    pairs, pred_a, pred_b = _paired_scenario()
    resamples = cv.bootstrap_indices(pairs, n_boot=1000, seed=1)
    boot_a = cv.bootstrap_f1(pred_a, pairs, resamples)["macro"]
    boot_b = cv.bootstrap_f1(pred_b, pairs, resamples)["macro"]
    ci_a, ci_b = cv.ci95(boot_a), cv.ci95(boot_b)
    assert ci_a[0] <= ci_b[1] and ci_b[0] <= ci_a[1], "marginal intervals should overlap"
    lo, _ = cv.ci95(boot_a - boot_b)
    assert lo > 0


def test_bootstrap_is_stratified():
    pairs, pred_a, _ = _paired_scenario()
    for sample in cv.bootstrap_indices(pairs, n_boot=20, seed=2):
        for s, idx in sample.items():
            assert idx.size == 50
            assert all(pairs.stratum[i] == s for i in idx)


def test_label_metrics_models_validate():
    pairs, pred_a, _ = _paired_scenario()
    resamples = cv.bootstrap_indices(pairs, n_boot=50, seed=3)
    rows = cv.label_metrics(pred_a, pairs, cv.bootstrap_f1(pred_a, pairs, resamples))
    assert {r.stratum for r in rows} == {"S-A", "S-B", "S-C", "macro"}
    assert all(r.f1_ci95 is not None for r in rows)


# ---------------------------------------------------------------------------
# Rater reliability (3.6)
# ---------------------------------------------------------------------------

def test_rater_agreement_hand_computed():
    first = ["same"] * 5 + ["different"] * 5
    second = ["same"] * 4 + ["different"] * 6
    kappa, agree = cv.rater_agreement(first, second)
    # p_o = 0.9, p_e = 0.5*0.4 + 0.5*0.6 = 0.5 -> kappa = 0.8
    assert agree == pytest.approx(0.9)
    assert kappa == pytest.approx(0.8)


def test_rater_agreement_single_category_perfect():
    kappa, agree = cv.rater_agreement(["same"] * 4, ["same"] * 4)
    assert kappa == 1.0 and agree == 1.0


# ---------------------------------------------------------------------------
# D7 selection rule (3.7) — one test per verdict-table row
# ---------------------------------------------------------------------------

_COMMON = np.random.default_rng(42).normal(0, 0.05, 2000)


def _cand(key, algo, f1, data=0.9, seed=0.95, noise=None, k=None, jitter_seed=0):
    jitter = np.random.default_rng(jitter_seed).normal(0, 0.003, 2000)
    params = {"k": k} if algo == "kmeans" else {"min_cluster_size": int(key.split("=")[1])}
    return cv.Candidate(
        key=key, algorithm=algo, params=params, seed_ari=seed, data_ari=data,
        macro_f1=f1, boot_macro_f1=f1 + _COMMON + jitter,
        noise_full=noise, noise_p90=noise,
    )


def _km(f1, **kw):
    return _cand("kmeans:k=18", "kmeans", f1, k=18, **kw)


def _hd(f1, mcs=10, **kw):
    kw.setdefault("noise", 0.10)
    return _cand(f"hdbscan_umap:mcs={mcs}", "hdbscan_umap", f1, jitter_seed=mcs, **kw)


def test_verdict_both_eligible_clear_gain_switches():
    d = cv.decide([_km(0.60), _hd(0.72)])
    assert d.verdict == "hdbscan_umap"
    cross = [x for x in d.deltas if x.purpose == "cross_algorithm"]
    assert cross[0].ci95[0] > 0


def test_verdict_both_eligible_small_gain_keeps_kmeans():
    d = cv.decide([_km(0.60), _hd(0.62)])       # real but below MIN_DELTA_F1
    assert d.verdict == "kmeans"


def test_verdict_only_kmeans_eligible():
    d = cv.decide([_km(0.60), _hd(0.90, noise=0.35)])
    assert d.verdict == "kmeans"
    assert "hdbscan_umap" not in d.selected


def test_verdict_only_hdbscan_eligible_with_clear_gain():
    d = cv.decide([_km(0.60, data=0.40, seed=0.50), _hd(0.72)])
    assert d.verdict == "hdbscan_umap"


def test_verdict_only_hdbscan_eligible_without_gain_is_inconclusive():
    d = cv.decide([_km(0.60, data=0.40, seed=0.50), _hd(0.61)])
    assert d.verdict == "inconclusive"


def test_verdict_nothing_eligible_is_inconclusive_with_reasons():
    km, hd = _km(0.60, data=0.40, seed=0.50), _hd(0.70, noise=0.50)
    d = cv.decide([km, hd])
    assert d.verdict == "inconclusive"
    assert km.reasons and hd.reasons


def test_noise_gate_precedes_stability_bar():
    over_noisy = _hd(0.70, mcs=25, data=0.95, seed=0.97, noise=0.40)
    usable = _hd(0.70, mcs=10, data=0.85, seed=0.90, noise=0.15)
    cv.decide([_km(0.60), over_noisy, usable])
    assert not over_noisy.eligible
    assert usable.eligible, usable.reasons


def test_kmeans_tie_goes_to_incumbent_k18():
    k15 = _cand("kmeans:k=15", "kmeans", 0.613, k=15, jitter_seed=15)
    k18 = _cand("kmeans:k=18", "kmeans", 0.61, k=18, jitter_seed=18)
    d = cv.decide([k15, k18])
    assert set(d.tie_sets["kmeans"]) == {"kmeans:k=15", "kmeans:k=18"}
    assert d.selected["kmeans"].params["k"] == 18


def test_relative_bar_uses_floor_when_all_unstable():
    a = _cand("kmeans:k=12", "kmeans", 0.6, k=12, data=0.58, seed=0.9)
    b = _cand("kmeans:k=15", "kmeans", 0.6, k=15, data=0.55, seed=0.9)
    d = cv.decide([a, b])
    assert not a.eligible and not b.eligible
    assert d.verdict == "inconclusive"
