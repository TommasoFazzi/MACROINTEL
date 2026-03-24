"""
PDF document ingestion module for institutional documents (SIPRI, CRS, NATO reports, etc.).

Extracts structured Markdown from PDF files using pymupdf4llm (PyMuPDF wrapper optimized
for RAG/LLM pipelines). Produces article dicts compatible with the existing NLP pipeline.

pymupdf4llm.to_markdown() handles:
- Header/footer removal
- Word rejoining (broken across lines)
- Markdown output with # headings, ## sections, formatted tables
- No custom regex heuristics needed
"""

import os
import asyncio
import aiohttp
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional
from urllib.parse import urlparse

try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False

try:
    import pymupdf4llm
    PYMUPDF4LLM_AVAILABLE = True
except ImportError:
    PYMUPDF4LLM_AVAILABLE = False

from ..utils.logger import get_logger

logger = get_logger(__name__)


class PDFIngestor:
    """Ingests PDF documents and converts them to article dicts compatible with the pipeline."""

    def __init__(self, timeout: int = 30):
        """
        Initialize the PDF ingestor.

        Args:
            timeout: Download timeout in seconds
        """
        if not PYMUPDF_AVAILABLE:
            raise ImportError("pymupdf is required. pip install pymupdf")

        self.timeout = timeout
        self._use_4llm = PYMUPDF4LLM_AVAILABLE
        if self._use_4llm:
            logger.info("PDFIngestor initialized with pymupdf4llm (Markdown output)")
        else:
            logger.info("PDFIngestor initialized with PyMuPDF (plain text fallback)")

    async def download_pdf(self, url: str, headers: Optional[Dict] = None) -> Optional[bytes]:
        """
        Download PDF from URL asynchronously.

        Args:
            url: URL to PDF file
            headers: Optional HTTP headers (e.g. User-Agent for consistency with HTML crawler)

        Returns:
            PDF content as bytes, or None if download fails
        """
        default_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        req_headers = headers or default_headers

        try:
            async with aiohttp.ClientSession(headers=req_headers) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=self.timeout)) as resp:
                    if resp.status == 200:
                        content_type = resp.content_type or ''
                        # Accept PDF or generic binary content types
                        if 'pdf' in content_type.lower() or 'octet-stream' in content_type.lower():
                            return await resp.read()
                        # Some servers return wrong content-type; check file extension
                        if url.lower().endswith('.pdf'):
                            return await resp.read()
                    logger.warning(f"Failed to download PDF from {url}: HTTP {resp.status}")
                    return None
        except asyncio.TimeoutError:
            logger.warning(f"Download timeout for PDF from {url}")
            return None
        except Exception as e:
            logger.warning(f"Failed to download PDF from {url}: {e}")
            return None

    def extract_text(self, pdf_bytes: bytes, max_pages: Optional[int] = None) -> Optional[str]:
        """
        Extract text from PDF bytes.

        Uses pymupdf4llm.to_markdown() if available (produces clean Markdown with
        headers, tables, and structure). Falls back to raw PyMuPDF text extraction.

        Args:
            pdf_bytes: PDF content as bytes
            max_pages: Maximum pages to extract (None = all)

        Returns:
            Extracted text (Markdown if pymupdf4llm available), or None if extraction fails
        """
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")

            if self._use_4llm:
                # pymupdf4llm: structured Markdown output
                if max_pages:
                    pages = list(range(min(max_pages, len(doc))))
                else:
                    pages = None
                md_text = pymupdf4llm.to_markdown(doc, pages=pages)
                doc.close()

                if not md_text or len(md_text.strip()) < 100:
                    logger.warning("pymupdf4llm extraction returned insufficient text")
                    return None
                return md_text
            else:
                # Fallback: raw PyMuPDF text extraction
                text_parts = []
                page_count = min(len(doc), max_pages) if max_pages else len(doc)

                for page_num in range(page_count):
                    page = doc[page_num]
                    text = page.get_text()
                    if text:
                        text_parts.append(text)

                doc.close()
                full_text = "\n\n".join(text_parts).strip()

                if not full_text:
                    logger.warning("PDF extraction returned empty text")
                    return None
                return full_text

        except Exception as e:
            logger.warning(f"Failed to extract text from PDF: {e}")
            return None

    def build_article_dict(
        self,
        text: str,
        url: str,
        title: str,
        publisher: str,
        category: str,
        subcategory: str,
        published_date: Optional[datetime] = None
    ) -> Dict:
        """
        Build article dictionary from extracted PDF text.

        Args:
            text: Extracted PDF text (Markdown or plain)
            url: PDF URL (used as unique identifier)
            title: Document title
            publisher: Publisher name (e.g., "SIPRI", "RAND Corporation")
            category: Article category
            subcategory: Article subcategory
            published_date: Publication date (defaults to today)

        Returns:
            Article dict compatible with NLP pipeline
        """
        if published_date is None:
            published_date = datetime.utcnow()

        extraction_method = 'pymupdf4llm' if self._use_4llm else 'pymupdf'

        return {
            'title': title,
            'link': url,
            'published': published_date.isoformat() if isinstance(published_date, datetime) else published_date,
            'summary': text[:300],
            'source': f"pdf:{publisher}",
            'category': category,
            'subcategory': subcategory,
            'full_content': {
                'text': text,
                'extraction_method': extraction_method,
                'is_long_document': True,
            },
            'extraction_success': True,
            'extraction_method': extraction_method,
            'is_long_document': True,
        }

    async def run_url(
        self,
        url: str,
        metadata: Dict,
        headers: Optional[Dict] = None
    ) -> Optional[Dict]:
        """
        Download and process a single PDF from URL.

        Args:
            url: PDF URL
            metadata: Dict with keys: title, publisher, category, subcategory, published (optional)
            headers: Optional HTTP headers for download

        Returns:
            Article dict, or None if processing fails
        """
        logger.info(f"Processing PDF from {url}")

        pdf_bytes = await self.download_pdf(url, headers=headers)
        if not pdf_bytes:
            return None

        text = self.extract_text(pdf_bytes)
        if not text or len(text.strip()) < 50:
            logger.warning(f"Insufficient text extracted from {url}")
            return None

        article = self.build_article_dict(
            text=text,
            url=url,
            title=metadata.get('title', 'Untitled PDF'),
            publisher=metadata.get('publisher', 'Unknown'),
            category=metadata.get('category', 'intelligence'),
            subcategory=metadata.get('subcategory', 'documents'),
            published_date=metadata.get('published')
        )

        logger.info(f"Successfully processed PDF: {article['title'][:50]}...")
        return article

    async def run_batch(self, pdf_configs: List[Dict]) -> List[Dict]:
        """
        Process multiple PDFs concurrently.

        Args:
            pdf_configs: List of dicts with keys: url, title, publisher, category, subcategory

        Returns:
            List of article dicts (failed PDFs skipped)
        """
        logger.info(f"Processing {len(pdf_configs)} PDFs concurrently...")

        tasks = [
            self.run_url(config['url'], config)
            for config in pdf_configs
        ]

        results = await asyncio.gather(*tasks, return_exceptions=False)
        articles = [r for r in results if r is not None]

        logger.info(f"Successfully processed {len(articles)}/{len(pdf_configs)} PDFs")
        return articles

    def run_all(self, config_path: str = "config/pdf_sources.yaml") -> List[Dict]:
        """
        Load config and process all PDF sources.

        Args:
            config_path: Path to pdf_sources.yaml configuration

        Returns:
            List of article dicts
        """
        import yaml

        config_file = Path(config_path)
        if not config_file.exists():
            logger.warning(f"Config file not found: {config_path}")
            return []

        with open(config_file, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        if not config or 'pdf_sources' not in config:
            logger.warning(f"No pdf_sources found in {config_path}")
            return []

        pdf_configs = config['pdf_sources']
        logger.info(f"Loaded {len(pdf_configs)} PDF sources from config")

        articles = asyncio.run(self.run_batch(pdf_configs))
        return articles

    def run_single_file(self, file_path: str, metadata: Dict) -> Optional[Dict]:
        """
        Process a single local PDF file.

        Args:
            file_path: Path to local PDF file
            metadata: Dict with keys: title, publisher, category, subcategory, published (optional)

        Returns:
            Article dict, or None if processing fails
        """
        file_path = Path(file_path)
        if not file_path.exists():
            logger.warning(f"File not found: {file_path}")
            return None

        logger.info(f"Processing local PDF: {file_path}")

        try:
            with open(file_path, 'rb') as f:
                pdf_bytes = f.read()
        except Exception as e:
            logger.warning(f"Failed to read PDF file {file_path}: {e}")
            return None

        text = self.extract_text(pdf_bytes)
        if not text or len(text.strip()) < 50:
            logger.warning(f"Insufficient text extracted from {file_path}")
            return None

        url = f"file://{file_path.absolute()}"

        article = self.build_article_dict(
            text=text,
            url=url,
            title=metadata.get('title', file_path.stem),
            publisher=metadata.get('publisher', 'Local Document'),
            category=metadata.get('category', 'intelligence'),
            subcategory=metadata.get('subcategory', 'documents'),
            published_date=metadata.get('published')
        )

        logger.info(f"Successfully processed local PDF: {article['title'][:50]}...")
        return article
