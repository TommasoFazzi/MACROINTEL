"""DatabaseManager methods for ingestion_seen_links (migration 049)."""
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import psycopg2
import pytest

from src.storage.database import DatabaseManager

pytestmark = pytest.mark.unit


@pytest.fixture
def db_and_cursor():
    with patch("src.storage.database.SimpleConnectionPool"):
        db = DatabaseManager(connection_url="postgresql://test:test@localhost/test")
    cur = MagicMock()
    cur.__enter__.return_value = cur
    conn = MagicMock()
    conn.cursor.return_value = cur

    @contextmanager
    def fake_conn():
        yield conn
    db.get_connection = fake_conn
    return db, cur


def test_attempted_links_unions_both_tables(db_and_cursor):
    db, cur = db_and_cursor
    cur.fetchall.return_value = [("https://a",), ("https://b",)]

    assert db.get_attempted_links(["https://a", "https://b", "https://c"]) == {"https://a", "https://b"}

    sql, params = cur.execute.call_args.args
    assert "FROM articles WHERE link = ANY(%s)" in sql
    assert "UNION" in sql
    assert "FROM ingestion_seen_links WHERE link = ANY(%s)" in sql
    assert params == (["https://a", "https://b", "https://c"],) * 2


def test_empty_inputs_make_no_query(db_and_cursor):
    db, cur = db_and_cursor
    with patch("src.storage.database.execute_values") as ev:
        assert db.get_attempted_links([]) == set()
        assert db.record_seen_links([{"title": "no link"}]) == 0
        ev.assert_not_called()
    cur.execute.assert_not_called()


def test_record_inserts_link_and_source_on_conflict_do_nothing(db_and_cursor):
    db, cur = db_and_cursor
    with patch("src.storage.database.execute_values", return_value=[(1,), (1,)]) as ev:
        assert db.record_seen_links([{"link": "https://a", "source": "RAND"}, {"link": "https://b"}]) == 2

    _, sql, rows = ev.call_args.args
    assert sql.startswith("INSERT INTO ingestion_seen_links (link, source) VALUES %s")
    assert "ON CONFLICT (link) DO NOTHING RETURNING 1" in sql
    assert rows == [("https://a", "RAND"), ("https://b", None)]
    assert ev.call_args.kwargs == {"fetch": True}


def test_record_counts_rows_across_pages(db_and_cursor):
    # 2026-10-29 first run logged "Recorded 91" for 391 links: rowcount of the last page only.
    db, cur = db_and_cursor
    cur.rowcount = 91
    items = [{"link": f"https://x/{i}"} for i in range(391)]
    with patch("src.storage.database.execute_values", return_value=[(1,)] * 391):
        assert db.record_seen_links(items) == 391


def test_prune_and_clear(db_and_cursor):
    db, cur = db_and_cursor
    db.prune_seen_links(days=30)
    sql, params = cur.execute.call_args.args
    assert sql.startswith("DELETE FROM ingestion_seen_links") and "first_seen_at <" in sql
    assert params == (30,)

    since = datetime(2026, 10, 26, tzinfo=timezone.utc)
    db.clear_seen_links_since(since)
    sql, params = cur.execute.call_args.args
    assert "first_seen_at >= %s" in sql and params == (since,)


def test_errors_propagate(db_and_cursor):
    db, cur = db_and_cursor
    cur.execute.side_effect = psycopg2.OperationalError("connection lost")
    with pytest.raises(psycopg2.OperationalError):
        db.get_attempted_links(["https://a"])
    with pytest.raises(psycopg2.OperationalError):
        db.prune_seen_links()
