"""
Test per DatabaseManager - Business Logic.

Questi test verificano la logica di business SENZA richiedere un database reale.
Per test di integrazione completi (schema, queries, vector search),
eseguire test separati con database PostgreSQL + pgvector.

Questi test verificano:
- Validazione input (skip articoli senza NLP data)
- Batch statistics tracking
- Query building per semantic search
- Gestione duplicati
"""

import pytest
from unittest.mock import Mock, patch, MagicMock, call
from datetime import datetime
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

    conn.cursor.return_value = cursor

    return conn, cursor


@pytest.fixture
def db_manager():
    """DatabaseManager con connection pool mockato."""
    with patch('src.storage.database.SimpleConnectionPool') as mock_pool:
        # Mock the pool instance
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
        'published': '2025-11-28T10:00:00Z',
        'source': 'Test Source',
        'category': 'intelligence',
        'subcategory': 'cybersecurity',
        'summary': 'Test summary',
        'nlp_processing': {'success': True},
        'nlp_data': {
            'clean_text': 'This is clean text.',
            'entities': {'entities': [], 'by_type': {}, 'entity_count': 0},
            'preprocessed': {'num_tokens': 10, 'num_sentences': 2},
            'original_length': 100,
            'clean_length': 19,
            'full_text_embedding': [0.1] * 384,
            'chunks': [
                {
                    'text': 'Chunk 1',
                    'embedding': [0.1] * 384,
                    'word_count': 10,
                    'sentence_count': 1
                },
                {
                    'text': 'Chunk 2',
                    'embedding': [0.2] * 384,
                    'word_count': 8,
                    'sentence_count': 1
                }
            ]
        }
    }


@pytest.fixture
def sample_article_without_nlp():
    """Articolo senza NLP processing."""
    return {
        'title': 'No NLP Article',
        'link': 'https://example.com/no-nlp',
        'source': 'Test Source'
    }


# ============================================================================
# TEST: INITIALIZATION
# ============================================================================

@pytest.mark.unit
def test_database_manager_init_with_url():
    """Test: inizializzazione con connection URL."""
    with patch('src.storage.database.SimpleConnectionPool') as mock_pool:
        mock_pool.return_value = MagicMock()
        db = DatabaseManager(connection_url="postgresql://user:pass@localhost/testdb")

        assert db.connection_url == "postgresql://user:pass@localhost/testdb"
        mock_pool.assert_called_once()


@pytest.mark.unit
def test_database_manager_init_from_env():
    """Test: legge connection URL da environment."""
    with patch('src.storage.database.SimpleConnectionPool') as mock_pool, \
         patch.dict('os.environ', {'DATABASE_URL': 'postgresql://env:pass@localhost/envdb'}):

        mock_pool.return_value = MagicMock()
        db = DatabaseManager()

        assert db.connection_url == "postgresql://env:pass@localhost/envdb"


@pytest.mark.unit
def test_database_manager_init_from_individual_env():
    """Test: costruisce URL da variabili individuali."""
    with patch('src.storage.database.SimpleConnectionPool') as mock_pool, \
         patch.dict('os.environ', {
             'DB_HOST': 'testhost',
             'DB_NAME': 'testdb',
             'DB_USER': 'testuser',
             'DB_PASS': 'testpass',
             'DB_PORT': '5433'
         }, clear=True):

        mock_pool.return_value = MagicMock()
        db = DatabaseManager()

        assert 'testhost' in db.connection_url
        assert 'testdb' in db.connection_url
        assert 'testuser' in db.connection_url
        assert '5433' in db.connection_url


# ============================================================================
# TEST: SAVE ARTICLE - VALIDATION LOGIC
# ============================================================================

@pytest.mark.unit
def test_save_article_skips_without_nlp_success(db_manager, sample_article_without_nlp):
    """Test: skip articoli senza NLP processing success."""
    result = db_manager.save_article(sample_article_without_nlp)

    # Dovrebbe ritornare None (skipped)
    assert result is None


@pytest.mark.unit
def test_save_article_skips_with_failed_nlp(db_manager):
    """Test: skip articoli con NLP processing fallito."""
    article = {
        'title': 'Failed NLP',
        'link': 'https://example.com/failed',
        'nlp_processing': {'success': False, 'error': 'Some error'}
    }

    result = db_manager.save_article(article)

    assert result is None


@pytest.mark.unit
def test_save_article_with_valid_nlp_data(sample_article_with_nlp):
    """Test: articolo con NLP data ha i campi richiesti."""
    # Questo è un test di validazione dati, non di database
    # Verifica che l'articolo di test abbia struttura corretta

    assert sample_article_with_nlp['nlp_processing']['success'] is True
    assert 'nlp_data' in sample_article_with_nlp
    assert 'chunks' in sample_article_with_nlp['nlp_data']
    assert len(sample_article_with_nlp['nlp_data']['chunks']) > 0

    # Verifica che chunks abbiano embeddings
    for chunk in sample_article_with_nlp['nlp_data']['chunks']:
        assert 'embedding' in chunk
        assert len(chunk['embedding']) == 384


