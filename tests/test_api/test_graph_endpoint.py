"""Graph endpoint payload-budget tests for the fix-emerging-storyline-lifecycle-leak change.

The endpoint used to return every active storyline plus every edge above the
weight threshold, with no LIMIT and no time window. Because edges grow with the
SQUARE of the node pool, the response degraded super-linearly as the corpus grew:
2402 nodes / 41344 edges / 5.82 MB by Aug 2026, past both the landing page's fetch
budget and Next.js's 2 MB data-cache entry limit.

These tests drive the endpoint through a fake DatabaseManager so they stay `unit`.
"""

from __future__ import annotations

import asyncio
import re
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Optional

import pytest

from src.api.routers import stories as stories_mod
from src.api.routers.stories import (
    GraphView,
    MAX_EDGES_DEFAULT,
    MAX_NODES_DEFAULT,
    get_graph_network,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

def _node_row(sid: int, momentum: float, community: int | None = 1) -> tuple:
    """Row shaped like the endpoint's node SELECT (13 columns, in order)."""
    return (
        sid,                       # id
        f"Storyline {sid}",        # title
        f"summary {sid}",          # summary
        ["Entity A", "Entity B"],  # key_entities
        "active",                  # narrative_status
        "geopolitics",             # category
        10,                        # article_count
        momentum,                  # momentum_score
        date(2026, 1, 1),          # start_date
        datetime(2026, 8, 1),      # last_update
        30,                        # days_active
        community,                 # community_id
        "Some Community",          # community_name
    )


class _FakeCursor:
    def __init__(self, node_rows: list[tuple], edge_rows: list[tuple], log: list):
        self._node_rows = node_rows
        self._edge_rows = edge_rows
        self._log = log
        self._last = None

    def execute(self, sql: str, params: Optional[tuple] = None) -> None:
        self._log.append((sql, params))
        self._last = "nodes" if "FROM storylines" in sql else "edges"
        if self._last == "nodes":
            limit = params[1] if params and len(params) > 1 else len(self._node_rows)
            rows = self._node_rows[:limit]
            # Mirror what Postgres would return for the slim projection: the two
            # heavy columns are selected as literal NULL, so they never leave the DB.
            if "NULL AS summary" in sql:
                rows = [r[:2] + (None, None) + r[4:] for r in rows]
            self._result = rows
        else:
            selected = set(params[1]) if params and len(params) > 1 else set()
            limit = params[3] if params and len(params) > 3 else len(self._edge_rows)
            self._result = [
                e for e in self._edge_rows
                if e[0] in selected and e[1] in selected
            ][:limit]

    def fetchall(self) -> list[tuple]:
        return self._result

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        pass

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class _FakeDB:
    def __init__(self, node_rows: list[tuple], edge_rows: list[tuple]):
        self.log: list = []
        self._cursor = _FakeCursor(node_rows, edge_rows, self.log)
        self._conn = _FakeConnection(self._cursor)

    @contextmanager
    def get_connection(self):
        yield self._conn


@pytest.fixture(autouse=True)
def _clear_cache():
    stories_mod._graph_cache.clear()
    yield
    stories_mod._graph_cache.clear()


def _call(monkeypatch, node_rows, edge_rows, **kwargs) -> tuple[dict, _FakeDB]:
    db = _FakeDB(node_rows, edge_rows)
    monkeypatch.setattr(stories_mod, "get_db", lambda: db)
    params = dict(
        min_edge_weight=0.10,
        min_momentum=0.0,
        view=GraphView.full,
        max_nodes=MAX_NODES_DEFAULT,
        max_edges=MAX_EDGES_DEFAULT,
        api_key="test",
    )
    params.update(kwargs)
    resp = asyncio.run(get_graph_network(**params))
    return resp, db


# ---------------------------------------------------------------------------
# Task 6.1 — slim projection
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_slim_projection_omits_heavy_fields(monkeypatch):
    """summary and key_entities are 27% of the payload and unread by the landing."""
    rows = [_node_row(1, 0.9)]
    resp, db = _call(monkeypatch, rows, [], view=GraphView.slim)

    node_sql = db.log[0][0]
    assert "NULL AS summary" in node_sql
    assert "NULL AS key_entities" in node_sql

    node = resp["data"]["nodes"][0]
    assert node["summary"] is None
    assert node["key_entities"] == []


@pytest.mark.unit
def test_slim_projection_keeps_every_field_the_landing_reads(monkeypatch):
    """The fields consumed by web-platform/lib/landing/live.ts must survive."""
    resp, _ = _call(monkeypatch, [_node_row(1, 0.9)], [], view=GraphView.slim)
    node = resp["data"]["nodes"][0]

    for field in ("id", "title", "category", "momentum_score", "community_id",
                  "community_name", "article_count", "start_date", "days_active"):
        assert field in node, f"landing reads {field}"
    assert node["id"] == 1
    assert node["community_name"] == "Some Community"


@pytest.mark.unit
def test_full_projection_still_transfers_heavy_fields(monkeypatch):
    """Regression guard: /dashboard and /stories must not lose data."""
    resp, db = _call(monkeypatch, [_node_row(1, 0.9)], [], view=GraphView.full)

    node_sql = db.log[0][0]
    assert "NULL AS summary" not in node_sql
    node = resp["data"]["nodes"][0]
    assert node["summary"] == "summary 1"
    assert node["key_entities"] == ["Entity A", "Entity B"]


# ---------------------------------------------------------------------------
# Task 6.2 — no dangling links
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_no_link_references_a_missing_node(monkeypatch):
    """Every source/target must appear among the returned node ids.

    The old endpoint read every edge above the weight threshold regardless of
    which nodes it returned, so `links` could cite ids absent from `nodes`. The
    edge query is now scoped to the selected ids, which is what makes the
    invariant hold. Exercised with the node cap dropping most of the pool.
    """
    nodes = [_node_row(i, 1.0 - i / 100) for i in range(1, 11)]
    edges = [(a, b, 0.5, "relates_to")
             for a in range(1, 11) for b in range(1, 11) if a < b]
    resp, _ = _call(monkeypatch, nodes, edges, max_nodes=3)

    ids = {n["id"] for n in resp["data"]["nodes"]}
    assert len(ids) <= 3
    for link in resp["data"]["links"]:
        assert link["source"] in ids, f"dangling source {link['source']}"
        assert link["target"] in ids, f"dangling target {link['target']}"


@pytest.mark.unit
def test_edge_cap_never_leaves_a_dangling_reference(monkeypatch):
    """The edge cap drops edges after the ANY() scoping, so the invariant must
    survive it too."""
    nodes = [_node_row(i, 0.05) for i in range(1, 8)]
    edges = [(a, b, a / 10, "relates_to")
             for a in range(1, 8) for b in range(1, 8) if a < b]
    resp, _ = _call(monkeypatch, nodes, edges, max_edges=2)

    ids = {n["id"] for n in resp["data"]["nodes"]}
    link_ids = {i for l in resp["data"]["links"] for i in (l["source"], l["target"])}
    assert link_ids <= ids, f"links reference ids absent from nodes: {link_ids - ids}"


@pytest.mark.unit
def test_edges_are_fetched_only_between_selected_nodes(monkeypatch):
    """The SQL itself must scope edges to the selected ids — that is what bounds
    the quadratic term, rather than pruning in Python after transferring them."""
    nodes = [_node_row(i, 0.9) for i in (1, 2)]
    _, db = _call(monkeypatch, nodes, [(1, 2, 0.5, "relates_to")])

    edge_sql, edge_params = db.log[1]
    assert "source_story_id = ANY(%s)" in edge_sql
    assert "target_story_id = ANY(%s)" in edge_sql
    assert edge_params[1] == [1, 2]


# ---------------------------------------------------------------------------
# Task 6.3 — bounds
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_node_limit_is_enforced_and_ordered_by_momentum(monkeypatch):
    nodes = [_node_row(i, 1.0 - i / 100) for i in range(1, 21)]
    resp, db = _call(monkeypatch, nodes, [], max_nodes=5)

    node_sql, node_params = db.log[0]
    assert "LIMIT %s" in node_sql
    assert "ORDER BY momentum_score DESC" in node_sql
    assert node_params[1] == 5
    assert len(resp["data"]["nodes"]) <= 5

    scores = [n["momentum_score"] for n in resp["data"]["nodes"]]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.unit
def test_edge_limit_is_enforced(monkeypatch):
    nodes = [_node_row(i, 0.9) for i in range(1, 6)]
    edges = [(a, b, 0.5, "relates_to")
             for a in range(1, 6) for b in range(1, 6) if a < b]
    resp, db = _call(monkeypatch, nodes, edges, max_edges=3)

    edge_sql, edge_params = db.log[1]
    assert "LIMIT %s" in edge_sql
    assert "ORDER BY weight DESC" in edge_sql
    assert edge_params[3] == 3
    assert len(resp["data"]["links"]) <= 3


@pytest.mark.unit
def test_min_momentum_is_applied_in_sql(monkeypatch):
    """Filtering in SQL keeps the LIMIT meaningful — a Python-side filter would
    discard rows already counted against it."""
    _, db = _call(monkeypatch, [_node_row(1, 0.9)], [], min_momentum=0.5)

    node_sql, node_params = db.log[0]
    assert "momentum_score >= %s" in node_sql
    assert node_params[0] == 0.5


@pytest.mark.unit
def test_empty_node_set_skips_the_edge_query(monkeypatch):
    """With no nodes there is nothing to join against — the edge query must be
    skipped rather than issued with an empty ANY() array."""
    resp, db = _call(monkeypatch, [], [])

    assert resp["data"]["nodes"] == []
    assert resp["data"]["links"] == []
    assert len(db.log) == 1, "edge query should not run"


# ---------------------------------------------------------------------------
# Task 6.4 — cache isolation
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_projections_do_not_share_cache_entries(monkeypatch):
    """A slim response served to a full request (or vice versa) would silently
    strip fields from /dashboard."""
    rows = [_node_row(1, 0.9)]

    slim, _ = _call(monkeypatch, rows, [], view=GraphView.slim)
    assert slim["data"]["nodes"][0]["summary"] is None

    full, _ = _call(monkeypatch, rows, [], view=GraphView.full)
    assert full["data"]["nodes"][0]["summary"] == "summary 1"

    assert len(stories_mod._graph_cache) == 2


@pytest.mark.unit
def test_limits_participate_in_the_cache_key(monkeypatch):
    rows = [_node_row(i, 0.9) for i in range(1, 11)]

    _call(monkeypatch, rows, [], max_nodes=3)
    _call(monkeypatch, rows, [], max_nodes=7)

    assert len(stories_mod._graph_cache) == 2


@pytest.mark.unit
def test_identical_params_hit_the_cache(monkeypatch):
    rows = [_node_row(1, 0.9)]
    _call(monkeypatch, rows, [])

    db = _FakeDB(rows, [])
    monkeypatch.setattr(stories_mod, "get_db", lambda: db)
    asyncio.run(get_graph_network(
        min_edge_weight=0.10, min_momentum=0.0, view=GraphView.full,
        max_nodes=MAX_NODES_DEFAULT, max_edges=MAX_EDGES_DEFAULT, api_key="test",
    ))
    assert db.log == [], "second identical call should be served from cache"


# ---------------------------------------------------------------------------
# Task 6.5 — response shape regression
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_default_response_shape_is_unchanged(monkeypatch):
    """Existing consumers must see the same envelope and node keys as before."""
    nodes = [_node_row(1, 0.9), _node_row(2, 0.8)]
    resp, _ = _call(monkeypatch, nodes, [(1, 2, 0.5, "relates_to")])

    assert set(resp) == {"success", "data", "generated_at"}
    assert resp["success"] is True
    assert set(resp["data"]) == {"nodes", "links", "stats"}
    assert set(resp["data"]["stats"]) == {
        "total_nodes", "total_edges", "avg_momentum",
        "communities_count", "avg_edges_per_node",
    }
    assert set(resp["data"]["nodes"][0]) == {
        "id", "title", "summary", "category", "narrative_status",
        "momentum_score", "article_count", "key_entities", "start_date",
        "last_update", "days_active", "community_id", "community_name",
    }
    assert set(resp["data"]["links"][0]) == {
        "source", "target", "weight", "relation_type",
    }


@pytest.mark.unit
def test_stats_reflect_the_bounded_response(monkeypatch):
    """total_edges must count what was actually returned, not the unbounded set —
    otherwise the landing would report a graph it never received."""
    nodes = [_node_row(i, 0.9) for i in range(1, 4)]
    edges = [(1, 2, 0.5, "relates_to"), (2, 3, 0.4, "relates_to")]
    resp, _ = _call(monkeypatch, nodes, edges)

    stats = resp["data"]["stats"]
    assert stats["total_nodes"] == len(resp["data"]["nodes"])
    assert stats["total_edges"] == len(resp["data"]["links"])
