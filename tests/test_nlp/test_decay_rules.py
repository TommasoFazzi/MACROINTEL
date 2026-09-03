"""Lifecycle decay rule tests for the fix-emerging-storyline-lifecycle-leak change.

Covers rule_5, the catch-all that closes the 'emerging' dead-end: a storyline
born with >= 3 articles that never receives another one satisfied neither the
promotion path (needs a new article) nor rule_4 (needs article_count < 3), so it
stayed 'emerging' forever. Production had 1828 such rows.

`_apply_decay()` is pure SQL, so these tests drive it through a fake
DatabaseManager that records every statement and replays a scripted rowcount per
rule. That keeps them `unit` — the real predicates are asserted against the SQL
text, which is exactly what regressed.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Any, Optional

import pytest

from src.nlp.narrative_processor import NarrativeProcessor


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeCursor:
    """Records executed statements and returns a scripted rowcount per call."""

    def __init__(self, rowcounts: list[int], log: list[tuple[str, Optional[tuple]]]):
        self._rowcounts = list(rowcounts)
        self._log = log
        self.rowcount = 0

    def execute(self, sql: str, params: Optional[tuple] = None) -> None:
        self._log.append((sql, params))
        self.rowcount = self._rowcounts.pop(0) if self._rowcounts else 0

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor
        self.committed = False

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.committed = True

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class _FakeDB:
    def __init__(self, rowcounts: list[int]):
        self.log: list[tuple[str, Optional[tuple]]] = []
        self.cursor = _FakeCursor(rowcounts, self.log)
        self.connection = _FakeConnection(self.cursor)

    @contextmanager
    def get_connection(self):
        yield self.connection


def _processor(rowcounts: list[int]) -> tuple[NarrativeProcessor, _FakeDB]:
    """Build a processor with no LLM and a scripted DB."""
    db = _FakeDB(rowcounts)
    proc = NarrativeProcessor(db_manager=db, skip_llm=True)
    return proc, db


def _statement_for(db: _FakeDB, rule_index: int) -> tuple[str, Optional[tuple]]:
    """Statements are emitted in rule order: rule_1..rule_5 (0-based index)."""
    return db.log[rule_index]


RULE_1, RULE_2, RULE_3, RULE_4, RULE_5 = range(5)


def _norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


# ---------------------------------------------------------------------------
# Task 3.1 — the case that escaped before rule_5
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_emerging_with_three_articles_and_stale_is_archived():
    """Spec scenario: emerging, article_count >= 3, last_update past the threshold.

    This is the class of storyline that had no exit at all. rule_5 must catch it,
    and must do so WITHOUT looking at article_count.
    """
    proc, db = _processor([0, 0, 0, 0, 7])
    stats = proc._apply_decay()

    sql, params = _statement_for(db, RULE_5)
    assert "narrative_status = 'archived'" in sql
    assert "WHERE narrative_status = 'emerging'" in _norm(sql)
    # The whole point: no article_count gate. Reintroducing one recreates the bug.
    assert "article_count" not in sql
    assert params == (proc.EMERGING_TTL_DAYS,)
    assert stats["rule_5"] == 7


@pytest.mark.unit
def test_rule_5_threshold_comes_from_config():
    """The threshold is wired to decay.emerging_ttl_days, not hardcoded."""
    proc, db = _processor([0, 0, 0, 0, 0])
    proc._apply_decay()

    _, params = _statement_for(db, RULE_5)
    assert params == (proc.EMERGING_TTL_DAYS,)
    # 30 mirrors rule_3's stabilized window so there is a single abandonment horizon.
    assert proc.EMERGING_TTL_DAYS == 30


# ---------------------------------------------------------------------------
# Task 3.2 — rule_4 keeps its own lane
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_small_stale_emerging_is_counted_by_rule_4_not_rule_5():
    """Spec scenario: article_count < 3 and older than 5 days is rule_4's job.

    rule_4 runs first and flips the row out of 'emerging', so rule_5's UPDATE no
    longer matches it. That ordering is what makes rule_5's counter measure
    exactly the leak and nothing else.
    """
    proc, db = _processor([0, 0, 0, 4, 0])
    stats = proc._apply_decay()

    assert stats["rule_4"] == 4
    assert stats["rule_5"] == 0

    rule4_sql, _ = _statement_for(db, RULE_4)
    assert "article_count < 3" in rule4_sql

    # Ordering is load-bearing, not incidental.
    assert db.log.index(_statement_for(db, RULE_4)) < db.log.index(_statement_for(db, RULE_5))


@pytest.mark.unit
def test_rule_4_predicate_is_unchanged():
    """rule_4 must keep its created_at/5-day semantics — rule_5 does not replace it."""
    proc, db = _processor([0, 0, 0, 0, 0])
    proc._apply_decay()

    sql, _ = _statement_for(db, RULE_4)
    norm = _norm(sql)
    assert "narrative_status = 'emerging'" in norm
    assert "article_count < 3" in norm
    assert "created_at < NOW() - INTERVAL '5 days'" in norm


# ---------------------------------------------------------------------------
# Task 3.3 — recent rows are untouched
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_recent_emerging_is_not_archived():
    """Spec scenario: emerging within the threshold → no transition.

    Expressed as the SQL contract: rule_5 only ever matches rows whose
    last_update is older than the threshold, so a recent row cannot be hit.
    """
    proc, db = _processor([0, 0, 0, 0, 0])
    stats = proc._apply_decay()

    sql, _ = _statement_for(db, RULE_5)
    assert "last_update < NOW() - MAKE_INTERVAL(days => %s)" in _norm(sql)
    assert stats["rule_5"] == 0


# ---------------------------------------------------------------------------
# Task 3.4 — an active storyline is never caught by the stale rule
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_rule_5_only_targets_emerging():
    """A storyline promoted to 'active' (it received an article, which also
    refreshes last_update) is out of rule_5's reach: the rule is scoped to
    'emerging' only."""
    proc, db = _processor([0, 0, 0, 0, 0])
    proc._apply_decay()

    sql = _norm(_statement_for(db, RULE_5)[0])
    where = sql.split("WHERE", 1)[1]
    assert "narrative_status = 'emerging'" in where
    # Only 'emerging' rows are selected — 'archived' appears in the SET clause,
    # which is why this asserts against the WHERE clause alone.
    for other in ("'active'", "'stabilized'", "'archived'"):
        assert f"narrative_status = {other}" not in where


@pytest.mark.unit
def test_promotion_to_active_refreshes_last_update():
    """The promotion path sets last_update = NOW() alongside the status change,
    which is what keeps an actively-updated storyline permanently clear of the
    stale rule."""
    import inspect

    src = inspect.getsource(NarrativeProcessor._assign_event_to_storyline)
    assert "narrative_status = %s" in src
    assert "last_update = NOW()" in src
    assert "new_status = 'active'" in src


# ---------------------------------------------------------------------------
# Task 3.5 — the counter is always present
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_rule_5_key_present_even_when_zero():
    """decay_stats is persisted verbatim, so a missing key would silently drop the
    metric. It must exist on every run, including quiet ones."""
    proc, _ = _processor([0, 0, 0, 0, 0])
    stats = proc._apply_decay()

    assert "rule_5" in stats
    assert stats["rule_5"] == 0
    assert set(stats) == {"rule_1", "rule_2", "rule_3", "rule_4", "rule_5", "reverse_promo"}


@pytest.mark.unit
def test_all_five_rules_execute_in_order():
    """Guards against a rule being accidentally dropped or reordered."""
    proc, db = _processor([1, 2, 3, 4, 5])
    stats = proc._apply_decay()

    assert [stats[f"rule_{i}"] for i in range(1, 6)] == [1, 2, 3, 4, 5]
    assert len(db.log) == 5
    assert db.connection.committed