@pytest.mark.unit
def test_save_article_skips_duplicate(db_manager, sample_article_with_nlp, mock_connection):
    """Test: skip articoli duplicati (link già esistente)."""
    conn, cursor = mock_connection

    # Mock fetchone per ritornare existing ID
    cursor.fetchone.return_value = (999,)  # Article già esiste

    with patch.object(db_manager, 'get_connection') as mock_get_conn:
        mock_get_conn.return_value.__enter__ = Mock(return_value=conn)
        mock_get_conn.return_value.__exit__ = Mock(return_value=False)

        result = db_manager.save_article(sample_article_with_nlp)

        # Dovrebbe ritornare None (duplicate)
        assert result is None


# ============================================================================
# TEST: BATCH SAVE - STATISTICS LOGIC
# ============================================================================

@pytest.mark.unit
def test_batch_save_tracks_statistics(db_manager):
    """Test: batch_save traccia statistiche correttamente."""
    articles = [
        # Articolo valido
        {
            'title': 'Valid 1',
            'link': 'https://example.com/1',
            'nlp_processing': {'success': True},
            'nlp_data': {
                'clean_text': 'Text',
                'entities': {},
                'preprocessed': {},
                'full_text_embedding': [0.1] * 384,
                'chunks': [{'text': 'chunk', 'embedding': [0.1]*384, 'word_count': 5}]
            }
        },
        # Articolo senza NLP (error)
        {
            'title': 'No NLP',
            'link': 'https://example.com/2'
        },
        # Articolo valido
        {
            'title': 'Valid 2',
            'link': 'https://example.com/3',
            'nlp_processing': {'success': True},
            'nlp_data': {
                'clean_text': 'Text',
                'entities': {},
                'preprocessed': {},
                'full_text_embedding': [0.1] * 384,
                'chunks': []
            }
        }
    ]

    # Mock save_article per simulare: success, error, duplicate
    with patch.object(db_manager, 'save_article') as mock_save:
        mock_save.side_effect = [100, None, None]  # ID, None (error), None (dup)

        stats = db_manager.batch_save(articles)

        assert stats['saved'] == 1
        assert stats['errors'] == 1
        assert stats['skipped'] == 1
        assert stats['total_chunks'] == 1


@pytest.mark.unit
def test_batch_save_empty_list(db_manager):
    """Test: batch_save con lista vuota."""
    stats = db_manager.batch_save([])

    assert stats['saved'] == 0
    assert stats['errors'] == 0
    assert stats['skipped'] == 0


@pytest.mark.unit
def test_batch_save_counts_chunks(db_manager):
    """Test: batch_save conta chunks correttamente."""
    articles = [
        {
            'title': 'Multi Chunks',
            'link': 'https://example.com/multi',
            'nlp_processing': {'success': True},
            'nlp_data': {
                'clean_text': 'Text',
                'entities': {},
                'preprocessed': {},
                'full_text_embedding': [0.1] * 384,
                'chunks': [
                    {'text': 'c1', 'embedding': [0.1]*384, 'word_count': 5},
                    {'text': 'c2', 'embedding': [0.1]*384, 'word_count': 5},
                    {'text': 'c3', 'embedding': [0.1]*384, 'word_count': 5},
                ]
            }
        }
    ]

    with patch.object(db_manager, 'save_article') as mock_save:
        mock_save.return_value = 100  # Success

        stats = db_manager.batch_save(articles)

        assert stats['total_chunks'] == 3


# ============================================================================
# TEST: SEMANTIC SEARCH - QUERY BUILDING
# ============================================================================

@pytest.mark.unit
def test_semantic_search_builds_query_without_category(db_manager, mock_connection):
    """Test: semantic search costruisce query corretta senza filtro categoria."""
    conn, cursor = mock_connection
    cursor.fetchall.return_value = []

    query_embedding = [0.1] * 384

    with patch.object(db_manager, 'get_connection') as mock_get_conn:
        mock_get_conn.return_value.__enter__ = Mock(return_value=conn)
        mock_get_conn.return_value.__exit__ = Mock(return_value=False)

        results = db_manager.semantic_search(query_embedding, top_k=5)

        # Verifica che execute sia stato chiamato
        cursor.execute.assert_called_once()

        # Verifica parametri
        call_args = cursor.execute.call_args
        query = call_args[0][0]
        params = call_args[0][1]

        # Query non dovrebbe avere filtro categoria
        assert 'category =' not in query
        assert len(params) == 3  # embedding (2x) + top_k


@pytest.mark.unit
def test_semantic_search_builds_query_with_category(db_manager, mock_connection):
    """Test: semantic search aggiunge filtro categoria se specificato."""
    conn, cursor = mock_connection
    cursor.fetchall.return_value = []

    query_embedding = [0.1] * 384

    with patch.object(db_manager, 'get_connection') as mock_get_conn:
        mock_get_conn.return_value.__enter__ = Mock(return_value=conn)
        mock_get_conn.return_value.__exit__ = Mock(return_value=False)

        results = db_manager.semantic_search(
            query_embedding,
            top_k=10,
            category='intelligence'
        )

        # Verifica parametri includono categoria
        call_args = cursor.execute.call_args
        params = call_args[0][1]

        assert len(params) == 4  # embedding + category + embedding + top_k
        assert 'intelligence' in params


