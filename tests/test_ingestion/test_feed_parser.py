"""
Test per FeedParser - RSS/Atom feed parsing.

Questi test verificano:
- Parsing di feed RSS validi
- Estrazione metadata (title, link, date, etc.)
- Gestione di feed non validi
- Filtro per categoria
"""

import pytest
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from src.ingestion.feed_parser import FeedParser


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def mock_feed_config(tmp_path):
    """
    Crea un file di configurazione YAML temporaneo per i test.

    Returns:
        Path: Path al file YAML temporaneo
    """
    config_content = """
feeds:
  - name: Test Feed 1
    url: https://example.com/rss1
    category: intelligence
    subcategory: cybersecurity

  - name: Test Feed 2
    url: https://example.com/rss2
    category: tech_economy
    subcategory: technology
"""
    config_file = tmp_path / "test_feeds.yaml"
    config_file.write_text(config_content)
    return str(config_file)


@pytest.fixture
def feed_parser(mock_feed_config):
    """
    Fixture che crea un FeedParser con configurazione di test.
    """
    return FeedParser(config_path=mock_feed_config)


@pytest.fixture
def mock_rss_entry():
    """
    Mock di un entry RSS da feedparser.

    Simula la struttura che feedparser.parse() ritorna.
    """
    entry = MagicMock()
    entry.title = "Test Article Title"
    entry.link = "https://example.com/article-1"
    entry.summary = "This is a test article summary."
    entry.published_parsed = (2025, 11, 28, 10, 30, 0, 0, 0, 0)
    entry.author = "Test Author"
    entry.tags = [{'term': 'technology'}, {'term': 'ai'}]

    # Rimuovi l'attributo content (il codice usa summary come fallback)
    del entry.content

    # Mock del metodo get
    def get_value(key, default=None):
        values = {
            'title': 'Test Article Title',
            'link': 'https://example.com/article-1'
        }
        return values.get(key, default)

    entry.get = get_value

    return entry


@pytest.fixture
def mock_feed_response():
    """
    Mock di una risposta completa da feedparser.parse().
    """
    feed = Mock()
    feed.bozo = False  # No parsing errors

    def create_entry(num):
        """Helper per creare entry mock."""
        entry = MagicMock()
        entry.title = f"Article {num}"
        entry.link = f"https://example.com/article-{num}"
        entry.summary = f"Summary {num}"
        entry.published_parsed = (2025, 11, 28, 10 + num, 0, 0, 0, 0, 0)

        # Configura get() per ritornare i valori corretti
        def get_value(key, default=None):
            values = {
                'title': f'Article {num}',
                'link': f'https://example.com/article-{num}'
            }
            return values.get(key, default)

        entry.get = get_value
        # Nessun attributo authors o tags per semplicità
        return entry

    feed.entries = [create_entry(1), create_entry(2), create_entry(3)]
    return feed


# ============================================================================
# TEST: INIZIALIZZAZIONE E CONFIGURAZIONE
# ============================================================================

@pytest.mark.unit
def test_feed_parser_initialization(feed_parser):
    """Test: FeedParser si inizializza correttamente."""
    assert feed_parser is not None
    assert feed_parser.feeds_config is not None
    assert len(feed_parser.feeds_config) == 2


@pytest.mark.unit
def test_feed_parser_loads_config(feed_parser):
    """Test: configurazione YAML viene caricata correttamente."""
    feeds = feed_parser.feeds_config

    assert len(feeds) == 2
    assert feeds[0]['name'] == 'Test Feed 1'
    assert feeds[0]['category'] == 'intelligence'
    assert feeds[1]['name'] == 'Test Feed 2'
    assert feeds[1]['category'] == 'tech_economy'


@pytest.mark.unit
def test_feed_parser_handles_missing_config():
    """Test: gestisce file config mancante senza crashare."""
    parser = FeedParser(config_path="non_existent_file.yaml")

    # Non deve crashare, solo ritornare lista vuota
    assert parser.feeds_config == []


# ============================================================================
# TEST: PARSING RSS FEED
# ============================================================================

@pytest.mark.unit
@patch('feedparser.parse')
def test_parse_feed_success(mock_parse, feed_parser, mock_feed_response):
    """Test: parsing di feed RSS valido ritorna articoli."""
    mock_parse.return_value = mock_feed_response

    articles = feed_parser.parse_feed("https://example.com/rss", "Test Feed")

    assert len(articles) == 3
    assert articles[0]['title'] == 'Article 1'
    assert articles[0]['link'] == 'https://example.com/article-1'
    assert articles[0]['source'] == 'Test Feed'


