"""
Main ingestion pipeline that orchestrates feed parsing and content extraction.
"""

import json
import hashlib
import asyncio
import os
import re
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional

from .feed_parser import FeedParser
from .content_extractor import ContentExtractor
from ..utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Off-topic keyword blocklist
# Articles whose title matches any of these patterns are discarded early
# to avoid wasting bandwidth and processing on irrelevant content.
# Patterns are case-insensitive and match whole words (word boundaries).
# ---------------------------------------------------------------------------
_BLOCKLIST_PATTERNS: List[re.Pattern] = [
    re.compile(p, re.IGNORECASE) for p in [
        # --- Sports ---
        r'\b(?:NBA|NFL|NHL|MLB|MLS|UFC|ATP|WTA|FIFA|UEFA|ICC)\b',
        r'\b(?:Premier League|La Liga|Serie A|Bundesliga|Ligue 1|Champions League)\b',
        r'\b(?:Super Bowl|World Series|Grand Slam|Grand Prix|MotoGP|Formula [12])\b',
        r'\b(?:Australian Open|Roland Garros|Wimbledon|US Open Tennis)\b',
        r'\b(?:T20|ODI|Test cricket|IPL|Ashes)\b',
        r'\b(?:Lakers|Warriors|Celtics|Yankees|Cowboys|Patriots)\b',
        r'\b(?:Real Madrid|Barcelona|Man(?:chester)? (?:United|City)|Arsenal|Liverpool|Chelsea|Tottenham|Juventus|PSG|Bayern)\b',
        r'\b(?:Ronaldo|Messi|Guardiola|Mourinho|Klopp)\b',
        # --- Entertainment ---
        r'\b(?:Grammy|Oscar|Emmy|Golden Globe|BAFTA|Cannes Film)\b',
        r'\b(?:Netflix|Spotify|Disney\+|Hulu|HBO Max|streaming wars)\b',
        r'\b(?:box office|blockbuster|sequel|franchise|Marvel|DC Comics)\b',
        r'\b(?:K-pop|BTS|Taylor Swift|Beyonc[eé]|Drake)\b',
        # --- Lifestyle / Tabloid ---
        r'\b(?:celebrity|gossip|red carpet|fashion week|Met Gala)\b',
        r'\b(?:Kardashian|reality TV|Love Island)\b',
        r'\b(?:Fontana di Trevi|tourist ticket|travel guide)\b',
    ]
]


def _is_off_topic(title: str) -> bool:
    """Check if an article title matches any off-topic blocklist pattern."""
    for pattern in _BLOCKLIST_PATTERNS:
        if pattern.search(title):
            return True
    return False