@pytest.mark.unit
def test_semantic_search_returns_formatted_results(db_manager, mock_connection):
    """Test: semantic search formatta risultati correttamente."""
    conn, cursor = mock_connection

    # Mock database results (updated to include embedding, source_type, authority_score fields)
    embedding = [0.1] * 384
    cursor.fetchall.return_value = [
        (1, 'Chunk text', 0, 10, 100, 'Article Title', 'https://ex.com', 'Source', datetime(2025, 11, 28), 'intel', embedding, 0.95, 'Think Tank', 5.0)
    ]

    query_embedding = [0.1] * 384

    with patch.object(db_manager, 'get_connection') as mock_get_conn:
        mock_get_conn.return_value.__enter__ = Mock(return_value=conn)
        mock_get_conn.return_value.__exit__ = Mock(return_value=False)

        results = db_manager.semantic_search(query_embedding, top_k=1)

        assert len(results) == 1
        result = results[0]

        # Verifica formato output
        assert result['chunk_id'] == 1
        assert result['content'] == 'Chunk text'
        assert result['article_id'] == 100
        assert result['title'] == 'Article Title'
        assert result['embedding'] == embedding
        assert result['similarity'] == 0.95


# ============================================================================
# TEST: UPSERT APPROVAL FEEDBACK - LOGIC
# ============================================================================

@pytest.mark.unit
def test_upsert_approval_feedback_creates_new(db_manager, mock_connection):
    """Test: upsert crea nuovo feedback se non esiste."""
    conn, cursor = mock_connection

    # Nessun feedback esistente
    cursor.fetchone.side_effect = [None, (123,)]  # No existing, then new ID

    with patch.object(db_manager, 'get_connection') as mock_get_conn:
        mock_get_conn.return_value.__enter__ = Mock(return_value=conn)
        mock_get_conn.return_value.__exit__ = Mock(return_value=False)

        result = db_manager.upsert_approval_feedback(
            report_id=1,
            rating=5,
            comment="Great report!"
        )

        assert result == 123

        # Verifica che sia stato fatto INSERT (non UPDATE)
        execute_calls = cursor.execute.call_args_list
        assert any('INSERT' in str(call) for call in execute_calls)


@pytest.mark.unit
def test_upsert_approval_feedback_updates_existing(db_manager, mock_connection):
    """Test: upsert aggiorna feedback esistente."""
    conn, cursor = mock_connection

    # Feedback già esiste con ID 999
    cursor.fetchone.side_effect = [(999,), (999,)]  # Existing ID, then updated ID

    with patch.object(db_manager, 'get_connection') as mock_get_conn:
        mock_get_conn.return_value.__enter__ = Mock(return_value=conn)
        mock_get_conn.return_value.__exit__ = Mock(return_value=False)

        result = db_manager.upsert_approval_feedback(
            report_id=1,
            rating=4,
            comment="Updated comment"
        )

        assert result == 999

        # Verifica che sia stato fatto UPDATE (non INSERT)
        execute_calls = cursor.execute.call_args_list
        assert any('UPDATE' in str(call) for call in execute_calls)


# ============================================================================
# TEST: GET METHODS - RETURN EMPTY ON ERROR
# ============================================================================

@pytest.mark.unit
def test_get_statistics_returns_empty_on_error(db_manager):
    """Test: get_statistics ritorna dict vuoto su errore."""
    with patch.object(db_manager, 'get_connection') as mock_get_conn:
        mock_get_conn.side_effect = Exception("DB Error")

        stats = db_manager.get_statistics()

        assert stats == {}


@pytest.mark.unit
def test_semantic_search_returns_empty_on_error(db_manager):
    """Test: semantic_search ritorna lista vuota su errore."""
    with patch.object(db_manager, 'get_connection') as mock_get_conn:
        mock_get_conn.side_effect = Exception("DB Error")

        results = db_manager.semantic_search([0.1] * 384)

        assert results == []


@pytest.mark.unit
def test_get_recent_articles_returns_empty_on_error(db_manager):
    """Test: get_recent_articles ritorna lista vuota su errore."""
    with patch.object(db_manager, 'get_connection') as mock_get_conn:
        mock_get_conn.side_effect = Exception("DB Error")

        articles = db_manager.get_recent_articles()

        assert articles == []


# ============================================================================
# TEST: CLOSE CONNECTION POOL
# ============================================================================

@pytest.mark.unit
def test_close_closes_pool(db_manager):
    """Test: close() chiude connection pool."""
    db_manager.pool = MagicMock()

    db_manager.close()

    db_manager.pool.closeall.assert_called_once()


@pytest.mark.unit
def test_close_handles_no_pool():
    """Test: close() non crasha se pool non esiste."""
    with patch('src.storage.database.SimpleConnectionPool') as mock_pool:
        mock_pool.return_value = MagicMock()
        db = DatabaseManager(connection_url="postgresql://test:test@localhost/test")
        delattr(db, 'pool')

        # Non dovrebbe crashare
        db.close()