@pytest.mark.unit
@patch('feedparser.parse')
def test_parse_feed_extracts_metadata(mock_parse, feed_parser, mock_feed_response):
    """Test: tutti i metadati vengono estratti correttamente."""
    mock_parse.return_value = mock_feed_response

    articles = feed_parser.parse_feed("https://example.com/rss", "Test Feed")
    article = articles[0]

    # Verifica tutti i campi richiesti
    required_fields = ['title', 'link', 'published', 'summary', 'source', 'fetched_at']
    for field in required_fields:
        assert field in article, f"Campo {field} mancante"
        assert article[field] is not None


@pytest.mark.unit
@patch('feedparser.parse')
def test_parse_feed_handles_published_date(mock_parse, feed_parser, mock_feed_response):
    """Test: data di pubblicazione viene parsata correttamente."""
    mock_parse.return_value = mock_feed_response

    articles = feed_parser.parse_feed("https://example.com/rss", "Test Feed")

    assert isinstance(articles[0]['published'], datetime)
    assert articles[0]['published'].year == 2025
    assert articles[0]['published'].month == 11
    assert articles[0]['published'].day == 28


@pytest.mark.unit
@patch('feedparser.parse')
def test_parse_feed_handles_invalid_feed(mock_parse, feed_parser):
    """Test: feed non valido ritorna lista vuota (non crash)."""
    # Simula errore di parsing
    mock_parse.side_effect = Exception("Network error")

    articles = feed_parser.parse_feed("https://invalid-url.com/rss", "Invalid Feed")

    assert articles == []  # Non deve crashare


@pytest.mark.unit
@patch('feedparser.parse')
def test_parse_feed_handles_bozo_feed(mock_parse, feed_parser):
    """Test: feed con warning (bozo=True) viene processato comunque."""
    bozo_feed = Mock()
    bozo_feed.bozo = True
    bozo_feed.bozo_exception = "Malformed XML"
    bozo_feed.entries = []

    mock_parse.return_value = bozo_feed

    # Non deve crashare, solo loggare warning
    articles = feed_parser.parse_feed("https://example.com/rss", "Bozo Feed")

    assert articles == []


# ============================================================================
# TEST: EXTRACT ARTICLE DATA
# ============================================================================

@pytest.mark.unit
def test_extract_article_data(feed_parser, mock_rss_entry):
    """Test: estrazione dati da entry RSS."""
    article = feed_parser._extract_article_data(mock_rss_entry, "Test Source")

    assert article is not None
    assert article['title'] == "Test Article Title"
    assert article['link'] == "https://example.com/article-1"
    assert article['source'] == "Test Source"
    assert article['summary'] == "This is a test article summary."


@pytest.mark.unit
def test_extract_authors(feed_parser, mock_rss_entry):
    """Test: estrazione autori da entry."""
    authors = feed_parser._extract_authors(mock_rss_entry)

    assert len(authors) == 1
    assert "Test Author" in authors


@pytest.mark.unit
def test_extract_tags(feed_parser, mock_rss_entry):
    """Test: estrazione tags da entry."""
    tags = feed_parser._extract_tags(mock_rss_entry)

    assert len(tags) == 2
    assert 'technology' in tags
    assert 'ai' in tags


@pytest.mark.unit
def test_extract_article_handles_missing_date(feed_parser):
    """Test: gestisce entry senza data pubblicazione."""
    entry = MagicMock()
    entry.title = "No Date Article"
    entry.link = "https://example.com/no-date"
    entry.summary = "Summary"

    def get_value(key, default=None):
        values = {
            'title': 'No Date Article',
            'link': 'https://example.com/no-date'
        }
        return values.get(key, default)

    entry.get = get_value

    # NO published_parsed attribute - simula con hasattr che ritorna False
    del entry.published_parsed
    del entry.updated_parsed

    article = feed_parser._extract_article_data(entry, "Test")

    assert article is not None
    assert article['published'] is None  # Data può essere None


# ============================================================================
# TEST: PARSE ALL FEEDS
# ============================================================================

@pytest.mark.unit
def test_parse_all_feeds(feed_parser):
    """Test: parsing di tutti i feed configurati."""
    # Mock the async internal method — returns articles with category already set
    async def fake_fetch(session, url, name, category, subcategory):
        return [
            {'title': f'Article {i}', 'link': f'https://example.com/{name}/{i}',
             'source': name, 'published': datetime(2025, 11, 28, 10 + i),
             'summary': f'Summary {i}', 'authors': [], 'tags': [],
             'fetched_at': datetime.now(), 'category': category,
             'subcategory': subcategory}
            for i in range(1, 4)
        ]

    with patch.object(FeedParser, '_fetch_and_parse_feed', new_callable=AsyncMock, side_effect=fake_fetch):
        all_articles = feed_parser.parse_all_feeds()

    # 2 feed * 3 articoli ciascuno = 6 articoli totali
    assert len(all_articles) == 6


