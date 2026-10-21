"""Candidate formation vectors and the D3 gate (storyline-representation-track)."""

import numpy as np
import pytest

from src.nlp import formation_representation as fr


def _vec(*xs):
    return np.array(xs, dtype=np.float64)


def test_build_from_means_falls_back_to_baseline():
    base = {1: _vec(3, 4), 2: _vec(0, 2)}
    emb, fallback = fr.build_from_means([1, 2, 3], {1: _vec(1, 0)}, base)
    assert fallback.tolist() == [False, True, True]
    assert np.allclose(emb[0], [1, 0]) and np.allclose(emb[1], [0, 1])
    assert not emb[2].any()          # neither candidate nor baseline


def test_build_from_means_refuses_mixed_spaces():
    with pytest.raises(ValueError):
        fr.build_from_means([1, 2], {1: _vec(1, 0, 0)}, {2: _vec(1, 0)})


def test_load_vectors_requires_alignment(tmp_path):
    path = tmp_path / "formation_X.npz"
    np.savez(path, ids=np.array([1, 2]), embedding=np.eye(2, dtype=np.float32), fallback=np.zeros(2, bool))
    assert set(fr.load_vectors(path, [1, 2])) == {1, 2}
    with pytest.raises(ValueError):
        fr.load_vectors(path, [2, 1])


def _setup():
    """Four J rows and four S rows. In `good`, positives are near-identical and
    negatives orthogonal; in `bad`, every pair has the same cosine."""
    members = {"J": {}, "S": {}}
    gold = {"J": {}, "S": {}}
    good, bad = {}, {}
    aid = 0
    for row, positive in enumerate([True, True, False, False, True]):
        a, s1, s2 = aid, aid + 1, aid + 2
        aid += 3
        good[a] = _vec(1, 0)
        good[s1] = good[s2] = _vec(1, 0.05) if positive else _vec(0, 1)
        bad[a], bad[s1], bad[s2] = _vec(1, 0), _vec(1, 1), _vec(1, 1)
        members["J"][str(row)] = {"article": a, "storyline": [s1, s2]}
        members["S"][str(row)] = {"left": [a], "right": [s1, s2]}
        gold["J"][str(row)] = "yes" if positive else "no"
        gold["S"][str(row)] = "same" if positive else "different"
    gold["J"]["4"] = "unsure"
    return gold, members, good, bad


def test_gate_self_check_is_zero_and_fails():
    gold, members, good, _ = _setup()
    out = fr.auc_gate(gold, members, good, good, n_boot=50)
    assert out["S"]["delta"] == 0 and out["J"]["delta"] == 0
    assert out["J"]["n"] == 4                # the unsure row is dropped
    assert out["passes"] is False


def test_gate_passes_a_separating_candidate():
    gold, members, good, bad = _setup()
    out = fr.auc_gate(gold, members, bad, good, n_boot=200)
    assert out["S"]["auc_base"] == 0.5 and out["S"]["auc_candidate"] == 1.0
    assert out["passes"] is True
    # and the reverse direction fails
    assert fr.auc_gate(gold, members, good, bad, n_boot=50)["passes"] is False


def _replay_stream():
    from tests.test_nlp.test_storyline_replay import _stream
    return _stream()


def test_formation_vectors_equal_to_baseline_reproduce_the_replay():
    from src.nlp import storyline_replay as rp
    from src.nlp.config import NarrativeClusteringConfig

    arts, runs = _replay_stream()
    same = fr.with_formation_vectors(arts, {a.id: a.embedding for a in arts})
    base = rp.StorylineReplay(arts, NarrativeClusteringConfig()).run(runs)
    cand = rp.StorylineReplay(same, NarrativeClusteringConfig()).run(runs)
    assert [r.model_dump() for r in cand.records] == [r.model_dump() for r in base.records]