class IngestionPipeline:
    """Main pipeline for news data ingestion."""

    def __init__(
        self,
        config_path: str = "config/feeds.yaml",
        output_dir: str = "data",
        extract_full_content: bool = True
    ):
        """
        Initialize the ingestion pipeline.

        Args:
            config_path: Path to feeds configuration
            output_dir: Directory to save extracted data
            extract_full_content: Whether to extract full article content
        """
        self.feed_parser = FeedParser(config_path)
        self.content_extractor = ContentExtractor() if extract_full_content else None
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)

        logger.info("Ingestion pipeline initialized")

    def deduplicate_by_quick_hash(self, articles: List[Dict]) -> List[Dict]:
        """
        Quick deduplication based on hash(link + title).

        Uses MD5 hash of link + first 100 chars of title to detect duplicates.
        This is Phase 1 deduplication - in-memory, very fast.

        Args:
            articles: List of article dictionaries

        Returns:
            List of unique articles (duplicates removed)
        """
        if not articles:
            return []

        seen_hashes = set()
        unique = []
        skipped = 0

        for article in articles:
            # Create hash from link + title (first 100 chars)
            link = article.get('link', '')
            title = article.get('title', '')[:100]
            hash_key = f"{link}|{title}"
            quick_hash = hashlib.md5(hash_key.encode('utf-8')).hexdigest()

            if quick_hash not in seen_hashes:
                seen_hashes.add(quick_hash)
                unique.append(article)
            else:
                skipped += 1
                logger.debug(
                    f"Skipped duplicate (hash): {article.get('title', 'N/A')[:50]}... "
                    f"from {article.get('source', 'unknown')}"
                )

        if skipped > 0:
            logger.info(
                f"✓ Quick hash dedup: {len(articles)} → {len(unique)} "
                f"({skipped} duplicates removed, {(skipped/len(articles)*100):.1f}%)"
            )
        else:
            logger.info(f"✓ Quick hash dedup: No duplicates found ({len(articles)} unique)")

        return unique

    async def _run_async(
        self,
        category: Optional[str] = None,
        extract_content: bool = True,
        max_age_days: int = 1
    ) -> List[Dict]:
        """
        Async core of the ingestion pipeline.

        Calls async methods directly to avoid nested asyncio.run() calls.
        A single event loop governs both feed parsing and content extraction.
        """
        # Step 1: Parse RSS feeds concurrently
        logger.info("\n[STEP 1] Parsing RSS feeds concurrently...")
        articles = await self.feed_parser._parse_all_feeds_async(category=category)

        if not articles:
            logger.warning("No articles found from RSS feeds")
            return []

        logger.info(f"Parsed {len(articles)} articles from RSS feeds")

        # Step 1.5: Quick hash deduplication (sync, pure computation)
        logger.info("\n[STEP 1.5] Deduplicating articles (quick hash)...")
        articles = self.deduplicate_by_quick_hash(articles)

        if not articles:
            logger.warning("No articles remaining after deduplication")
            return []

        # Step 1.6: Keyword blocklist filter (before content extraction to save bandwidth)
        logger.info("\n[STEP 1.6] Filtering off-topic articles (keyword blocklist)...")
        pre_filter_count = len(articles)
        blocked = []
        filtered = []
        for a in articles:
            title = a.get('title', '')
            if _is_off_topic(title):
                blocked.append(a)
            else:
                filtered.append(a)
        if blocked:
            for b in blocked:
                logger.debug(f"Blocked (off-topic): {b.get('title', 'N/A')[:60]}... [{b.get('source', '?')}]")
            logger.info(
                f"✓ Keyword blocklist: {pre_filter_count} → {len(filtered)} "
                f"({len(blocked)} off-topic removed)"
            )
        else:
            logger.info(f"✓ Keyword blocklist: No off-topic articles found ({pre_filter_count} kept)")
        articles = filtered

        # Filter articles by age (sync, pure computation)
        if max_age_days > 0:
            cutoff_date = datetime.now() - timedelta(days=max_age_days)
            original_count = len(articles)
            articles = [
                a for a in articles
                if a.get('published') and a['published'] >= cutoff_date
            ]
            filtered_count = original_count - len(articles)
            if filtered_count > 0:
                logger.info(f"Filtered out {filtered_count} articles older than {max_age_days} day(s)")
            logger.info(f"{len(articles)} recent articles remaining")

        # Step 2: Extract full content concurrently
        if extract_content and self.content_extractor:
            logger.info("\n[STEP 2] Extracting full article content concurrently...")
            articles = await self.content_extractor._extract_batch_async(articles)
            success_count = sum(1 for a in articles if a.get('extraction_success'))
            logger.info(f"Extracted full content for {success_count}/{len(articles)} articles")
        else:
            logger.info("\n[STEP 2] Skipping full content extraction")

        return articles

    def run(
        self,
        category: Optional[str] = None,
        save_output: bool = True,
        extract_content: bool = True,
        max_age_days: int = 1
    ) -> List[Dict]:
        """
        Run the complete ingestion pipeline.

        Uses a single asyncio.run() to orchestrate all async I/O
        (feed fetching + content extraction) under one event loop.

        Args:
            category: Optional category filter
            save_output: Whether to save output to file
            extract_content: Whether to extract full content from URLs
            max_age_days: Maximum age of articles in days (default: 1)

        Returns:
            List of processed articles
        """
        logger.info("=" * 80)
        logger.info("Starting news ingestion pipeline (async)")
        logger.info("=" * 80)

        articles = asyncio.run(
            self._run_async(category, extract_content, max_age_days)
        )

        # Step 3: Save output (sync I/O, outside async loop)
        if save_output and articles:
            logger.info("\n[STEP 3] Saving results...")
            output_file = self._save_output(articles, category)
            logger.info(f"Results saved to: {output_file}")

            # Write to pipeline manifest if running inside orchestrator
            self._write_manifest(output_file, len(articles))

            # Write to pipeline manifest if running inside orchestrator
            self._write_manifest(output_file, len(articles))

        logger.info("\n" + "=" * 80)
        logger.info("Pipeline execution completed successfully")
        logger.info("=" * 80)

        return articles

    def _save_output(self, articles: List[Dict], category: Optional[str] = None) -> Path:
        """
        Save articles to JSON file.

        Args:
            articles: List of article dictionaries
            category: Optional category name for filename

        Returns:
            Path to output file
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        category_suffix = f"_{category}" if category else ""
        filename = f"articles{category_suffix}_{timestamp}.json"
        output_file = self.output_dir / filename

        # Convert datetime objects to strings for JSON serialization
        serializable_articles = []
        for article in articles:
            article_copy = article.copy()
            for key, value in article_copy.items():
                if isinstance(value, datetime):
                    article_copy[key] = value.isoformat()
            serializable_articles.append(article_copy)

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(serializable_articles, f, indent=2, ensure_ascii=False)

        return output_file

    def _write_manifest(self, output_file: Path, article_count: int):
        """Write ingestion output to pipeline manifest if PIPELINE_MANIFEST_PATH is set."""
        manifest_path = os.environ.get("PIPELINE_MANIFEST_PATH")
        if not manifest_path:
            return

        try:
            from scripts.pipeline_manifest import write_step
            write_step("ingestion", {
                "output_file": str(output_file),
                "article_count": article_count,
            })
            logger.info(f"Manifest updated: ingestion → {output_file.name}")
        except Exception as e:
            logger.warning(f"Failed to write manifest (non-blocking): {e}")

    def get_summary(self, articles: List[Dict]) -> Dict:
        """
        Get a summary of the ingested articles.

        Args:
            articles: List of article dictionaries

        Returns:
            Summary statistics dictionary
        """
        total = len(articles)
        by_category = {}
        by_source = {}
        extraction_success = 0

        for article in articles:
            # Category counts
            category = article.get('category', 'unknown')
            by_category[category] = by_category.get(category, 0) + 1

            # Source counts
            source = article.get('source', 'unknown')
            by_source[source] = by_source.get(source, 0) + 1

            # Extraction success
            if article.get('extraction_success'):
                extraction_success += 1

        return {
            'total_articles': total,
            'extraction_success_rate': f"{extraction_success}/{total}" if total > 0 else "0/0",
            'by_category': by_category,
            'by_source': by_source,
            'top_sources': sorted(by_source.items(), key=lambda x: x[1], reverse=True)[:5]
        }


if __name__ == "__main__":
    # Run the pipeline as a test
    pipeline = IngestionPipeline()

    # Run for all feeds (you can also filter by category)
    articles = pipeline.run(
        category=None,  # Use None for all categories, or specify: 'intelligence', 'tech_economy', etc.
        save_output=True,
        extract_content=True,  # Extract full article content (required for NLP analysis)
        max_age_days=1  # Only articles from last 24 hours
    )

    # Print summary
    summary = pipeline.get_summary(articles)
    print("\n" + "=" * 80)
    print("INGESTION SUMMARY")
    print("=" * 80)
    print(f"\nTotal articles: {summary['total_articles']}")
    print(f"Extraction success: {summary['extraction_success_rate']}")
    print(f"\nArticles by category:")
    for category, count in summary['by_category'].items():
        print(f"  {category}: {count}")
    print(f"\nTop 5 sources:")
    for source, count in summary['top_sources']:
        print(f"  {source}: {count}")
