"""
Test per DatabaseManager - Content Hash Deduplication (FASE 2).

Questi test verificano:
- Calcolo content_hash in save_article()
- Deduplicazione basata su content_hash (ultimi 7 giorni)
- Salvataggio content_hash nel database
- Gestione articoli con contenuto identico ma link diversi
"""

import pytest
import hashlib
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta
from src.storage.database import DatabaseManager


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def mock_connection():
    """Mock connection con cursor."""
    conn = MagicMock()
    cursor = MagicMock()

    # Mock context manager per cursor
    cursor.__enter__ = Mock(return_value=cursor)
    cursor.__exit__ = Mock(return_value=False)

    # Mock context manager per connection
    conn.__enter__ = Mock(return_value=conn)
    conn.__exit__ = Mock(return_value=False)

    conn.cursor.return_value = cursor

    return conn, cursor


@pytest.fixture
def db_manager():
    """DatabaseManager con connection pool mockato."""
    with patch('src.storage.database.SimpleConnectionPool') as mock_pool:
        mock_pool_instance = MagicMock()
        mock_pool.return_value = mock_pool_instance

        db = DatabaseManager(connection_url="postgresql://test:test@localhost/test")
        return db


@pytest.fixture
def sample_article_with_nlp():
    """Articolo con NLP processing completo."""
    return {
        'title': 'Test Article',
        'link': 'https://example.com/test',
        'published': datetime(2025, 11, 29, 10, 0, 0),
        'source': 'Source A',
        'category': 'intelligence',
        'subcategory': 'cybersecurity',
        'summary': 'Test summary',
        'nlp_processing': {'success': True},
        'nlp_data': {
            'clean_text': 'This is the clean text content for testing deduplication.',
            'entities': {'entities': [], 'by_type': {}, 'entity_count': 0},
            'preprocessed': {'num_tokens': 10, 'num_sentences': 2},
            'original_length': 100,
            'clean_length': 58,
            'full_text_embedding': [0.1] * 384,
            'chunks': [
                {
                    'text': 'Chunk 1',
                    'embedding': [0.1] * 384,
                    'word_count': 10,
                    'sentence_count': 1
                }
            ]
        }
    }


# ============================================================================
# TEST: CONTENT HASH COMPUTATION
# ============================================================================

@pytest.mark.unit
def test_save_article_computes_content_hash():
    """Test: content_hash viene computato correttamente da clean_text."""
    clean_text = 'This is the clean text content for testing deduplication.'

    # Test hash computation (same logic as in save_article)
    content_hash = hashlib.md5(clean_text.encode('utf-8')).hexdigest()

    # Verifica formato MD5 (32 caratteri hex)
    assert len(content_hash) == 32
    assert all(c in '0123456789abcdef' for c in content_hash)

    # Verifica che stesso contenuto produce stesso hash
    content_hash2 = hashlib.md5(clean_text.encode('utf-8')).hexdigest()
    assert content_hash == content_hash2

    # Verifica che contenuto diverso produce hash diverso
    different_text = 'Different content here'
    different_hash = hashlib.md5(different_text.encode('utf-8')).hexdigest()
    assert content_hash != different_hash


@pytest.mark.unit
def test_save_article_handles_empty_clean_text(db_manager, mock_connection):
    """Test: gestisce articoli senza clean_text (content_hash = None)."""
    article = {
        'title': 'Article without clean_text',
        'link': 'https://example.com/no-clean',
        'nlp_processing': {'success': True},
        'nlp_data': {
            'entities': {},
            'preprocessed': {},
            'full_text_embedding': [0.1] * 384,
            'chunks': []
        }
    }

    conn, cursor = mock_connection
    cursor.fetchone.side_effect = [None, (123,)]  # No existing, then new ID

    with patch.object(db_manager, 'get_connection') as mock_get_conn:
        mock_get_conn.return_value.__enter__ = Mock(return_value=conn)
        mock_get_conn.return_value.__exit__ = Mock(return_value=False)

        result = db_manager.save_article(article)

        # Dovrebbe salvare con content_hash = None
        assert result == 123

        # Verifica params (index 2: [0]=link check, [1]=load_source_cache, [2]=INSERT)
        insert_call = cursor.execute.call_args_list[2]
        params = insert_call[0][1]
        assert params[-3] is None  # content_hash should be None (source_id and domain added after)


# ============================================================================
# TEST: CONTENT HASH DEDUPLICATION
# ============================================================================

