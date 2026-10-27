"""A feed serving only very old entries triggers its fallback scraper (D5)."""
import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.ingestion.feed_parser import FeedParser, _is_stale_feed

pytestmark = pytest.mark.unit

CSIS = 'CSIS - Center for Strategic and International Studies'

STALE_RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>CSIS</title>
<item><title>Event 2016</title><link>https://www.csis.org/events/old</link>
<pubDate>Tue, 12 Apr 2016 14:00:00 GMT</pubDate></item>
</channel></rss>"""

FALLBACK_ITEM = {'title': 'New analysis', 'link': 'https://www.csis.org/analysis/new',
                 'published': datetime.now(), 'scraped': True}


class _Resp:
    def __init__(self, text):
        self._text = text

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def parser(tmp_path):
    cfg = tmp_path / "feeds.yaml"
    cfg.write_text("feeds: []\n")
    return FeedParser(str(cfg))


def test_is_stale_feed_thresholds():
    old = [{'published': datetime.now() - timedelta(days=61)}]
    fresh = old + [{'published': datetime.now() - timedelta(days=59)}]
    assert _is_stale_feed(old, CSIS)
    assert not _is_stale_feed(fresh, CSIS)
    assert not _is_stale_feed([{'published': None}], CSIS)  # undated: cannot judge


def test_stale_feed_replaced_by_fallback_async(parser):
    session = MagicMock()
    session.get.return_value = _Resp(STALE_RSS)
    with patch.object(parser, '_scrape_fallback_async', AsyncMock(return_value=[dict(FALLBACK_ITEM)])) as fb:
        articles = asyncio.run(parser._fetch_and_parse_feed(session, 'https://www.csis.org/rss.xml',
                                                            CSIS, 'intelligence', 'geopolitics'))
    fb.assert_awaited_once()
    assert [a['link'] for a in articles] == ['https://www.csis.org/analysis/new']
    assert articles[0]['category'] == 'intelligence'


def test_stale_feed_keeps_rss_items_when_fallback_empty(parser):
    session = MagicMock()
    session.get.return_value = _Resp(STALE_RSS)
    with patch.object(parser, '_scrape_fallback_async', AsyncMock(return_value=[])):
        articles = asyncio.run(parser._fetch_and_parse_feed(session, 'u', CSIS, 'intelligence', 'geopolitics'))
    assert [a['link'] for a in articles] == ['https://www.csis.org/events/old']


def test_stale_feed_without_fallback_only_warns(parser, caplog):
    session = MagicMock()
    session.get.return_value = _Resp(STALE_RSS)
    with patch.object(parser, '_scrape_fallback_async', AsyncMock()) as fb:
        articles = asyncio.run(parser._fetch_and_parse_feed(session, 'u', 'Some Feed', 'x', 'y'))
    fb.assert_not_awaited()
    assert len(articles) == 1
    assert "Stale feed Some Feed" in caplog.text


def test_stale_feed_sync_parity(parser):
    with patch('src.ingestion.feed_parser.feedparser.parse',
               side_effect=lambda _: __import__('feedparser').parse(STALE_RSS)), \
         patch.object(parser, 'scrape_fallback', return_value=[dict(FALLBACK_ITEM)]) as fb:
        articles = parser.parse_feed('https://www.csis.org/rss.xml', CSIS)
    fb.assert_called_once_with(CSIS)
    assert articles[0]['link'] == 'https://www.csis.org/analysis/new'
