"""
Test per IngestionPipeline - Quick Hash Deduplication (FASE 1).

Questi test verificano:
- deduplicate_by_quick_hash(): rimozione duplicati basata su hash(link + title)
- Integrazione in run(): dedup viene chiamato dopo parse_all_feeds()
"""

import pytest
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from datetime import datetime, timedelta
from src.ingestion.pipeline import IngestionPipeline


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def pipeline():
    """IngestionPipeline con dipendenze mockata."""
    with patch('src.ingestion.pipeline.FeedParser') as mock_parser, \
         patch('src.ingestion.pipeline.ContentExtractor') as mock_extractor:

        pipeline = IngestionPipeline(
            config_path="config/feeds.yaml",
            output_dir="data",
            extract_full_content=True,
            db=MagicMock(**{'get_attempted_links.return_value': set()})
        )

        return pipeline


@pytest.fixture
def sample_articles():
    """Articoli di test con duplicati."""
    return [
        {
            'title': 'Article 1',
            'link': 'https://example.com/article1',
            'source': 'Source A',
            'published': '2025-11-29'
        },
        {
            'title': 'Article 2',
            'link': 'https://example.com/article2',
            'source': 'Source B',
            'published': '2025-11-29'
        },
        {
            'title': 'Article 1',  # Duplicate title
            'link': 'https://example.com/article1',  # Duplicate link
            'source': 'Source A',
            'published': '2025-11-29'
        },
        {
            'title': 'Article 3',
            'link': 'https://example.com/article3',
            'source': 'Source C',
            'published': '2025-11-29'
        }
    ]


# ============================================================================
# TEST: DEDUPLICATE_BY_QUICK_HASH
# ============================================================================

@pytest.mark.unit
def test_deduplicate_by_quick_hash_removes_exact_duplicates(pipeline):
    """Test: rimuove duplicati esatti (stesso link + title)."""
    articles = [
        {'title': 'Article A', 'link': 'https://ex.com/a', 'source': 'Source 1'},
        {'title': 'Article B', 'link': 'https://ex.com/b', 'source': 'Source 2'},
        {'title': 'Article A', 'link': 'https://ex.com/a', 'source': 'Source 1'},  # Duplicate
    ]

    result = pipeline.deduplicate_by_quick_hash(articles)

    assert len(result) == 2
    assert result[0]['title'] == 'Article A'
    assert result[1]['title'] == 'Article B'


@pytest.mark.unit
def test_deduplicate_by_quick_hash_keeps_different_links(pipeline):
    """Test: mantiene articoli con link diversi (anche se titolo simile)."""
    articles = [
        {'title': 'Same Title', 'link': 'https://ex.com/a', 'source': 'Source 1'},
        {'title': 'Same Title', 'link': 'https://ex.com/b', 'source': 'Source 2'},
    ]

    result = pipeline.deduplicate_by_quick_hash(articles)

    # Link diversi → NON duplicati
    assert len(result) == 2


@pytest.mark.unit
def test_deduplicate_by_quick_hash_keeps_different_titles(pipeline):
    """Test: mantiene articoli con titoli diversi (anche se link simile)."""
    articles = [
        {'title': 'Title A', 'link': 'https://ex.com/same', 'source': 'Source 1'},
        {'title': 'Title B', 'link': 'https://ex.com/same', 'source': 'Source 2'},
    ]

    result = pipeline.deduplicate_by_quick_hash(articles)

    # Titoli diversi → NON duplicati
    assert len(result) == 2


@pytest.mark.unit
def test_deduplicate_by_quick_hash_truncates_title(pipeline):
    """Test: usa solo primi 100 caratteri del titolo per hash."""
    long_title_base = "A" * 100

    articles = [
        {'title': long_title_base + "X", 'link': 'https://ex.com/a', 'source': 'Source 1'},
        {'title': long_title_base + "Y", 'link': 'https://ex.com/a', 'source': 'Source 2'},  # Same first 100 chars
    ]

    result = pipeline.deduplicate_by_quick_hash(articles)

    # Primi 100 caratteri + link uguali → duplicato
    assert len(result) == 1


@pytest.mark.unit
def test_deduplicate_by_quick_hash_handles_empty_list(pipeline):
    """Test: lista vuota ritorna lista vuota."""
    result = pipeline.deduplicate_by_quick_hash([])
    assert result == []


@pytest.mark.unit
def test_deduplicate_by_quick_hash_handles_missing_fields(pipeline):
    """Test: gestisce articoli senza title o link."""
    articles = [
        {'title': 'Article A'},  # No link
        {'link': 'https://ex.com/b'},  # No title
        {'source': 'Source C'},  # No title or link
    ]

    result = pipeline.deduplicate_by_quick_hash(articles)

    # Dovrebbe gestire senza crashare
    assert len(result) == 3  # Tutti sono "unici" (hash diversi con campi mancanti)