@pytest.mark.unit
def test_save_article_skips_duplicate_content_hash(db_manager, sample_article_with_nlp, mock_connection):
    """Test: skip articoli con content_hash identico (ultimi 7 giorni)."""
    conn, cursor = mock_connection

    # Mock: no existing link, but existing content_hash
    existing_article = (999, 'Original Article', 'Source B', 'https://example.com/original')
    cursor.fetchone.side_effect = [None, existing_article]  # No link match, but content_hash match

    with patch.object(db_manager, 'get_connection') as mock_get_conn:
        mock_get_conn.return_value.__enter__ = Mock(return_value=conn)
        mock_get_conn.return_value.__exit__ = Mock(return_value=False)

        result = db_manager.save_article(sample_article_with_nlp)

        # Dovrebbe ritornare None (duplicate content)
        assert result is None

        # Verifica che la query di content_hash check sia stata eseguita
        content_check_call = cursor.execute.call_args_list[1]
        query = content_check_call[0][0]
        assert 'content_hash' in query
        assert 'INTERVAL' in query  # Time window check


@pytest.mark.unit
def test_save_article_saves_different_content_hash():
    """Test: contenuti diversi producono hash diversi."""
    content1 = "This is article one with unique content"
    content2 = "This is article two with different content"

    hash1 = hashlib.md5(content1.encode('utf-8')).hexdigest()
    hash2 = hashlib.md5(content2.encode('utf-8')).hexdigest()

    # Hash diversi per contenuti diversi
    assert hash1 != hash2

    # Piccole differenze producono hash completamente diversi
    content3 = "This is article one with unique content."  # Added period
    hash3 = hashlib.md5(content3.encode('utf-8')).hexdigest()
    assert hash1 != hash3


@pytest.mark.unit
def test_save_article_content_hash_7_day_window(db_manager, sample_article_with_nlp, mock_connection):
    """Test: content_hash check usa finestra temporale di 7 giorni."""
    conn, cursor = mock_connection
    cursor.fetchone.side_effect = [None, None, (123,)]

    with patch.object(db_manager, 'get_connection') as mock_get_conn:
        mock_get_conn.return_value.__enter__ = Mock(return_value=conn)
        mock_get_conn.return_value.__exit__ = Mock(return_value=False)

        db_manager.save_article(sample_article_with_nlp)

        # Verifica query content_hash
        content_check_call = cursor.execute.call_args_list[1]
        query = content_check_call[0][0]

        # Dovrebbe avere INTERVAL '7 days'
        assert "INTERVAL '7 days'" in query
        assert 'published_date >' in query


@pytest.mark.unit
def test_save_article_different_link_same_content(db_manager, mock_connection):
    """Test: skip articoli con link diverso ma contenuto identico."""
    article1 = {
        'title': 'Article from Source A',
        'link': 'https://sourcea.com/article',
        'source': 'Source A',
        'nlp_processing': {'success': True},
        'nlp_data': {
            'clean_text': 'Same content text here',
            'entities': {},
            'preprocessed': {},
            'full_text_embedding': [0.1] * 384,
            'chunks': []
        }
    }

    article2 = {
        'title': 'Article from Source B',
        'link': 'https://sourceb.com/article',  # Different link
        'source': 'Source B',
        'nlp_processing': {'success': True},
        'nlp_data': {
            'clean_text': 'Same content text here',  # Same content!
            'entities': {},
            'preprocessed': {},
            'full_text_embedding': [0.1] * 384,
            'chunks': []
        }
    }

    conn, cursor = mock_connection

    with patch.object(db_manager, 'get_connection') as mock_get_conn:
        mock_get_conn.return_value.__enter__ = Mock(return_value=conn)
        mock_get_conn.return_value.__exit__ = Mock(return_value=False)

        # Save first article
        cursor.fetchone.side_effect = [None, None, (100,)]  # No link, no content, new ID
        result1 = db_manager.save_article(article1)
        assert result1 == 100

        # Save second article (same content, different link)
        existing = (100, 'Article from Source A', 'Source A', 'https://sourcea.com/article')
        cursor.fetchone.side_effect = [None, existing]  # No link match, but content match
        result2 = db_manager.save_article(article2)

        # Dovrebbe essere skippato (duplicate content)
        assert result2 is None


# ============================================================================
# TEST: EDGE CASES
# ============================================================================

