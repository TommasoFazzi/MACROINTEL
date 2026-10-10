"""DatabaseManager.save_report() persists report_generation_context (migration 048)."""
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import psycopg2
import pytest

from src.storage.database import DatabaseManager

pytestmark = pytest.mark.unit

_CTX = {
    "writer_path": "v2", "writer_model": "gemini-3.1-pro", "system_prompt": "sys",
    "user_prompt": "user", "articles_in_prompt": [{"n": 1, "excerpt": "e"}],
    "storylines_in_prompt": [], "macro_context_text": "BRENT 81", "macro_snapshot": {"rows": []},
}


@pytest.fixture
def db_and_cursor():
    with patch("src.storage.database.SimpleConnectionPool"):
        db = DatabaseManager(connection_url="postgresql://test:test@localhost/test")
    cur = MagicMock()
    cur.__enter__.return_value = cur
    cur.fetchone.return_value = (42,)
    conn = MagicMock()
    conn.cursor.return_value = cur

    @contextmanager
    def fake_conn():
        yield conn
    db.get_connection = fake_conn
    return db, cur


def _sql(cur):
    return [c.args[0].strip() for c in cur.execute.call_args_list]


def test_context_row_written_with_report(db_and_cursor):
    db, cur = db_and_cursor
    assert db.save_report({"report_text": "r", "metadata": {}, "generation_context": _CTX}) == 42

    sql = _sql(cur)
    assert sql[0].startswith("INSERT INTO reports")
    assert sql[1] == "SAVEPOINT generation_context"
    assert sql[2].startswith("INSERT INTO report_generation_context")
    assert sql[3] == "RELEASE SAVEPOINT generation_context"
    params = cur.execute.call_args_list[2].args[1]
    assert params[0] == 42 and params[1] == "v2" and params[4] == "user"


def test_missing_table_still_saves_report(db_and_cursor, caplog):
    db, cur = db_and_cursor

    def execute(sql, params=None):
        if "report_generation_context" in sql:
            raise psycopg2.errors.UndefinedTable('relation "report_generation_context" does not exist')
    cur.execute.side_effect = execute

    assert db.save_report({"report_text": "r", "metadata": {}, "generation_context": _CTX}) == 42
    assert "ROLLBACK TO SAVEPOINT generation_context" in _sql(cur)
    assert "migration 048" in caplog.text


def test_report_without_context_unchanged(db_and_cursor):
    db, cur = db_and_cursor
    assert db.save_report({"report_text": "r", "metadata": {}, "report_type": "weekly"}) == 42
    assert len(_sql(cur)) == 1