@pytest.mark.unit
def test_parse_all_feeds_adds_category(feed_parser):
    """Test: categoria e subcategoria vengono aggiunte agli articoli."""
    async def fake_fetch(session, url, name, category, subcategory):
        return [
            {'title': f'Article {i}', 'link': f'https://example.com/{name}/{i}',
             'source': name, 'published': datetime(2025, 11, 28, 10 + i),
             'summary': f'Summary {i}', 'authors': [], 'tags': [],
             'fetched_at': datetime.now(), 'category': category,
             'subcategory': subcategory}
            for i in range(1, 4)
        ]

    with patch.object(FeedParser, '_fetch_and_parse_feed', new_callable=AsyncMock, side_effect=fake_fetch):
        all_articles = feed_parser.parse_all_feeds()

    # Primo feed è 'intelligence'
    assert all_articles[0]['category'] == 'intelligence'
    assert all_articles[0]['subcategory'] == 'cybersecurity'


@pytest.mark.unit
def test_parse_feeds_by_category(feed_parser):
    """Test: filtro per categoria funziona."""
    async def fake_fetch(session, url, name, category, subcategory):
        return [
            {'title': f'Article {i}', 'link': f'https://example.com/{name}/{i}',
             'source': name, 'published': datetime(2025, 11, 28, 10 + i),
             'summary': f'Summary {i}', 'authors': [], 'tags': [],
             'fetched_at': datetime.now(), 'category': category,
             'subcategory': subcategory}
            for i in range(1, 4)
        ]

    with patch.object(FeedParser, '_fetch_and_parse_feed', new_callable=AsyncMock, side_effect=fake_fetch):
        # Solo feed 'intelligence'
        articles = feed_parser.parse_all_feeds(category='intelligence')

    # 1 feed * 3 articoli = 3 articoli
    assert len(articles) == 3
    assert all(a['category'] == 'intelligence' for a in articles)


# ============================================================================
# TEST: GET FEEDS BY CATEGORY
# ============================================================================

@pytest.mark.unit
def test_get_feeds_by_category(feed_parser):
    """Test: organizzazione feed per categoria."""
    categorized = feed_parser.get_feeds_by_category()

    assert 'intelligence' in categorized
    assert 'tech_economy' in categorized
    assert len(categorized['intelligence']) == 1
    assert len(categorized['tech_economy']) == 1


# ============================================================================
# TEST: EDGE CASES
# ============================================================================

@pytest.mark.unit
@patch('feedparser.parse')
def test_parse_feed_empty_entries(mock_parse, feed_parser):
    """Test: feed senza entry ritorna lista vuota."""
    empty_feed = Mock()
    empty_feed.bozo = False
    empty_feed.entries = []

    mock_parse.return_value = empty_feed

    articles = feed_parser.parse_feed("https://example.com/empty", "Empty Feed")

    assert articles == []


@pytest.mark.unit
def test_extract_article_handles_exception(feed_parser):
    """Test: exception durante estrazione non causa crash."""
    bad_entry = Mock()
    bad_entry.get = Mock(side_effect=Exception("Bad entry"))

    article = feed_parser._extract_article_data(bad_entry, "Test")

    assert article is None  # Ritorna None invece di crashare


@pytest.mark.unit
def test_listing_link_filter_applies_before_any_cap():
    """CSIS: article links sit after ~1150 other links; an early DOM cap used to drop them all."""
    from bs4 import BeautifulSoup
    noise = ''.join(f'<a href="/about/{i}">About {i}</a>' for i in range(1200))
    arts = ''.join(f'<a href="/analysis/item-{i}"></a><a href="/analysis/item-{i}">Item {i}</a>' for i in range(25))
    soup = BeautifulSoup(f'<html><body>{noise}{arts}</body></html>', 'html.parser')
    config = {'url': 'https://www.csis.org/analysis', 'selector': 'a[href]', 'link_based': True,
              'link_filter': r'^/analysis/[a-z0-9-]+$', 'category': 'intelligence', 'subcategory': 'think_tank'}

    items = FeedParser._articles_from_listing(soup, config, 'CSIS')

    assert len(items) == 20  # output cap
    assert items[0]['link'] == 'https://www.csis.org/analysis/item-0'
    assert items[0]['title'] == 'Item 0'  # empty image link skipped, titled link kept
    assert all(a['scraped'] and a['extraction_method'] == 'fallback_scraper' for a in items)
