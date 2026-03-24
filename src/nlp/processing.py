"""
NLP processing module for text analysis, entity extraction, embeddings, and RAG preparation.
Combines deep NLP analysis with optimized text chunking for RAG systems.
"""

import json
import re
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any
from datetime import datetime

from bs4 import BeautifulSoup
import spacy
from spacy.tokens import Doc
from sentence_transformers import SentenceTransformer
import numpy as np

from ..utils.logger import get_logger

logger = get_logger(__name__)


class NLPProcessor:
    """
    Hybrid NLP processor combining:
    - Text cleaning and chunking (optimized for RAG)
    - Entity extraction (NER)
    - Embeddings generation (for vector search)
    - Linguistic preprocessing (tokenization, lemmatization)
    """

    def __init__(
        self,
        spacy_model: str = "xx_ent_wiki_sm",
        embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        batch_size: int = 32,
        chunk_size: int = 500,
        chunk_overlap: int = 50
    ):
        """
        Initialize hybrid NLP processor.

        Args:
            spacy_model: spaCy model name for multilingual NLP (supports 50+ languages)
            embedding_model: Sentence Transformers model for embeddings
            batch_size: Batch size for processing
            chunk_size: Approximate words per chunk for RAG
            chunk_overlap: Word overlap between chunks for context preservation
        """
        logger.info("Initializing hybrid NLP processor...")

        # Load spaCy model for Italian
        try:
            self.nlp = spacy.load(spacy_model)
            # Increase max length for very long articles
            self.nlp.max_length = 2000000

            # Add sentencizer if not present (required for xx_ent_wiki_sm and similar minimal models)
            if "sentencizer" not in self.nlp.pipe_names and "parser" not in self.nlp.pipe_names:
                self.nlp.add_pipe("sentencizer")
                logger.info("✓ Added sentencizer component to pipeline (required for sentence segmentation)")

            logger.info(f"✓ Loaded spaCy model: {spacy_model}")
        except OSError:
            logger.error(f"spaCy model '{spacy_model}' not found. Run: python -m spacy download {spacy_model}")
            raise

        # Load Sentence Transformer for embeddings
        try:
            self.embedding_model = SentenceTransformer(embedding_model)
            logger.info(f"✓ Loaded embedding model: {embedding_model}")
        except Exception as e:
            logger.error(f"Failed to load embedding model: {e}")
            raise

        self.batch_size = batch_size
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        logger.info("Hybrid NLP processor initialized successfully")

    def clean_text(self, text: str) -> str:
        """
        Clean text from scraping artifacts and formatting noise.

        Args:
            text: Raw text to clean

        Returns:
            Cleaned text
        """
        if not text:
            return ""

        # 1. Strip HTML tags and attributes safely using BeautifulSoup
        if '<' in text:
            text = BeautifulSoup(text, 'html.parser').get_text(separator=' ')

        # 3. Remove URLs (http, https, www)
        text = re.sub(r'https?://[^\s]+', ' ', text, flags=re.IGNORECASE)
        text = re.sub(r'www\.[^\s]+', ' ', text, flags=re.IGNORECASE)

        # 4. Decode HTML entities
        text = text.replace('&nbsp;', ' ')
        text = text.replace('&amp;', '&')
        text = text.replace('&lt;', '<')
        text = text.replace('&gt;', '>')
        text = text.replace('&quot;', '"')
        text = text.replace('&#39;', "'")
        text = text.replace('&apos;', "'")

        # 5. Remove markdown links and brackets
        text = re.sub(r'\[.*?\]\(.*?\)', ' ', text)  # [text](url)
        text = re.sub(r'\[.*?\]', ' ', text)  # [text]

        # 6. Remove common noise patterns in news articles
        patterns_to_remove = [
            r"Follow us on Twitter",
            r"Click here to subscribe",
            r"Share this article",
            r"Photo:",
            r"Source:",
            r"Read more:",
            r"Subscribe to our newsletter",
            r"Sign up for",
            r"Related articles:",
            r"More from",
            r"Advertisement",
            r"Sponsored content",
            r"Copyright \d{4}",
            r"All rights reserved"
        ]

        for pattern in patterns_to_remove:
            text = re.sub(pattern, " ", text, flags=re.IGNORECASE)

        # 7. Normalize whitespace (remove double spaces, tabs, excessive newlines)
        text = " ".join(text.split())

        return text.strip()

    def create_chunks(self, text: str, is_long_document: bool = False) -> List[Dict[str, Any]]:
        """
        Split text into semantic chunks based on complete sentences with overlap.

        For long documents (PDFs from pymupdf4llm), uses section-aware chunking
        that splits on Markdown headings first, then applies sliding window within
        each section.

        Args:
            text: Cleaned text to chunk
            is_long_document: If True and text contains Markdown headings, use
                              section-aware chunking (set by PDFIngestor)

        Returns:
            List of chunk dictionaries with text and metadata
        """
        if not text:
            return []

        # Section-aware chunking for long documents with Markdown headings
        if is_long_document and '\n## ' in text:
            chunks = self._create_section_chunks(text)
            if chunks:
                return chunks

        # Standard sliding window chunking
        return self._create_sliding_window_chunks(text)

    def _create_section_chunks(self, text: str) -> List[Dict[str, Any]]:
        """
        Split Markdown on ## headings, then sliding window within each section.

        Preserves document structure by keeping section titles as metadata
        on each chunk. Falls back to standard chunking if no sections found.

        Args:
            text: Markdown text with ## headings (from pymupdf4llm)

        Returns:
            List of chunk dicts with 'section_title' metadata, or empty list
        """
        # Split on Markdown headings (# or ##), preserving the heading line
        sections = re.split(r'\n(?=#{1,2}\s)', text)
        all_chunks = []

        for section in sections:
            section = section.strip()
            if not section:
                continue

            lines = section.split('\n', 1)
            if lines[0].startswith('#'):
                title = lines[0].lstrip('#').strip()
                body = lines[1] if len(lines) > 1 else ''
            else:
                title = 'Introduction'
                body = section

            if not body.strip():
                continue

            section_chunks = self._create_sliding_window_chunks(body)
            for chunk in section_chunks:
                chunk['section_title'] = title
            all_chunks.extend(section_chunks)

        return all_chunks

    def _create_sliding_window_chunks(self, text: str) -> List[Dict[str, Any]]:
        """
        Standard sliding window chunking with sentence boundaries and overlap.

        Args:
            text: Text to chunk

        Returns:
            List of chunk dictionaries with text and metadata
        """
        if not text or not text.strip():
            return []

        # Use spaCy to accurately split into sentences
        doc = self.nlp(text)
        sentences = [sent.text.strip() for sent in doc.sents]

        chunks = []
        current_chunk = []
        current_length = 0

        for sentence in sentences:
            sent_len = len(sentence.split())

            # If adding sentence exceeds chunk size...
            if current_length + sent_len > self.chunk_size and current_chunk:
                # ...save current chunk
                chunk_text = " ".join(current_chunk)
                chunks.append({
                    'text': chunk_text,
                    'word_count': current_length,
                    'sentence_count': len(current_chunk)
                })

                # Overlap management: keep last sentences for next chunk
                overlap_buffer = []
                overlap_len = 0
                for s in reversed(current_chunk):
                    s_len = len(s.split())
                    if overlap_len + s_len < self.chunk_overlap:
                        overlap_buffer.insert(0, s)
                        overlap_len += s_len
                    else:
                        break

                current_chunk = overlap_buffer
                current_length = overlap_len

            current_chunk.append(sentence)
            current_length += sent_len

        # Add remaining chunk
        if current_chunk:
            chunk_text = " ".join(current_chunk)
            chunks.append({
                'text': chunk_text,
                'word_count': current_length,
                'sentence_count': len(current_chunk)
            })

        return chunks

    def preprocess_text(self, text: str) -> Dict:
        """
        Preprocess text with spaCy: tokenization, lemmatization, POS tagging.

        Args:
            text: Raw text to process

        Returns:
            Dictionary with preprocessed data
        """
        if not text or not isinstance(text, str):
            return {
                'tokens': [],
                'lemmas': [],
                'pos_tags': [],
                'sentences': [],
                'num_tokens': 0,
                'num_sentences': 0
            }

        doc = self.nlp(text)

        return {
            'tokens': [token.text for token in doc],
            'lemmas': [token.lemma_ for token in doc],
            'pos_tags': [(token.text, token.pos_) for token in doc],
            'sentences': [sent.text for sent in doc.sents],
            'num_tokens': len(doc),
            'num_sentences': len(list(doc.sents))
        }

    def extract_entities(self, text: str) -> Dict:
        """
        Extract named entities from text using spaCy NER.

        Args:
            text: Text to extract entities from

        Returns:
            Dictionary with entities organized by type
        """
        if not text or not isinstance(text, str):
            return {'entities': [], 'by_type': {}, 'entity_count': 0}

        doc = self.nlp(text)

        entities = []
        entities_by_type = {}

        for ent in doc.ents:
            entity_data = {
                'text': ent.text,
                'label': ent.label_,
                'start': ent.start_char,
                'end': ent.end_char
            }
            entities.append(entity_data)

            # Group by type
            if ent.label_ not in entities_by_type:
                entities_by_type[ent.label_] = []
            entities_by_type[ent.label_].append(ent.text)

        return {
            'entities': entities,
            'by_type': entities_by_type,
            'entity_count': len(entities)
        }

    def generate_embedding(self, text: str) -> np.ndarray:
        """
        Generate semantic embedding for text.

        Args:
            text: Text to embed

        Returns:
            Embedding vector as numpy array
        """
        if not text or not isinstance(text, str):
            # Return zero vector if text is empty
            return np.zeros(self.embedding_model.get_sentence_embedding_dimension())

        embedding = self.embedding_model.encode(text, convert_to_numpy=True)
        return embedding

    def generate_chunk_embeddings(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Generate embeddings for each chunk (optimized for RAG).

        Args:
            chunks: List of chunk dictionaries

        Returns:
            Chunks enriched with embeddings
        """
        if not chunks:
            return []

        # Extract texts for batch embedding
        chunk_texts = [chunk['text'] for chunk in chunks]

        # Generate embeddings in batch (much faster)
        embeddings = self.embedding_model.encode(chunk_texts, convert_to_numpy=True, show_progress_bar=False)

        # Add embeddings to chunks
        for i, chunk in enumerate(chunks):
            chunk['embedding'] = embeddings[i].tolist()
            chunk['embedding_dim'] = len(embeddings[i])

        return chunks

    def process_article(self, article: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a single article with complete NLP pipeline:
        1. Text cleaning
        2. Chunking for RAG
        3. Entity extraction
        4. Embeddings generation
        5. Linguistic preprocessing

        Args:
            article: Article dictionary with 'full_content' field

        Returns:
            Article dictionary enriched with NLP data
        """
        # Extract text from full_content
        full_content = article.get('full_content', {})

        if isinstance(full_content, dict):
            raw_text = full_content.get('text', '')
        elif isinstance(full_content, str):
            raw_text = full_content
        else:
            raw_text = article.get('summary', '')  # Fallback to summary

        if not raw_text:
            logger.warning(f"No text found for article: {article.get('title', 'Unknown')[:50]}...")
            article['nlp_processing'] = {
                'success': False,
                'error': 'No text content available'
            }
            return article

        try:
            logger.debug(f"Processing article: {article.get('title', 'Unknown')[:50]}...")

            # Step 1: Clean text
            clean_text = self.clean_text(raw_text)

            # Step 2: Create chunks for RAG (only if text is substantial)
            # Detect long documents (PDFs) for section-aware chunking
            is_long_doc = False
            if isinstance(full_content, dict):
                is_long_doc = full_content.get('is_long_document', False)
            if not is_long_doc:
                is_long_doc = article.get('is_long_document', False)

            chunks = []
            if len(clean_text.split()) > 100:
                chunks = self.create_chunks(clean_text, is_long_document=is_long_doc)
            else:
                # Short text = single chunk
                if clean_text:
                    chunks = [{
                        'text': clean_text,
                        'word_count': len(clean_text.split()),
                        'sentence_count': 1
                    }]

            # Step 3: Generate embeddings for each chunk
            chunks_with_embeddings = self.generate_chunk_embeddings(chunks)

            # Step 4: Extract entities (from full clean text)
            entities = self.extract_entities(clean_text)

            # Step 5: Preprocess text (tokenization, lemmatization)
            preprocessed = self.preprocess_text(clean_text)

            # Step 6: Generate full-text embedding (for article-level similarity)
            full_text_embedding = self.generate_embedding(clean_text)

            # Add all NLP data to article
            article['nlp_data'] = {
                'clean_text': clean_text,
                'chunks': chunks_with_embeddings,
                'chunk_count': len(chunks_with_embeddings),
                'entities': entities,
                'preprocessed': preprocessed,
                'full_text_embedding': full_text_embedding.tolist(),
                'embedding_dim': len(full_text_embedding),
                'original_length': len(raw_text),
                'clean_length': len(clean_text),
                'processed_at': datetime.now().isoformat()
            }

            article['nlp_processing'] = {
                'success': True,
                'timestamp': datetime.now().isoformat()
            }

            return article

        except Exception as e:
            logger.error(f"Error processing article '{article.get('title', 'Unknown')[:50]}...': {e}")
            article['nlp_processing'] = {
                'success': False,
                'error': str(e)
            }
            return article

    def process_batch(self, articles: List[Dict], show_progress: bool = True) -> List[Dict]:
        """
        Process a batch of articles with NLP pipeline.

        Args:
            articles: List of article dictionaries
            show_progress: Whether to show progress logs

        Returns:
            List of processed articles with NLP data
        """
        logger.info(f"Processing {len(articles)} articles with hybrid NLP pipeline...")

        processed_articles = []
        success_count = 0

        for i, article in enumerate(articles):
            processed = self.process_article(article)
            processed_articles.append(processed)

            if processed.get('nlp_processing', {}).get('success'):
                success_count += 1

            if show_progress and (i + 1) % 10 == 0:
                logger.info(f"Progress: {i + 1}/{len(articles)} articles processed")

        logger.info(f"✓ NLP processing complete: {success_count}/{len(articles)} successful")

        return processed_articles

    def get_processing_stats(self, articles: List[Dict]) -> Dict:
        """
        Get statistics about NLP processing results.

        Args:
            articles: List of processed articles

        Returns:
            Dictionary with processing statistics
        """
        total = len(articles)
        successful = sum(1 for a in articles if a.get('nlp_processing', {}).get('success'))

        # Entity statistics
        all_entities = []
        entity_types = {}

        # Chunk statistics
        total_chunks = 0
        chunk_sizes = []

        for article in articles:
            nlp_data = article.get('nlp_data', {})

            # Entities
            entities = nlp_data.get('entities', {})
            all_entities.extend(entities.get('entities', []))

            for entity_type, entity_list in entities.get('by_type', {}).items():
                if entity_type not in entity_types:
                    entity_types[entity_type] = 0
                entity_types[entity_type] += len(entity_list)

            # Chunks
            chunks = nlp_data.get('chunks', [])
            total_chunks += len(chunks)
            for chunk in chunks:
                chunk_sizes.append(chunk.get('word_count', 0))

        # Token statistics
        token_counts = [
            a.get('nlp_data', {}).get('preprocessed', {}).get('num_tokens', 0)
            for a in articles if a.get('nlp_processing', {}).get('success')
        ]

        avg_tokens = sum(token_counts) / len(token_counts) if token_counts else 0
        avg_chunk_size = sum(chunk_sizes) / len(chunk_sizes) if chunk_sizes else 0

        return {
            'total_articles': total,
            'successful_processing': successful,
            'success_rate': f"{successful}/{total} ({successful/total*100:.1f}%)" if total > 0 else "0/0",
            'total_entities_extracted': len(all_entities),
            'entities_by_type': entity_types,
            'avg_tokens_per_article': round(avg_tokens, 1),
            'total_chunks': total_chunks,
            'avg_chunks_per_article': round(total_chunks / successful, 1) if successful > 0 else 0,
            'avg_chunk_size': round(avg_chunk_size, 1),
            'embedding_dimension': articles[0].get('nlp_data', {}).get('embedding_dim', 0) if articles else 0
        }


def process_ingested_articles(
    input_file: str,
    output_file: Optional[str] = None,
    spacy_model: str = "it_core_news_lg",
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    chunk_size: int = 500,
    chunk_overlap: int = 50
) -> Tuple[List[Dict], Dict]:
    """
    Process articles from ingestion JSON file with hybrid NLP pipeline.

    Args:
        input_file: Path to ingestion JSON output
        output_file: Optional path to save processed output (auto-generated if None)
        spacy_model: spaCy model to use
        embedding_model: Sentence Transformers model to use
        chunk_size: Words per chunk for RAG
        chunk_overlap: Word overlap between chunks

    Returns:
        Tuple of (processed articles, statistics)
    """
    logger.info("="*80)
    logger.info("HYBRID NLP PROCESSING PIPELINE")
    logger.info("="*80)

    # Load articles
    logger.info(f"\n[STEP 1] Loading articles from: {input_file}")
    with open(input_file, 'r', encoding='utf-8') as f:
        articles = json.load(f)
    logger.info(f"✓ Loaded {len(articles)} articles")

    # Initialize processor
    logger.info("\n[STEP 2] Initializing hybrid NLP processor...")
    processor = NLPProcessor(
        spacy_model=spacy_model,
        embedding_model=embedding_model,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap
    )

    # Process articles
    logger.info("\n[STEP 3] Processing articles with NLP pipeline...")
    logger.info(f"  - Text cleaning & chunking (size={chunk_size}, overlap={chunk_overlap})")
    logger.info(f"  - Entity extraction (NER)")
    logger.info(f"  - Embedding generation for chunks & full text")
    processed_articles = processor.process_batch(articles)

    # Get statistics
    stats = processor.get_processing_stats(processed_articles)

    # Save output
    if output_file is None:
        # Auto-generate filename
        input_path = Path(input_file)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = input_path.parent / f"articles_nlp_{timestamp}.json"

    logger.info(f"\n[STEP 4] Saving processed articles to: {output_file}")
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(processed_articles, f, indent=2, ensure_ascii=False)
    logger.info(f"✓ Saved {len(processed_articles)} processed articles")

    logger.info("\n" + "="*80)
    logger.info("NLP PROCESSING COMPLETE")
    logger.info("="*80)

    return processed_articles, stats


if __name__ == "__main__":
    # Example usage: process latest ingestion file
    import sys
    from pathlib import Path

    # Find most recent ingestion file
    data_dir = Path("data")
    ingestion_files = sorted(data_dir.glob("articles_2*.json"), key=lambda p: p.stat().st_mtime, reverse=True)

    if not ingestion_files:
        print("No ingestion files found in data/ directory")
        sys.exit(1)

    latest_file = ingestion_files[0]
    print(f"Processing latest ingestion file: {latest_file}")

    # Process articles
    processed_articles, stats = process_ingested_articles(str(latest_file))

    # Print statistics
    print("\n" + "="*80)
    print("NLP PROCESSING STATISTICS")
    print("="*80)
    print(f"\nTotal articles: {stats['total_articles']}")
    print(f"Successful processing: {stats['success_rate']}")
    print(f"Total entities extracted: {stats['total_entities_extracted']}")
    print(f"Average tokens per article: {stats['avg_tokens_per_article']}")
    print(f"Total chunks created: {stats['total_chunks']}")
    print(f"Average chunks per article: {stats['avg_chunks_per_article']}")
    print(f"Average chunk size: {stats['avg_chunk_size']} words")
    print(f"Embedding dimension: {stats['embedding_dimension']}")
    print(f"\nTop 10 entity types:")
    for entity_type, count in sorted(stats['entities_by_type'].items(), key=lambda x: x[1], reverse=True)[:10]:
        print(f"  {entity_type}: {count}")