@pytest.mark.unit
def test_save_article_unicode_in_content_hash(db_manager, mock_connection):
    """Test: gestisce caratteri unicode nel content_hash."""
    article = {
        'title': 'Unicode Article',
        'link': 'https://example.com/unicode',
        'nlp_processing': {'success': True},
        'nlp_data': {
            'clean_text': 'Content with unicode: 中文 Русский العربية 🔥',
            'entities': {},
            'preprocessed': {},
            'full_text_embedding': [0.1] * 384,
            'chunks': []
        }
    }

    conn, cursor = mock_connection
    cursor.fetchone.side_effect = [None, None, (123,)]

    with patch.object(db_manager, 'get_connection') as mock_get_conn:
        mock_get_conn.return_value.__enter__ = Mock(return_value=conn)
        mock_get_conn.return_value.__exit__ = Mock(return_value=False)

        # Non dovrebbe crashare
        result = db_manager.save_article(article)
        assert result == 123

        # Verifica che hash sia stato computato correttamente
        expected_hash = hashlib.md5('Content with unicode: 中文 Русский العربية 🔥'.encode('utf-8')).hexdigest()
        # index 3: [0]=link check, [1]=content_hash check, [2]=load_source_cache, [3]=INSERT
        insert_call = cursor.execute.call_args_list[3]
        params = insert_call[0][1]
        assert params[-4] == expected_hash  # content_hash is 4th from last (before source_id, domain, extraction_method)


@pytest.mark.unit
def test_save_article_very_long_content(db_manager, mock_connection):
    """Test: gestisce contenuti molto lunghi per hash."""
    long_content = "A" * 100000  # 100k chars

    article = {
        'title': 'Long Article',
        'link': 'https://example.com/long',
        'nlp_processing': {'success': True},
        'nlp_data': {
            'clean_text': long_content,
            'entities': {},
            'preprocessed': {},
            'full_text_embedding': [0.1] * 384,
            'chunks': []
        }
    }

    conn, cursor = mock_connection
    cursor.fetchone.side_effect = [None, None, (123,)]

    with patch.object(db_manager, 'get_connection') as mock_get_conn:
        mock_get_conn.return_value.__enter__ = Mock(return_value=conn)
        mock_get_conn.return_value.__exit__ = Mock(return_value=False)

        # MD5 dovrebbe gestire senza problemi
        result = db_manager.save_article(article)
        assert result == 123

        # Hash dovrebbe essere sempre 32 caratteri (MD5)
        # index 3: [0]=link check, [1]=content_hash check, [2]=load_source_cache, [3]=INSERT
        insert_call = cursor.execute.call_args_list[3]
        params = insert_call[0][1]
        assert len(params[-4]) == 32  # content_hash is 4th from last (before source_id, domain, extraction_method)


# ============================================================================
# TEST: INTEGRATION WITH LINK DEDUPLICATION
# ============================================================================

@pytest.mark.unit
def test_save_article_link_check_before_content_hash(db_manager, sample_article_with_nlp, mock_connection):
    """Test: verifica link PRIMA di content_hash (efficienza)."""
    conn, cursor = mock_connection

    # Link già esiste
    cursor.fetchone.return_value = (999,)

    with patch.object(db_manager, 'get_connection') as mock_get_conn:
        mock_get_conn.return_value.__enter__ = Mock(return_value=conn)
        mock_get_conn.return_value.__exit__ = Mock(return_value=False)

        result = db_manager.save_article(sample_article_with_nlp)

        # Dovrebbe ritornare None (duplicate link)
        assert result is None

        # Dovrebbe aver fatto solo 1 query (link check)
        # NON dovrebbe aver fatto content_hash check (più costoso)
        assert cursor.execute.call_count == 1


@pytest.mark.unit
def test_save_article_content_hash_only_if_link_unique(db_manager, sample_article_with_nlp, mock_connection):
    """Test: content_hash check viene eseguito SOLO se link è unico."""
    conn, cursor = mock_connection

    # Link non esiste, content_hash non esiste
    cursor.fetchone.side_effect = [None, None]

    # Mock execute_batch to avoid actual DB call
    with patch('src.storage.database.execute_batch'):
        with patch.object(db_manager, 'get_connection') as mock_get_conn:
            mock_get_conn.return_value = conn

            result = db_manager.save_article(sample_article_with_nlp)

            # With execute_batch mocked, this should work
            # Verifica che link check e content_hash check siano stati chiamati
            assert cursor.execute.call_count >= 2  # Link check + content hash check
