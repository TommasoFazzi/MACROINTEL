#!/usr/bin/env python3
"""
Load NLP-processed articles into PostgreSQL database with pgvector.

Usage:
    python scripts/load_to_database.py                    # Load latest NLP file
    python scripts/load_to_database.py <file.json>        # Load specific file
    python scripts/load_to_database.py --init-only         # Only initialize schema
"""

import sys
import json
import glob
import os
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.storage.database import DatabaseManager
from src.utils.logger import get_logger
from scripts.pipeline_manifest import get_step_output, write_step, get_manifest_path

logger = get_logger(__name__)


def find_latest_nlp_file() -> Path:
    """Find the most recent NLP-processed JSON file."""
    data_dir = Path("data")
    nlp_files = list(data_dir.glob("articles_nlp_*.json"))

    if not nlp_files:
        raise FileNotFoundError("No NLP-processed files found in data/ directory")

    latest = max(nlp_files, key=lambda p: p.stat().st_mtime)
    return latest


def load_json_file(file_path: Path) -> list:
    """Load and validate JSON file."""
    logger.info(f"Loading file: {file_path}")

    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("JSON file must contain a list of articles")

    logger.info(f"✓ Loaded {len(data)} articles from {file_path.name}")
    return data


def validate_articles(articles: list) -> dict:
    """Validate articles have required NLP data."""
    stats = {
        'total': len(articles),
        'with_nlp': 0,
        'without_nlp': 0,
        'with_chunks': 0
    }

    for article in articles:
        if article.get('nlp_processing', {}).get('success', False):
            stats['with_nlp'] += 1
            if article.get('nlp_data', {}).get('chunks'):
                stats['with_chunks'] += 1
        else:
            stats['without_nlp'] += 1

    logger.info(f"Validation results:")
    logger.info(f"  Total articles: {stats['total']}")
    logger.info(f"  With NLP data: {stats['with_nlp']}")
    logger.info(f"  With chunks: {stats['with_chunks']}")
    logger.info(f"  Without NLP data: {stats['without_nlp']}")

    return stats


def main():
    """Main execution function."""
    logger.info("=" * 80)
    logger.info("DATABASE LOADING SCRIPT")
    logger.info("=" * 80)

    # Parse arguments
    init_only = '--init-only' in sys.argv

    # Initialize database manager
    logger.info("\n[STEP 1] Initializing database connection...")
    try:
        db = DatabaseManager()
        logger.info("✓ Database connection established")
    except Exception as e:
        logger.error(f"Failed to connect to database: {e}")
        logger.error("Make sure PostgreSQL is running and DATABASE_URL is set in .env")
        return 1

    # Initialize schema
    logger.info("\n[STEP 2] Initializing database schema...")
    try:
        db.init_db()
        logger.info("✓ Database schema initialized")
    except Exception as e:
        logger.error(f"Failed to initialize schema: {e}")
        return 1

    if init_only:
        logger.info("\n✓ Schema initialization complete (--init-only mode)")
        return 0

    # Find and load data file
    logger.info("\n[STEP 3] Loading NLP-processed articles...")

    # Determine which file to load — prefer manifest, fall back to CLI arg, then mtime glob
    if len(sys.argv) > 1 and not sys.argv[1].startswith('--'):
        file_path = Path(sys.argv[1])
        if not file_path.exists():
            logger.error(f"File not found: {file_path}")
            return 1
    elif get_manifest_path() is not None:
        manifest_output = get_step_output("nlp_processing")
        if manifest_output:
            file_path = Path(manifest_output)
            logger.info(f"Using manifest input: {file_path.name}")
            if not file_path.exists():
                logger.error(f"Manifest points to missing file: {file_path}")
                return 1
        else:
            logger.warning("Manifest exists but nlp_processing step not found, falling back to mtime")
            try:
                file_path = find_latest_nlp_file()
                logger.info(f"Using latest NLP file: {file_path.name}")
            except FileNotFoundError as e:
                logger.error(str(e))
                return 1
    else:
        try:
            file_path = find_latest_nlp_file()
            logger.info(f"Using latest NLP file: {file_path.name}")
        except FileNotFoundError as e:
            logger.error(str(e))
            return 1

    # Load articles
    try:
        articles = load_json_file(file_path)
    except Exception as e:
        logger.error(f"Failed to load JSON file: {e}")
        return 1

    # Validate articles
    logger.info("\n[STEP 4] Validating articles...")
    validation_stats = validate_articles(articles)

    if validation_stats['with_nlp'] == 0:
        logger.error("No articles with NLP data found. Run NLP processing first.")
        return 1

    # Save to database
    logger.info("\n[STEP 5] Saving articles to database...")
    try:
        save_stats = db.batch_save(articles)
    except Exception as e:
        logger.error(f"Failed to save articles: {e}")
        return 1

    # Get database statistics
    logger.info("\n[STEP 6] Retrieving database statistics...")
    try:
        db_stats = db.get_statistics()
    except Exception as e:
        logger.error(f"Failed to get statistics: {e}")
        db_stats = {}

    # Print final summary
    logger.info("\n" + "=" * 80)
    logger.info("LOADING COMPLETE")
    logger.info("=" * 80)
    logger.info(f"\nFile processed: {file_path.name}")
    logger.info(f"\nSave Statistics:")
    logger.info(f"  Articles saved: {save_stats.get('saved', 0)}")
    logger.info(f"  Articles skipped (duplicates): {save_stats.get('skipped', 0)}")
    logger.info(f"  Articles with errors: {save_stats.get('errors', 0)}")
    logger.info(f"  Total chunks inserted: {save_stats.get('total_chunks', 0)}")

    if db_stats:
        logger.info(f"\nDatabase Statistics:")
        logger.info(f"  Total articles in DB: {db_stats.get('total_articles', 0)}")
        logger.info(f"  Total chunks in DB: {db_stats.get('total_chunks', 0)}")
        logger.info(f"  Recent articles (7 days): {db_stats.get('recent_articles', 0)}")
        logger.info(f"\n  Articles by category:")
        for category, count in db_stats.get('by_category', {}).items():
            logger.info(f"    {category}: {count}")

    # Close database connection
    db.close()

    # Write result to manifest (if running inside orchestrated pipeline)
    write_step("load_to_database", {
        "input_file": str(file_path),
        "articles_saved": save_stats.get("saved", 0),
        "articles_skipped": save_stats.get("skipped", 0),
        "total_chunks": save_stats.get("total_chunks", 0),
    })

    logger.info("\n✓ All done!")
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        logger.info("\n\nInterrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        sys.exit(1)