def test_formation_vectors_drive_the_decisions():
    """Article 21 is an orphan under the baseline; a candidate that places it next to
    storyline X makes it join X. Nothing else of the article changes."""
    from src.nlp import storyline_replay as rp
    from src.nlp.config import NarrativeClusteringConfig

    arts, runs = _replay_stream()
    vectors = {a.id: a.embedding for a in arts}
    vectors[21] = vectors[20]
    cand = fr.with_formation_vectors(arts, vectors)
    assert [(a.id, a.title, a.created_at) for a in cand] == [(a.id, a.title, a.created_at) for a in arts]

    def outcome(arts_, aid):
        result = rp.StorylineReplay(arts_, NarrativeClusteringConfig()).run(runs)
        return next(r.outcome for r in result.records if aid in r.event_article_ids)

    assert outcome(arts, 21) == "orphan"
    assert outcome(cand, 21) == "joined"


def test_article_without_candidate_vector_is_unembedded():
    arts, _ = _replay_stream()
    cand = fr.with_formation_vectors(arts, {})
    assert all(a.embedding is None for a in cand)


def test_matched_threshold_calibrates_to_itself_and_follows_monotone_maps():
    rng = np.random.default_rng(0)
    cos = rng.uniform(0.3, 1.0, 20000)
    assert abs(fr.matched_threshold(cos, cos, 0.90) - 0.90) < 0.005
    assert abs(fr.matched_threshold(cos, cos, 0.75) - 0.75) < 0.005
    assert abs(fr.matched_threshold(cos, cos ** 2, 0.90) - 0.81) < 0.005
    # a larger share above threshold means a lower threshold
    assert fr.matched_threshold(cos, cos, 0.90, 1.25) < fr.matched_threshold(cos, cos, 0.90, 0.75)


def test_calibration_pairs_on_a_replay():
    from src.nlp import storyline_replay as rp
    from src.nlp.config import NarrativeClusteringConfig

    arts, runs = _replay_stream()
    result = rp.StorylineReplay(arts, NarrativeClusteringConfig(), burn_in_days=0).run(runs)
    vectors = {a.id: a.embedding for a in arts}
    pairs = fr.calibration_pairs(result, vectors, vectors)
    assert len(pairs["micro"]["base"]) > 0 and len(pairs["match"]["base"]) > 0
    assert np.allclose(pairs["micro"]["base"], pairs["micro"]["cand"])
    out = fr.calibrate(pairs, 0.90, 0.75)
    assert set(out) == {"n_pairs", "matched", "share_x0.75", "share_x1.25"}


def test_co_assignment_known_merge_is_one():
    links = {1: {7}, 2: {7}, 3: {7}, 4: {8}}
    assert fr.co_assignment(links, [1, 2], [3]) == 1.0
    assert fr.co_assignment(links, [1], [3, 4]) == 0.5
    assert fr.co_assignment(links, [1], [1]) is None


def test_co_assignment_result_benefit_and_guard():
    members = {str(i): {"left": [10 * i], "right": [10 * i + 1]} for i in range(6)}
    gold = {"0": "same", "1": "same", "2": "same", "3": "different", "4": "different", "5": "unsure"}
    split = {a: {a} for i in range(6) for a in (10 * i, 10 * i + 1)}
    merged_same = split | {a: {99 + i} for i in range(3) for a in (10 * i, 10 * i + 1)}
    out = fr.co_assignment_result(gold, members, split, merged_same, n_boot=100)
    assert out["same"]["base"] == 0.0 and out["same"]["candidate"] == 1.0
    assert out["benefit"] is False            # gain relative to a zero base is undefined
    half = split | {0: {50}, 1: {50}}
    out = fr.co_assignment_result(gold, members, half, merged_same, n_boot=100)
    assert out["benefit"] is True and out["different"]["delta"] == 0.0
    merge_all = {a: {1} for a in split}
    assert fr.co_assignment_result(gold, members, half, merge_all, n_boot=100)["benefit"] is False