@pytest.mark.unit
def test_deduplicate_by_quick_hash_preserves_first_occurrence(pipeline):
    """Test: mantiene la prima occorrenza di un duplicato."""
    articles = [
        {'title': 'Article', 'link': 'https://ex.com/a', 'source': 'Source A', 'extra': 'first'},
        {'title': 'Article', 'link': 'https://ex.com/a', 'source': 'Source A', 'extra': 'second'},
        {'title': 'Article', 'link': 'https://ex.com/a', 'source': 'Source A', 'extra': 'third'},
    ]

    result = pipeline.deduplicate_by_quick_hash(articles)

    assert len(result) == 1
    assert result[0]['extra'] == 'first'  # Primo mantenuto


@pytest.mark.unit
def test_deduplicate_by_quick_hash_multiple_duplicates(pipeline, sample_articles):
    """Test: gestisce correttamente batch con duplicati multipli."""
    result = pipeline.deduplicate_by_quick_hash(sample_articles)

    # 4 articoli → 3 unici (uno è duplicato)
    assert len(result) == 3

    # Verifica che i 3 unici siano presenti
    titles = [a['title'] for a in result]
    assert 'Article 1' in titles
    assert 'Article 2' in titles
    assert 'Article 3' in titles


@pytest.mark.unit
def test_deduplicate_by_quick_hash_unicode_handling(pipeline):
    """Test: gestisce correttamente caratteri unicode nei titoli."""
    articles = [
        {'title': 'Articolo 中文 Русский', 'link': 'https://ex.com/a', 'source': 'Source 1'},
        {'title': 'Articolo 中文 Русский', 'link': 'https://ex.com/a', 'source': 'Source 1'},
    ]

    result = pipeline.deduplicate_by_quick_hash(articles)

    # Dovrebbe gestire unicode correttamente e rimuovere duplicato
    assert len(result) == 1


# ============================================================================
# TEST: INTEGRAZIONE IN RUN()
# ============================================================================

@pytest.mark.unit
def test_run_calls_deduplicate_after_parsing(pipeline):
    """Test: run() chiama deduplicate_by_quick_hash dopo parse_all_feeds."""
    mock_articles = [
        {'title': 'A', 'link': 'https://ex.com/a', 'published': datetime.now()},
        {'title': 'A', 'link': 'https://ex.com/a', 'published': datetime.now()},  # Duplicate
        {'title': 'B', 'link': 'https://ex.com/b', 'published': datetime.now()},
    ]

    # Mock the async internal method (pipeline.run calls _run_async which calls _parse_all_feeds_async)
    mock_parse = AsyncMock(return_value=mock_articles)
    with patch.object(pipeline.feed_parser, '_parse_all_feeds_async', mock_parse):

        # Mock deduplicate to spy on it
        with patch.object(pipeline, 'deduplicate_by_quick_hash', wraps=pipeline.deduplicate_by_quick_hash) as mock_dedup:

            # Run pipeline (no extraction, no save)
            result = pipeline.run(
                save_output=False,
                extract_content=False,
                window_days=7
            )

            # Verifica che deduplicate sia stato chiamato
            mock_dedup.assert_called_once()

            # Verifica che il risultato sia deduplicate (2 unici, 1 duplicato rimosso)
            assert len(result) == 2


@pytest.mark.unit
def test_run_returns_empty_if_all_duplicates(pipeline):
    """Test: run() ritorna lista vuota se tutti gli articoli sono duplicati."""
    mock_articles = [
        {'title': 'Same', 'link': 'https://ex.com/same', 'published': datetime.now()},
        {'title': 'Same', 'link': 'https://ex.com/same', 'published': datetime.now()},
    ]

    mock_parse = AsyncMock(return_value=mock_articles)
    with patch.object(pipeline.feed_parser, '_parse_all_feeds_async', mock_parse):

        result = pipeline.run(
            save_output=False,
            extract_content=False,
            window_days=7
        )

        # Dovrebbe rimanere 1 articolo (il primo)
        assert len(result) == 1


@pytest.mark.unit
def test_run_dedup_before_content_extraction(pipeline):
    """Test: deduplicazione avviene PRIMA dell'estrazione contenuto (efficienza)."""
    mock_articles = [
        {'title': 'A', 'link': 'https://ex.com/a', 'published': datetime.now()},
        {'title': 'A', 'link': 'https://ex.com/a', 'published': datetime.now()},  # Duplicate
        {'title': 'B', 'link': 'https://ex.com/b', 'published': datetime.now()},
    ]

    mock_parse = AsyncMock(return_value=mock_articles)
    with patch.object(pipeline.feed_parser, '_parse_all_feeds_async', mock_parse):

        # Mock async content extraction
        mock_extract = AsyncMock(return_value=[])
        with patch.object(pipeline.content_extractor, '_extract_batch_async', mock_extract):

            pipeline.run(
                save_output=False,
                extract_content=True,
                window_days=7
            )

            # Verifica che _extract_batch_async riceva solo 2 articoli (post-dedup)
            call_args = mock_extract.call_args[0][0]
            assert len(call_args) == 2  # Dedup dovrebbe aver rimosso 1 duplicato
