"""Tests for src/nlp/storyline_replay.py (storyline-formation-causal-review tasks
4.1–4.4): a hand-built stream with known joins, orphans and expiries; prod's
early return on empty runs; loader predicate; determinism; decay predicates
pinned to prod's SQL text; fidelity report on a synthetic snapshot."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from src.nlp import narrative_processor as npm
from src.nlp import storyline_replay as rp
from src.nlp.config import FormationLevers, NarrativeClusteringConfig

DAY0 = datetime(2026, 9, 1, 7, 0, tzinfo=timezone.utc)
DIM = 8


def _e(i):
    v = np.zeros(DIM)
    v[i] = 1.0
    return v


def _art(aid, emb, day, title=None, published=True, embedded=True, hours_before=2):
    t = DAY0 + timedelta(days=day) - timedelta(hours=hours_before)
    return rp.ReplayArticle(id=aid, title=title or f"Iran talks {aid}",
                            published_date=t if published else None, created_at=t,
                            embedding=np.asarray(emb, dtype=float) if embedded else None,
                            entities={}, category="geo")


def _stream():
    """Day 0: two groups of three events (X around e0, Y around e4) → 2 storylines.
    Day 1: one article near X → joins X; one unrelated article → orphan.
    Day 10: no article → nothing runs. Day 16: one article near Y does NOT join:
    Y has been silent 16 days, so the linear time penalty (0.05/day = 0.80) sinks
    the score below 0.75 (hypothesis H1); it becomes an orphan. The day-1 orphan
    is older than 14 days → expired."""
    arts = [_art(1 + i, _e(0) + 0.55 * _e(1 + i), 0) for i in range(3)]
    arts += [_art(10 + i, _e(4) + 0.55 * _e(5 + i), 0) for i in range(3)]
    arts += [_art(20, _e(0) + 0.1 * _e(1), 1), _art(21, _e(1) - _e(0), 1)]
    arts += [_art(30, _e(4) + 0.1 * _e(5), 16)]
    runs = [DAY0 + timedelta(days=d) for d in (0, 1, 10, 16)]
    return arts, runs


def _replay(arts, **kw):
    return rp.StorylineReplay(arts, NarrativeClusteringConfig(), **kw)


def _outcomes(result):
    return {(r.run_ts, tuple(r.event_article_ids)): r.outcome for r in result.records}


@pytest.mark.unit
def test_hand_built_stream():
    arts, runs = _stream()
    result = _replay(arts).run(runs)
    out = _outcomes(result)

    x = result.links[1].pop() if len(result.links[1]) == 1 else None
    y = next(iter(result.links[10]))
    assert x is not None and x != y
    assert {1, 2, 3} == result.members[x] - {20}
    assert {10, 11, 12} == result.members[y] - {30}

    assert out[(runs[1], (20,))] == "joined" and result.links[20] == {x}
    assert out[(runs[1], (21,))] == "orphan"
    assert out[(runs[3], (30,))] == "orphan"
    rec30 = next(r for r in result.records if r.event_article_ids == [30])
    assert rec30.best_active["storyline_id"] == y
    assert rec30.best_active["time_penalty"] == pytest.approx(0.80)
    assert rec30.best_active_no_penalty["score"] >= 0.75     # clears without the penalty
    assert out[(runs[3], (21,))] == "orphan_expired"
    assert 21 not in result.links and [o.article_ids for o in result.orphans] == [[30]]

    sx, sy = result.storylines[x], result.storylines[y]
    assert (sx.article_count, sx.status) == (4, "active")          # promoted at the day-1 join
    assert (sy.article_count, sy.status) == (3, "emerging")       # born with 3, never joined
    assert sx.last_update == runs[1] and sy.last_update == runs[0]
    # rule_1 ran on day 16 only (day 10 had no articles: prod returns before decay)
    assert sx.momentum == pytest.approx(min(1.0, 0.5 + 0.1) * 0.7)


@pytest.mark.unit
def test_empty_run_skips_retry_and_decay():
    """Day 10 loads nothing, so the day-1 orphan (9 days old) is untouched and no
    storyline decays; only a run with articles expires or decays."""
    arts, runs = _stream()
    r = _replay(arts)
    for now in runs[:3]:
        r.run_once(now)
    assert [o.article_ids for o in r.orphans] == [[21]] and r.orphans[0].retry_count == 0
    assert all(s.momentum >= 0.5 for s in r.storylines.values())


@pytest.mark.unit
def test_loader_predicate_and_never_considered():
    """Prod's loader: embedding present, published within the last day of the run,
    created before it. Undated, unembedded and late articles are never considered."""
    arts = [
        _art(1, _e(0), 0),
        _art(2, _e(0), 0, published=False),                    # undated
        _art(3, _e(0), 0, embedded=False),                     # no embedding
        _art(4, _e(0), 0, hours_before=30),                    # published 30 h before the run
    ]
    result = _replay(arts).run([DAY0])
    never = {r.event_article_ids[0] for r in result.records if r.outcome == "never_considered"}
    assert never == {2, 3, 4}
    assert any(r.outcome == "orphan" and r.event_article_ids == [1] for r in result.records)


@pytest.mark.unit
def test_rule_4_archives_small_storyline_and_records_any_status_candidate():
    """A 2-event storyline that gets no join is archived by rule_4 after 5 days; a
    later event close to it reports it as best_any_status (an H8 candidate)."""
    arts = [_art(1, _e(0) + 0.55 * _e(1), 0), _art(2, _e(0) + 0.55 * _e(2), 0),
            _art(5, _e(4) + 0.55 * _e(5), 0), _art(6, _e(4) + 0.55 * _e(6), 0),
            _art(3, _e(7), 6), _art(4, _e(0) + 0.3 * _e(1) + 0.3 * _e(2), 7)]
    # day 6: article 3 triggers a run, whose decay archives the 2-article storyline;
    # day 7: article 4 finds it only among archived storylines
    result = _replay(arts).run([DAY0, DAY0 + timedelta(days=6), DAY0 + timedelta(days=7)])
    sid = next(iter(result.links[1]))
    assert result.storylines[sid].archived_by == "rule_4"
    rec = next(r for r in result.records if r.event_article_ids == [4])
    assert rec.outcome in ("orphan", "new_storyline")
    assert rec.best_any_status["storyline_id"] == sid
    assert rec.best_any_status["status"] == "archived"
    assert rec.best_any_status["archived_by"] == "rule_4"


@pytest.mark.unit
def test_off_topic_hook_archives_updated_storylines():
    arts = [_art(1 + i, _e(0) + 0.55 * _e(1 + i), 0, title="Champions League final") for i in range(3)]
    arts += [_art(10 + i, _e(4) + 0.55 * _e(5 + i), 0) for i in range(3)]
    result = _replay(arts).run([DAY0])
    by_title = {s.title.startswith("Champions"): s for s in result.storylines.values()}
    assert by_title[True].archived_by == "off_topic"
    assert by_title[False].status == "emerging"


@pytest.mark.unit
def test_single_group_day_creates_no_storyline():
    """HDBSCAN's default allow_single_cluster=False: when every unmatched event of a
    run belongs to one story, prod creates no storyline and all of them go to the
    orphan pool. Recorded as a level-1 finding; the replay reproduces it."""
    arts = [_art(1 + i, _e(0) + 0.55 * _e(1 + i), 0) for i in range(3)]
    result = _replay(arts).run([DAY0])
    assert not result.storylines
    assert sorted(o.article_ids[0] for o in result.orphans) == [1, 2, 3]


@pytest.mark.unit
def test_deterministic():
    arts, runs = _stream()
    a = _replay(arts).run(runs)
    b = _replay(arts).run(runs)
    assert [r.model_dump() for r in a.records] == [r.model_dump() for r in b.records]


@pytest.mark.unit
def test_non_default_levers_refused_until_implemented():
    with pytest.raises(NotImplementedError, match="match_stabilized"):
        _replay([], levers=FormationLevers(match_stabilized=True))


def _norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


@pytest.mark.unit
def test_decay_predicates_pinned_to_prod_sql():
    """task 4.2b: each Python predicate constant appears in the SQL prod runs."""
    r1, r2, r3, r4, r5 = (_norm(getattr(npm, f"DECAY_RULE_{i}_SQL")) for i in range(1, 6))
    assert f"WHERE narrative_status IN ('emerging', 'active') AND last_update < NOW() - INTERVAL '{rp.RULE_1_STALE_DAYS} days'" in r1
    assert "SET momentum_score = momentum_score * %s" in r1
    assert npm.NarrativeProcessor.__init__.__code__.co_consts.count(rp.MOMENTUM_DECAY_FACTOR) == 1
    assert f"WHERE narrative_status = 'active' AND momentum_score < {rp.RULE_2_MOMENTUM_BELOW}" in r2
    assert "SET narrative_status = 'stabilized'" in r2
    assert f"WHERE narrative_status = 'stabilized' AND last_update < NOW() - INTERVAL '{rp.RULE_3_STABILIZED_DAYS} days'" in r3
    assert (f"WHERE narrative_status = 'emerging' AND article_count < {rp.RULE_4_MIN_ARTICLES} "
            f"AND created_at < NOW() - INTERVAL '{rp.RULE_4_DAYS} days'") in r4
    assert "WHERE narrative_status = 'emerging' AND last_update < NOW() - MAKE_INTERVAL(days => %s)" in r5
    assert f"WHERE created_at < NOW() - INTERVAL '{rp.ORPHAN_TTL_DAYS} days'" in _norm(npm.ORPHAN_EXPIRY_SQL)
    for sql in (r3, r4, r5):
        assert "SET narrative_status = 'archived'" in sql


@pytest.mark.unit
def test_fidelity_report_on_identical_partition():
    """When prod linked exactly what the replay linked, ARI is 1 and coverage matches."""
    arts, runs = _stream()
    result = _replay(arts, burn_in_days=0).run(runs)
    result.scored_since = DAY0 - timedelta(days=1)      # score the whole stream
    prod_links = {aid: {100 + min(sids)} for aid, sids in result.links.items()}
    prod_storylines = {100 + sid: {"id": 100 + sid, "title": s.title, "summary": "Iran talks",
                                   "narrative_status": s.status, "created_at": s.created_at}
                       for sid, s in result.storylines.items()}
    snap = rp.FormationSnapshot(arts, prod_links, prod_storylines, runs)
    rep = rp.fidelity_report(result, snap)
    assert rep.ari_co_membership == pytest.approx(1.0)
    assert rep.coverage_replay == rep.coverage_prod
    assert rep.new_storylines_per_day_replay == rep.new_storylines_per_day_prod
    assert rep.off_topic_divergence == 0


@pytest.mark.unit
def test_export_roundtrip_and_cli(tmp_path):
    """Snapshot written by the export script loads, replays and reports."""
    import json as _json

    import scripts.storyline_replay as cli
    from tests.test_nlp.test_export_formation_snapshot import _FakeDB
    import scripts.export_formation_snapshot as ex

    snap_dir = tmp_path / "snap"
    ex.export(_FakeDB(), snap_dir, "abc", days=90)
    snap = rp.load_formation_snapshot(snap_dir)
    assert len(snap.articles) == 6 and snap.prod_links == {100: {7}, 101: {7}}
    assert snap.run_starts[0].tzinfo is not None

    assert cli.main(["fidelity", str(snap_dir), "--burn-in", "0", "--out", str(tmp_path / "out")]) == 0
    (run_dir,) = (tmp_path / "out").iterdir()
    report = _json.loads((run_dir / "fidelity.json").read_text())["report"]
    assert set(report) >= {"ari_co_membership", "coverage_replay", "coverage_prod", "off_topic_divergence"}
    assert (run_dir / "records.jsonl").read_text()

    (snap_dir / "links.csv").write_text("tampered")
    with pytest.raises(ValueError, match="SHA-256"):
        rp.load_formation_snapshot(snap_dir)
