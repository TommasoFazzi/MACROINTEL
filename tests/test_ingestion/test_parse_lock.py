"""content_extractor: every libxml2 parse runs under LXML_PARSE_LOCK (no concurrent parsing)."""
import threading
from unittest.mock import MagicMock

import pytest

import src.ingestion.content_extractor as ce

pytestmark = pytest.mark.unit


def _extractor():
    return object.__new__(ce.ContentExtractor)


def test_trafilatura_extract_runs_under_lock(monkeypatch):
    seen = []
    monkeypatch.setattr(ce.trafilatura, "extract",
                        lambda *a, **k: seen.append(ce.LXML_PARSE_LOCK.locked()) or '{"text": "t"}')
    out = _extractor().extract_with_trafilatura("https://x", html="<html></html>")
    assert out["text"] == "t" and seen == [True]
    assert not ce.LXML_PARSE_LOCK.locked()


def test_newspaper_parse_runs_under_lock(monkeypatch):
    seen = []
    art = MagicMock(text="body", authors=[], publish_date=None)
    art.parse.side_effect = lambda: seen.append(ce.LXML_PARSE_LOCK.locked())
    monkeypatch.setattr(ce, "NewspaperArticle", lambda url, request_timeout=None: art)
    ex = _extractor()
    ex.timeout = 5
    assert ex.extract_with_newspaper("https://x")["text"] == "body"
    assert seen == [True] and not ce.LXML_PARSE_LOCK.locked()


def test_lock_released_when_parse_raises(monkeypatch):
    def boom(*a, **k):
        raise ValueError("bad html")
    monkeypatch.setattr(ce.trafilatura, "extract", boom)
    assert _extractor().extract_with_trafilatura("https://x", html="<html></html>") is None
    assert not ce.LXML_PARSE_LOCK.locked()


def test_parses_never_overlap(monkeypatch):
    """16 threads: at most one parse in flight at any time."""
    active, peak, guard = [0], [0], threading.Lock()

    def slow_extract(*a, **k):
        with guard:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        threading.Event().wait(0.01)
        with guard:
            active[0] -= 1
        return '{"text": "t"}'

    monkeypatch.setattr(ce.trafilatura, "extract", slow_extract)
    ex = _extractor()
    threads = [threading.Thread(target=ex.extract_with_trafilatura, args=("https://x", "<html/>"))
               for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] == 1
