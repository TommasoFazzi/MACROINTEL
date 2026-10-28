"""scripts/check_source_freshness.py: stale / never-ingested / unsupported feeds."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from scripts import check_source_freshness as csf
from src.ingestion.feed_parser import FeedParser

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc)

FEEDS = [
    {'name': 'CSIS'},
    {'name': 'RUSI'},
    {'name': 'Fresh'},
    {'name': 'Weekly', 'max_stale_days': 45},
    {'name': 'Chatham House', 'status': 'unsupported'},
    {'name': 'Business | Euronews RSS'},
]
LAST = {
    'CSIS': NOW - timedelta(days=30),
    'Fresh': NOW - timedelta(days=1),
    'Weekly': NOW - timedelta(days=30),
    'Business | Euronews RSS': NOW - timedelta(days=20),
}


def test_rows_classify_each_case():
    rows = {r['feed']: r for r in csf.build_rows(FEEDS, LAST, NOW)}
    assert rows['CSIS']['status'] == 'stale' and rows['CSIS']['age'] == 30
    assert rows['RUSI']['status'] == 'never ingested'
    assert rows['Chatham House']['status'] == 'unsupported'
    assert 'Fresh' not in rows
    assert 'Weekly' not in rows  # per-feed override 45 > 30


def test_markdown_lists_date_age_and_escapes_pipes():
    md = csf.render_markdown(csf.build_rows(FEEDS, LAST, NOW), len(FEEDS))
    assert '| CSIS | stale | 2026-09-26 | 30 |' in md
    assert '| RUSI | never ingested | — | — |' in md
    assert md.count('Chatham House') == 1
    assert 'Business \\| Euronews RSS' in md


def test_all_fresh_message():
    md = csf.render_markdown([], 3)
    assert 'All 3 feeds' in md


def _write_config(tmp_path):
    cfg = tmp_path / 'feeds.yaml'
    cfg.write_text(
        "feeds:\n"
        "  - name: CSIS\n    url: https://x\n"
        "  - name: Chatham House\n    url: https://y\n    status: unsupported\n"
    )
    return cfg


def test_main_exits_zero_with_stale_feeds_and_writes_summary(tmp_path, monkeypatch):
    summary = tmp_path / 'summary.md'
    monkeypatch.setenv('GITHUB_STEP_SUMMARY', str(summary))
    cfg = _write_config(tmp_path)
    with patch.object(csf, 'query_last_created', return_value={'CSIS': datetime.now(timezone.utc) - timedelta(days=109)}) as q:
        assert csf.main(['--config', str(cfg)]) == 0
    q.assert_called_once_with(['CSIS'])  # unsupported feeds are not queried
    text = summary.read_text()
    assert '### Source freshness' in text and '| CSIS | stale |' in text and 'unsupported' in text


def test_main_nonzero_on_db_failure(tmp_path, monkeypatch):
    monkeypatch.delenv('GITHUB_STEP_SUMMARY', raising=False)
    with patch.object(csf, 'query_last_created', side_effect=RuntimeError('tunnel down')):
        assert csf.main(['--config', str(_write_config(tmp_path))]) != 0


def test_feed_parser_skips_unsupported(tmp_path):
    parser = FeedParser(str(_write_config(tmp_path)))
    assert [f['name'] for f in parser.feeds_config] == ['CSIS']
