"""
Content Extractor Module

This module extracts full-text content from article URLs using specialized libraries.
It tries multiple extraction methods to get the best quality content.

Extraction Strategy:
1. PDF auto-detection (direct .pdf URL or landing page with PDF download link)
2. Trafilatura (fast, best for news)
3. Newspaper3k (fallback)
4. Cloudscraper (for anti-bot protected sites like politico.com)
"""

import requests
import random
import asyncio
from typing import Optional, Dict, List
from datetime import datetime
from urllib.parse import urljoin
import trafilatura
from newspaper import Article as NewspaperArticle
from bs4 import BeautifulSoup

try:
    import cloudscraper
    CLOUDSCRAPER_AVAILABLE = True
except ImportError:
    CLOUDSCRAPER_AVAILABLE = False

from .pdf_ingestor import PDFIngestor, PYMUPDF_AVAILABLE
from ..utils.logger import get_logger

logger = get_logger(__name__)

# Pool di User-Agent realistici per evitare blocchi
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# Domini noti per richiedere cloudscraper (anti-bot protection)
PROTECTED_DOMAINS = [
    'politico.com',
]


class ContentExtractor:
    """Extracts full-text content from article URLs."""

    def __init__(self, timeout: int = 10, user_agent: str = None, max_concurrent: int = 10):
        """
        Initialize the ContentExtractor.

        Args:
            timeout: Request timeout in seconds
            user_agent: Custom user agent string
            max_concurrent: Max concurrent extractions for async batch
        """
        self.timeout = timeout
        self.max_concurrent = max_concurrent
        self.user_agent = user_agent or self._get_random_ua()

        # Standard requests session
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': self.user_agent})

        # Cloudscraper session for anti-bot protected sites
        self.cloudscraper_session = None
        if CLOUDSCRAPER_AVAILABLE:
            try:
                self.cloudscraper_session = cloudscraper.create_scraper(
                    browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False}
                )
                logger.debug("Cloudscraper session initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize cloudscraper: {e}")

    def _get_random_ua(self) -> str:
        """Get a random user agent from the pool."""
        return random.choice(USER_AGENTS)

    def _is_protected_domain(self, url: str) -> bool:
        """Check if URL belongs to a known anti-bot protected domain."""
        return any(domain in url for domain in PROTECTED_DOMAINS)

    def extract_with_trafilatura(self, url: str, html: str = None) -> Optional[Dict]:
        """
        Extract content using Trafilatura (best for news articles).

        Args:
            url: Article URL
            html: Optional pre-fetched HTML content

        Returns:
            Dictionary with extracted content or None
        """
        try:
            if html is None:
                downloaded = trafilatura.fetch_url(url)
            else:
                downloaded = html

            if not downloaded:
                return None

            # Extract with metadata
            content = trafilatura.extract(
                downloaded,
                include_comments=False,
                include_tables=True,
                no_fallback=False,
                output_format='json',
                with_metadata=True
            )

            if content:
                import json
                content_dict = json.loads(content)
                return {
                    'title': content_dict.get('title'),
                    'author': content_dict.get('author'),
                    'date': content_dict.get('date'),
                    'text': content_dict.get('text'),
                    'description': content_dict.get('description'),
                    'sitename': content_dict.get('sitename'),
                    'extraction_method': 'trafilatura'
                }

        except Exception as e:
            logger.debug(f"Trafilatura extraction failed for {url}: {e}")

        return None

    def extract_with_newspaper(self, url: str) -> Optional[Dict]:
        """
        Extract content using Newspaper3k (good fallback).

        Args:
            url: Article URL

        Returns:
            Dictionary with extracted content or None
        """
        try:
            article = NewspaperArticle(url)
            article.download()
            article.parse()

            if article.text:
                return {
                    'title': article.title,
                    'author': ', '.join(article.authors) if article.authors else None,
                    'date': article.publish_date.isoformat() if article.publish_date else None,
                    'text': article.text,
                    'description': article.meta_description,
                    'sitename': article.source_url,
                    'top_image': article.top_image,
                    'extraction_method': 'newspaper3k'
                }

        except Exception as e:
            logger.debug(f"Newspaper3k extraction failed for {url}: {e}")

        return None

    def extract_with_cloudscraper(self, url: str) -> Optional[Dict]:
        """
        Extract content using Cloudscraper for anti-bot protected sites.

        This method bypasses Cloudflare and similar bot protection by
        emulating a real browser's TLS fingerprint and challenge responses.

        Args:
            url: Article URL

        Returns:
            Dictionary with extracted content or None
        """
        if not self.cloudscraper_session:
            logger.debug("Cloudscraper not available")
            return None

        try:
            # Fetch with cloudscraper
            response = self.cloudscraper_session.get(
                url,
                timeout=self.timeout + 5  # Extra time for challenge solving
            )

            if response.status_code == 200:
                # Pass fetched HTML to trafilatura for extraction
                content = self.extract_with_trafilatura(url, html=response.text)
                if content:
                    content['extraction_method'] = 'cloudscraper+trafilatura'
                    logger.info(f"Successfully extracted with Cloudscraper: {url}")
                    return content

            logger.debug(f"Cloudscraper got status {response.status_code} for {url}")

        except Exception as e:
            logger.debug(f"Cloudscraper extraction failed for {url}: {e}")

        return None

    def _extract_pdf_content_sync(self, url: str) -> Optional[Dict]:
        """
        Extract content from a PDF URL synchronously via PDFIngestor.

        Args:
            url: URL pointing to a PDF file

        Returns:
            Dictionary with extracted content or None
        """
        try:
            ingestor = PDFIngestor()
            # Use same User-Agent as HTML crawler for consistency
            headers = {'User-Agent': self.user_agent}
            pdf_bytes = asyncio.run(ingestor.download_pdf(url, headers=headers))
            if not pdf_bytes:
                return None

            text = ingestor.extract_text(pdf_bytes)
            if not text or len(text.strip()) < 100:
                logger.warning(f"Insufficient text from PDF: {url}")
                return None

            logger.info(f"Successfully extracted PDF content: {url}")
            return {
                'text': text,
                'extraction_method': 'pymupdf4llm' if ingestor._use_4llm else 'pymupdf',
                'is_long_document': True,
            }
        except Exception as e:
            logger.debug(f"PDF extraction failed for {url}: {e}")
            return None

    def _find_pdf_link(self, html: str, base_url: str) -> Optional[str]:
        """
        Scan HTML for PDF download links (think tank landing pages).

        Many think tanks link to landing pages, not direct PDFs. This method
        searches for <a href="*.pdf"> with relevant link text.

        Args:
            html: HTML content of the landing page
            base_url: Base URL for resolving relative links

        Returns:
            Absolute URL to PDF file, or None
        """
        try:
            soup = BeautifulSoup(html, 'html.parser')

            pdf_keywords = [
                'download', 'full report', 'full text', 'pdf', 'read the report',
                'download report', 'download publication', 'view report',
                'read report', 'full paper', 'download paper',
            ]

            for a_tag in soup.find_all('a', href=True):
                href = a_tag['href'].strip()
                if not href.lower().endswith('.pdf'):
                    continue
                # Found a .pdf link — verify it's not a generic icon/logo link
                link_text = a_tag.get_text(strip=True).lower()
                # Accept if link text has relevant keywords OR is descriptive (>5 chars)
                if any(kw in link_text for kw in pdf_keywords) or len(link_text) > 5:
                    pdf_url = urljoin(base_url, href)
                    logger.info(f"Found PDF link in landing page: {pdf_url}")
                    return pdf_url

            return None
        except Exception as e:
            logger.debug(f"Error scanning for PDF links in {base_url}: {e}")
            return None

    def extract_content(self, url: str, html: str = None) -> Optional[Dict]:
        """
        Extract full-text content from URL using multiple methods.

        Extraction order:
        1. Direct PDF: If URL ends with .pdf, extract via PDFIngestor
        2. For protected domains: Try cloudscraper first
        3. Trafilatura (fast, best for news)
        4. Newspaper3k (fallback)
        5. Cloudscraper (last resort for any failed extraction)
        6. Landing page PDF detection: Scan HTML for PDF download links

        Args:
            url: Article URL
            html: Optional pre-fetched HTML content

        Returns:
            Dictionary with extracted content and metadata
        """
        logger.info(f"Extracting content from: {url}")

        # LEVEL 1: Direct PDF URL detection
        if PYMUPDF_AVAILABLE and url.lower().endswith('.pdf'):
            content = self._extract_pdf_content_sync(url)
            if content and content.get('text'):
                return content

        # For known protected domains, try cloudscraper first
        if self._is_protected_domain(url):
            logger.debug(f"Protected domain detected, trying cloudscraper first: {url}")
            content = self.extract_with_cloudscraper(url)
            if content and content.get('text'):
                return self._try_level2_pdf(url, content)

        # Try Trafilatura first (best for news)
        content = self.extract_with_trafilatura(url, html)
        if content and content.get('text'):
            logger.info(f"Successfully extracted with Trafilatura: {url}")
            return self._try_level2_pdf(url, content)

        # Fallback to Newspaper3k
        content = self.extract_with_newspaper(url)
        if content and content.get('text'):
            logger.info(f"Successfully extracted with Newspaper3k: {url}")
            return self._try_level2_pdf(url, content)

        # Last resort: try cloudscraper for any failed URL (might be anti-bot)
        if not self._is_protected_domain(url):  # Avoid double attempt
            content = self.extract_with_cloudscraper(url)
            if content and content.get('text'):
                return self._try_level2_pdf(url, content)

        logger.warning(f"Failed to extract content from: {url}")
        return None

    def _try_level2_pdf(self, url: str, html_content: Dict) -> Dict:
        """
        LEVEL 2: After successful HTML extraction, check if landing page contains
        a PDF download link (think tank pattern). If found, extract PDF and combine
        with HTML abstract. Returns original html_content if no PDF found.
        """
        if not PYMUPDF_AVAILABLE:
            return html_content
        raw_html = self._fetch_raw_html(url)
        if raw_html:
            pdf_url = self._find_pdf_link(raw_html, url)
            if pdf_url:
                pdf_content = self._extract_pdf_content_sync(pdf_url)
                if pdf_content and pdf_content.get('text'):
                    html_text = html_content.get('text', '')
                    pdf_content['text'] = html_text + '\n\n---\n\n' + pdf_content['text']
                    return pdf_content
        return html_content

    def _fetch_raw_html(self, url: str) -> Optional[str]:
        """Fetch raw HTML for PDF link scanning. Lightweight GET with timeout."""
        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code == 200:
                return resp.text
        except Exception:
            pass
        return None

    # =========================================================================
    # ASYNC METHODS — Estrazione parallela con semaforo
    # =========================================================================

    async def _extract_content_async(
        self,
        semaphore: asyncio.Semaphore,
        article: dict,
        idx: int,
        total: int,
    ) -> dict:
        """Extract content for a single article asynchronously."""
        url = article.get('link')
        if not url:
            logger.warning(f"Article {idx}/{total} has no URL, skipping")
            return article

        try:
            async with semaphore:
                # Delegate to sync extract_content in a thread
                full_content = await asyncio.to_thread(self.extract_content, url)

            article['full_content'] = full_content
            article['extraction_success'] = full_content is not None
            article['extraction_timestamp'] = datetime.now()

            if full_content:
                logger.info(f"[{idx}/{total}] Extracted: {article.get('title', 'N/A')[:50]}...")
            else:
                logger.warning(f"[{idx}/{total}] Failed: {article.get('title', 'N/A')[:50]}...")

            return article

        except Exception as e:
            logger.error(f"Error extracting article {idx}/{total}: {e}")
            article['full_content'] = None
            article['extraction_success'] = False
            article['extraction_error'] = str(e)
            return article

    async def _extract_batch_async(self, articles: list) -> list:
        """Extract full content for a batch of articles concurrently."""
        total = len(articles)
        if total == 0:
            return []

        logger.info(f"Extracting full content for {total} articles (max_concurrent={self.max_concurrent})...")

        semaphore = asyncio.Semaphore(self.max_concurrent)

        tasks = [
            self._extract_content_async(semaphore, article, idx, total)
            for idx, article in enumerate(articles, 1)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Handle any unhandled exceptions from gather
        final_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Article extraction raised exception: {result}")
                article = articles[i].copy()
                article['full_content'] = None
                article['extraction_success'] = False
                article['extraction_error'] = str(result)
                final_results.append(article)
            else:
                final_results.append(result)

        success_count = sum(1 for a in final_results if a.get('extraction_success'))
        logger.info(f"Extraction complete: {success_count}/{total} successful")

        return final_results

    # =========================================================================
    # SYNC METHODS — Per uso standalone e retrocompatibilità
    # =========================================================================

    def extract_batch(self, articles: list) -> list:
        """
        Extract full content for a batch of articles.

        Uses async concurrency internally for parallel extraction.
        For use within an existing async context (e.g. pipeline._run_async),
        call _extract_batch_async() directly instead.

        Args:
            articles: List of article dictionaries with 'link' key

        Returns:
            List of articles with 'full_content' field added
        """
        if not articles:
            return []
        return asyncio.run(self._extract_batch_async(articles))


if __name__ == "__main__":
    # Test the extractor
    extractor = ContentExtractor()

    # Test with a sample URL
    test_url = "https://www.bbc.com/news"
    content = extractor.extract_content(test_url)

    if content:
        print("\nExtracted content:")
        for key, value in content.items():
            if key == 'text':
                print(f"  {key}: {value[:200]}...")
            else:
                print(f"  {key}: {value}")
    else:
        print("Failed to extract content")
