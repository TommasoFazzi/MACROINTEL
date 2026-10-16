"""Tests for src/nlp/storyline_attribution.py (storyline-formation-causal-review task
5.1): one test per primary cause, the order between causes, expired-orphan
inheritance, and the two spec scenarios."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from src.nlp import storyline_attribution as at
from src.nlp import storyline_replay as rp
from src.nlp.config import NarrativeClusteringConfig

T0 = datetime(2026, 9, 1, 7, tzinfo=timezone.utc)
THR = 0.75


def _cand(score, **kw):
    return {"storyline_id": 1, "cosine": score, "time_penalty": 0.0, "boost": 0.0, "score": score} | kw


def _rec(outcome="orphan", orphan_id=None, **kw):
    return rp.DecisionRecord(run_ts=T0, event_article_ids=[1], outcome=outcome, orphan_id=orphan_id, **kw)


def _result(records, orphans=()):
    return rp.ReplayResult(records=list(records), storylines={}, links={}, members={}, link_time={},
                           orphans=[], orphan_log={o.id: o for o in orphans},
                           run_times=[T0, T0 + timedelta(days=20)], scored_since=T0)


def _orphan(oid, emb, day, end_day=None, end=None):
    return rp.OrphanEvent(id=oid, article_ids=[oid], title="", embedding=np.asarray(emb, float),
                          entities=[], category=None, created_at=T0 + timedelta(days=day),
                          ended_at=(T0 + timedelta(days=end_day)) if end_day is not None else None, end=end)


def _cause(rec, orphans=()):
    res = _result([rec], orphans)
    at.attribute(res, THR)
    return res.records[0]


@pytest.mark.unit
def test_never_considered():
    r = _cause(_rec("never_considered"))
    assert (r.primary_cause, r.flags) == ("never_considered", ["H7"])


@pytest.mark.unit
def test_archived_candidate_spec_scenario():
    """Spec: best candidate under prod scoring is a 2-article storyline archived by
    rule_4 and would clear the threshold → archived_candidate (even if H1 also holds)."""
    r = _cause(_rec(best_any_status=_cand(0.80, status="archived", archived_by="rule_4"),
                    best_active_no_penalty=_cand(0.90)))
    assert r.primary_cause == "archived_candidate"
    assert set(r.flags) == {"H8", "H1"}


@pytest.mark.unit
def test_archived_by_rule_3_is_not_h8():
    r = _cause(_rec(best_any_status=_cand(0.80, status="archived", archived_by="rule_3")))
    assert r.primary_cause == "residual"


@pytest.mark.unit
def test_stabilized_candidate():
    r = _cause(_rec(best_any_status=_cand(0.78, status="stabilized", archived_by=None)))
    assert (r.primary_cause, r.flags) == ("stabilized_candidate", ["H2"])


@pytest.mark.unit
def test_time_penalty_spec_scenario():
    """Spec: best active candidate scores 0.70 including a 0.15 penalty → time_penalty."""
    r = _cause(_rec(best_active=_cand(0.70, time_penalty=0.15),
                    best_active_no_penalty=_cand(0.85),
                    best_any_status=_cand(0.70, status="active", archived_by=None)))
    assert (r.primary_cause, r.flags) == ("time_penalty", ["H1"])


@pytest.mark.unit
def test_boost_lost():
    r = _cause(_rec(best_active_no_penalty=_cand(0.70), best_active_creation_boost=_cand(0.76)))
    assert (r.primary_cause, r.flags) == ("boost_lost", ["H4"])


@pytest.mark.unit
def test_orphan_twin_requires_later_twin_before_end():
    e = np.eye(4)
    orphans = [
        _orphan(1, e[0], day=0, end_day=14, end="expired"),
        _orphan(2, e[0] + 0.1 * e[1], day=3),                  # twin of 1, within its life
        _orphan(3, e[2], day=0, end_day=2, end="recovered"),
        _orphan(4, e[2], day=5),                               # twin of 3, but after 3 ended
    ]
    twins = at.orphans_with_twin(_result([], orphans))
    assert twins == {1}
    r = _cause(_rec("orphan", orphan_id=1), orphans)
    assert (r.primary_cause, r.flags) == ("orphan_twin", ["H3"])


@pytest.mark.unit
def test_overlap_flags_need_penalty_removal():
    """An H8-archived storyline that clears only without the penalty is flagged H8xH1
    but is not the primary cause (L8 alone would not fix it)."""
    r = _cause(_rec(best_archived_h8=_cand(0.60, status="archived", archived_by="rule_4",
                                           score_no_penalty=0.95, time_penalty=0.35),
                    best_stabilized=_cand(0.80, status="stabilized", archived_by=None,
                                          score_no_penalty=0.85)))
    assert r.primary_cause == "residual"
    assert r.flags == ["H8xH1"]          # stabilized one already clears with the penalty: not an overlap


@pytest.mark.unit
def test_residual():
    r = _cause(_rec("new_storyline", best_active_no_penalty=_cand(0.60)))
    assert (r.primary_cause, r.flags) == ("residual", [])


@pytest.mark.unit
def test_expired_orphan_inherits_cause_of_pooling_record():
    pooled = _rec("orphan", orphan_id=7, best_active_no_penalty=_cand(0.80))
    expired = _rec("orphan_expired", orphan_id=7)
    res = _result([pooled, expired], [_orphan(7, np.eye(4)[0], day=0, end_day=14, end="expired")])
    at.attribute(res, THR)
    assert res.records[1].primary_cause == "time_penalty"
    assert res.records[1].flags == ["H1"]


@pytest.mark.unit
def test_waterfall_on_replay_stream():
    """End to end on the replay's hand-built stream: the day-16 article that misses
    its storyline only because of the time penalty is attributed to H1."""
    from tests.test_nlp.test_storyline_replay import _stream

    arts, runs = _stream()
    res = rp.StorylineReplay(arts, NarrativeClusteringConfig(), burn_in_days=0).run(runs)
    at.attribute(res, THR)
    rec30 = next(r for r in res.records if r.event_article_ids == [30])
    assert rec30.primary_cause == "time_penalty"
    w = at.waterfall(res)
    assert w["fragmentation"]["by_cause"]["time_penalty"]["events"] >= 1
    assert sum(v["events"] for v in w["fragmentation"]["by_cause"].values()) == w["fragmentation"]["n_events"]
