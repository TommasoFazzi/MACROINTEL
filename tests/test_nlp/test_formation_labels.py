"""Tests for src/nlp/formation_labels.py (storyline-formation-causal-review tasks
6.3, 6.3b): stratified estimator and paired bootstrap, sampler, pools."""

from __future__ import annotations

import numpy as np
import pytest

from src.nlp import formation_labels as fl
from src.nlp import storyline_attribution as at
from src.nlp import storyline_replay as rp
from src.nlp.config import NarrativeClusteringConfig


@pytest.mark.unit
def test_oversampled_stratum_does_not_bias_precision():
    """Spec: a lever adds 40 joins and 100 of 150 labelled J pairs come from the
    added stratum → the lever's precision weighs that stratum by 40 joins."""
    labels = fl.StratumLabels({"J-base": np.array([1] * 45 + [0] * 5),      # 0.90
                               "J-H1": np.array([1] * 50 + [0] * 50)})      # 0.50
    lever = {"J-base": 1000, "J-H1": 40}
    p = fl.stratified_precision(lever, labels)
    assert p == pytest.approx((1000 * 0.9 + 40 * 0.5) / 1040)
    naive = (45 + 50) / 150
    assert abs(p - naive) > 0.2


@pytest.mark.unit
def test_paired_delta_bound():
    labels = fl.StratumLabels({"J-base": np.array([1] * 85 + [0] * 15),
                               "J-H1": np.array([1] * 85 + [0] * 15)})
    same = fl.paired_delta({"J-base": 2300, "J-H1": 2800}, {"J-base": 2300}, labels, n_boot=500)
    assert same["delta"] == pytest.approx(0.0)
    assert same["ci95"][0] < 0 < same["ci95"][1]
    worse = fl.StratumLabels({"J-base": labels.by_stratum["J-base"],
                              "J-H1": np.array([1] * 55 + [0] * 45)})
    bad = fl.paired_delta({"J-base": 2300, "J-H1": 2800}, {"J-base": 2300}, worse, n_boot=500)
    assert bad["delta"] < -0.1 and not bad["passes_bound"]


@pytest.mark.unit
def test_removed_joins_change_the_weights():
    """A lever that removes baseline joins has a smaller J-base count."""
    labels = fl.StratumLabels({"J-base": np.array([1, 1, 1, 0]), "J-H1": np.array([1, 0])})
    assert fl.stratified_precision({"J-base": 10, "J-H1": 10}, labels) == pytest.approx(0.625)
    with pytest.raises(ValueError, match="no labels"):
        fl.stratified_precision({"J-base": 5, "J-H8": 3}, labels)


@pytest.mark.unit
def test_sample_strata_sizes_and_no_duplicates():
    pool = [{"stratum": s, "article_id": i % 30, "storyline_title": "t", "left_id": None, "right_id": None}
            for i, s in enumerate(["J-base"] * 40 + ["J-H1"] * 40)]
    out = fl.sample_strata(pool, {"J-base": 20, "J-H1": 20}, seed=1)
    keys = [(x["article_id"], x["storyline_title"]) for x in out]
    assert len(keys) == len(set(keys))
    assert sum(x["stratum"] == "J-base" for x in out) == 20
    assert sum(x["stratum"] == "J-H1" for x in out) <= 20       # duplicates of J-base are skipped


@pytest.mark.unit
def test_pools_on_replay_stream():
    """The hand-built stream's day-16 article (H1) lands in J-H1 against storyline Y,
    with the titles Y had before the decision."""
    from tests.test_nlp.test_storyline_replay import _stream

    arts, runs = _stream()
    res = rp.StorylineReplay(arts, NarrativeClusteringConfig(), burn_in_days=0).run(runs)
    at.attribute(res, 0.75)
    titles = {a.id: a.title for a in arts}
    pool = fl.j_pool(res, "spacy_boost", titles)
    h1 = [x for x in pool if x["stratum"] == "J-H1" and x["article_id"] == 30]
    assert len(h1) == 1
    y = next(iter(res.links[10]))
    assert h1[0]["storyline_id"] == y
    assert set(h1[0]["recent_titles"]) == {titles[10], titles[11], titles[12]}
    assert any(x["stratum"] == "J-base" and x["article_id"] == 20 for x in pool)
