"""Feed item selection by link novelty (change ingestion-freshness-fix)."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import psycopg2
import pytest

from src.ingestion.pipeline import (
    IngestionPipeline,
    MAX_SELECTED_PER_RUN,
    select_new_items,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 24, 7, 0, tzinfo=timezone.utc)


def _item(link, published, **extra):
    return {'title': link, 'link': link, 'source': 'RAND', 'published': published,
            'fetched_at': datetime(2026, 10, 24, 7, 0), **extra}


# ── select_new_items (pure) ──────────────────────────────────────────────────

def test_late_rand_item_is_selected():
    # Stamped 04:00Z two days earlier, first seen in the feed today: lost by the old 24h filter
    item = _item('https://rand.org/RRA3964-1', datetime(2026, 10, 22, 4, 0))
    assert select_new_items([item], set(), NOW) == [item]


def test_attempted_link_is_skipped_whatever_its_date():
    item = _item('https://rand.org/a', NOW - timedelta(hours=1))
    assert select_new_items([item], {'https://rand.org/a'}, NOW) == []


def test_item_older_than_window_is_skipped():
    item = _item('https://rand.org/a', NOW - timedelta(days=8))
    assert select_new_items([item], set(), NOW) == []


def test_undated_item_is_selected_with_fetched_at_and_input_untouched():
    item = _item('https://crs/a', None)
    [selected] = select_new_items([item], set(), NOW)
    assert selected['published'] == item['fetched_at']
    assert item['published'] is None


def test_cap_keeps_newest_dated_first_then_undated():
    items = [_item(f'https://x/{i}', NOW - timedelta(minutes=i)) for i in range(5)]
    undated = _item('https://x/undated', None)
    result = select_new_items([undated] + items[::-1], set(), NOW, max_items=3)
    assert [a['link'] for a in result] == ['https://x/0', 'https://x/1', 'https://x/2']

    result = select_new_items([undated] + items, set(), NOW, max_items=10)
    assert result[-1]['link'] == 'https://x/undated'


def test_naive_and_aware_datetimes_compare_as_utc():
    naive = _item('https://x/naive', datetime(2026, 10, 17, 8, 0))          # 6d23h before NOW
    aware = _item('https://x/aware', datetime(2026, 10, 17, 9, 0, tzinfo=timezone(timedelta(hours=2))))  # 07:00Z, exactly 7d
    old = _item('https://x/old', datetime(2026, 10, 17, 6, 59))
    assert [a['link'] for a in select_new_items([naive, aware, old], set(), NOW)] == [
        'https://x/naive', 'https://x/aware']


# ── pipeline integration ─────────────────────────────────────────────────────

@pytest.fixture
def make_pipeline():
    def make(db):
        with patch('src.ingestion.pipeline.FeedParser'), patch('src.ingestion.pipeline.ContentExtractor'):
            p = IngestionPipeline(extract_full_content=True, db=db)
        return p
    return make


def _db(attempted=frozenset()):
    db = MagicMock()
    db.get_attempted_links.return_value = set(attempted)
    db.record_seen_links.return_value = 0
    return db


def _run(p, items, extracted=None, **kw):
    with patch.object(p.feed_parser, '_parse_all_feeds_async', AsyncMock(return_value=items)), \
         patch.object(p.content_extractor, '_extract_batch_async',
                      AsyncMock(side_effect=lambda arts: extracted if extracted is not None else arts)):
        return p.run(save_output=False, **kw)


def test_run_selects_new_and_records_after_extraction(make_pipeline):
    db = _db(attempted={'https://x/old-link'})
    p = make_pipeline(db)
    fresh = _item('https://x/new', datetime.now() - timedelta(days=2))
    seen = _item('https://x/old-link', datetime.now())

    result = _run(p, [fresh, seen])

    assert [a['link'] for a in result] == ['https://x/new']
    db.prune_seen_links.assert_called_once_with(30)
    db.record_seen_links.assert_called_once()
    assert [a['link'] for a in db.record_seen_links.call_args.args[0]] == ['https://x/new']


def test_lookup_failure_falls_back_to_24h_and_warns(make_pipeline):
    db = _db()
    db.get_attempted_links.side_effect = psycopg2.OperationalError("down")
    p = make_pipeline(db)
    recent = _item('https://x/recent', datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2))
    late = _item('https://x/late', datetime.now() - timedelta(days=2))
    undated = _item('https://x/undated', None)

    with patch('src.ingestion.pipeline.report_run_warning') as warn:
        result = _run(p, [recent, late, undated])

    assert [a['link'] for a in result] == ['https://x/recent']
    warn.assert_called_once()
    assert '24h' in warn.call_args.args[1]
    db.record_seen_links.assert_not_called()


def test_db_construction_failure_falls_back(make_pipeline):
    p = make_pipeline(None)
    recent = _item('https://x/recent', datetime.now(timezone.utc).replace(tzinfo=None))
    with patch('src.storage.database.DatabaseManager', side_effect=psycopg2.OperationalError("no db")), \
         patch('src.ingestion.pipeline.report_run_warning') as warn:
        result = _run(p, [recent])
    assert len(result) == 1 and warn.called


def test_record_failure_keeps_items_and_warns(make_pipeline):
    db = _db()
    db.record_seen_links.side_effect = psycopg2.OperationalError("insert failed")
    p = make_pipeline(db)
    item = _item('https://x/a', datetime.now())

    with patch('src.ingestion.pipeline.report_run_warning') as warn:
        result = _run(p, [item])

    assert [a['link'] for a in result] == ['https://x/a']
    warn.assert_called_once()


def test_crash_during_extraction_records_nothing(make_pipeline):
    db = _db()
    p = make_pipeline(db)
    items = [_item(f'https://x/{i}', datetime.now()) for i in range(3)]
    with patch.object(p.feed_parser, '_parse_all_feeds_async', AsyncMock(return_value=items)), \
         patch.object(p.content_extractor, '_extract_batch_async',
                      AsyncMock(side_effect=RuntimeError("libxml2 abort stand-in"))):
        with pytest.raises(RuntimeError):
            p.run(save_output=False)
    db.record_seen_links.assert_not_called()


def test_no_extraction_records_nothing(make_pipeline):
    db = _db()
    p = make_pipeline(db)
    with patch.object(p.feed_parser, '_parse_all_feeds_async',
                      AsyncMock(return_value=[_item('https://x/a', datetime.now())])):
        p.run(save_output=False, extract_content=False)
    db.record_seen_links.assert_not_called()


def test_retry_run_clears_today_utc(make_pipeline, monkeypatch):
    monkeypatch.setenv("INGESTION_RETRY_RUN", "true")
    db = _db()
    p = make_pipeline(db)
    _run(p, [_item('https://x/a', datetime.now())])

    since = db.clear_seen_links_since.call_args.args[0]
    assert since.tzinfo == timezone.utc
    assert (since.hour, since.minute, since.second) == (0, 0, 0)
    assert since.date() == datetime.now(timezone.utc).date()


def test_retry_unset_does_not_clear(make_pipeline, monkeypatch):
    monkeypatch.delenv("INGESTION_RETRY_RUN", raising=False)
    db = _db()
    _run(make_pipeline(db), [_item('https://x/a', datetime.now())])
    db.clear_seen_links_since.assert_not_called()


def test_cap_applies_in_pipeline(make_pipeline):
    db = _db()
    p = make_pipeline(db)
    now = datetime.now()
    items = [_item(f'https://x/{i}', now - timedelta(seconds=i)) for i in range(MAX_SELECTED_PER_RUN + 5)]
    result = _run(p, items)
    assert len(result) == MAX_SELECTED_PER_RUN
    assert len(db.record_seen_links.call_args.args[0]) == MAX_SELECTED_PER_RUN
    assert 'https://x/0' in {a['link'] for a in result}


# ── scraped pages (D4) ───────────────────────────────────────────────────────

def _scraped(link, page_date):
    return {**_item(link, datetime.now()), 'scraped': True,
            'extraction_success': True,
            'full_content': {'date': page_date, 'extraction_method': 'trafilatura'}}


def test_old_scraped_page_dropped_but_recorded(make_pipeline):
    db = _db()
    p = make_pipeline(db)
    old = _scraped('https://ecfr.eu/old', (datetime.now() - timedelta(days=40)).date().isoformat())
    result = _run(p, [old])
    assert result == []
    assert [a['link'] for a in db.record_seen_links.call_args.args[0]] == ['https://ecfr.eu/old']


def test_recent_scraped_page_keeps_fetch_time(make_pipeline):
    p = make_pipeline(_db())
    fetched = datetime.now()
    isw = {**_scraped('https://isw/x', (fetched - timedelta(days=1)).date().isoformat()), 'published': fetched}
    [kept] = _run(p, [isw])
    assert kept['published'] == fetched


def test_undated_scraped_page_kept(make_pipeline):
    p = make_pipeline(_db())
    assert len(_run(p, [_scraped('https://x/nodate', None)])) == 1


def test_unparseable_page_date_kept(make_pipeline):
    p = make_pipeline(_db())
    assert len(_run(p, [_scraped('https://x/bad', 'last Tuesday')])) == 1


def test_old_rss_page_date_does_not_drop_unscraped_item(make_pipeline):
    p = make_pipeline(_db())
    rss = {**_scraped('https://x/rss', '2016-01-01'), 'scraped': False}
    assert len(_run(p, [rss])) == 1
