"""Lever L0 in prod (storyline-formation-causal-review tasks.md 8.2, decision-memo.md).

With `formation_levers.link_count_fix` off (default) prod is unchanged: the loader
does not look at `orphan_events` and `article_count` grows by the event size. On,
the loader skips articles waiting in the orphan pool and `article_count` grows by
the links actually inserted (`ON CONFLICT DO NOTHING` rowcount).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Optional

import numpy as np
import pytest

from src.nlp.narrative_processor import NarrativeProcessor
from src.storage.database import DatabaseManager


class _Cursor:
    """INSERTs report rowcount 1 for new (article, storyline) pairs, 0 on conflict;
    the storyline SELECT returns a scripted row."""

    def __init__(self, existing_links: set, storyline_row: tuple):
        self.log: list[tuple[str, Optional[tuple]]] = []
        self.links = set(existing_links)
        self.row = storyline_row
        self.rowcount = 0

    def execute(self, sql: str, params: Optional[tuple] = None) -> None:
        self.log.append((sql, params))
        self.rowcount = 0
        if "INSERT INTO article_storylines" in sql:
            key = (params[0], params[1])
            self.rowcount = int(key not in self.links)
            self.links.add(key)

    def fetchone(self):
        return self.row

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class _DB:
    def __init__(self, cursor: _Cursor):
        self.cur = cursor

    @contextmanager
    def get_connection(self):
        cur = self.cur

        class _Conn:
            def cursor(self):
                return cur

            def commit(self):
                pass

        yield _Conn()


def _processor(fix: bool, existing_links: set) -> tuple[NarrativeProcessor, _Cursor]:
    cur = _Cursor(existing_links, ([0.0] * 384, [], 2, "emerging"))
    proc = NarrativeProcessor(db_manager=_DB(cur), skip_llm=True)
    proc.LINK_COUNT_FIX = fix
    return proc, cur


def _new_count(cur: _Cursor) -> int:
    update = next(p for sql, p in cur.log if "UPDATE storylines SET" in sql)
    return update[2]


@pytest.mark.unit
@pytest.mark.parametrize("fix, expected", [(False, 4), (True, 3)])
def test_article_count_adds_inserted_links_only_with_l0(fix, expected):
    """Storyline 7 holds 2 articles; the event brings article 1 (already linked) and
    article 5. Today the count jumps by 2; with L0 by 1, matching the links."""
    proc, cur = _processor(fix, existing_links={(1, 7)})
    event = {"article_ids": [1, 5], "embedding": np.ones(384) / np.sqrt(384), "entities": []}
    proc._assign_event_to_storyline(event, 7)
    assert _new_count(cur) == expected


@pytest.mark.unit
def test_default_config_keeps_l0_off():
    proc = NarrativeProcessor(db_manager=_DB(_Cursor(set(), None)), skip_llm=True)
    assert proc.LINK_COUNT_FIX is False


@pytest.mark.unit
@pytest.mark.parametrize("flag", [False, True])
def test_loader_excludes_orphan_pool_only_when_asked(flag):
    cur = _Cursor(set(), None)
    db = object.__new__(DatabaseManager)
    db.get_connection = _DB(cur).get_connection
    db.get_all_article_embeddings(days=1, exclude_assigned=True, exclude_orphan_pool=flag)
    sql = cur.log[0][0]
    assert "article_storylines" in sql
    assert ("orphan_events" in sql) is flag
