#!/usr/bin/env python3
"""
Extract entities from articles and populate entities table.

Reads entities from articles.entities JSONB column and creates
dedicated entity records for Intelligence Map visualization.
"""
import sys
import argparse
from pathlib import Path
from collections import Counter

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.storage.database import DatabaseManager
from src.utils.logger import get_logger

logger = get_logger(__name__)


def extract_entities_from_articles(days: int = 0):
    """
    Extract entities from articles and populate entities table.

    Args:
        days: Only process articles from the last N days (0 = all articles)
    """
    db = DatabaseManager()

    logger.info("Extracting entities from articles...")

    # Get articles with entities, optionally filtered by date
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            if days > 0:
                logger.info(f"Filtering articles from last {days} days")
                cur.execute("""
                    SELECT id, entities
                    FROM articles
                    WHERE entities IS NOT NULL
                      AND entities != '{}'::jsonb
                      AND published_date >= NOW() - INTERVAL '%s days'
                """, (days,))
            else:
                cur.execute("""
                    SELECT id, entities
                    FROM articles
                    WHERE entities IS NOT NULL
                      AND entities != '{}'::jsonb
                """)
            
            articles = cur.fetchall()
    
    logger.info(f"Found {len(articles)} articles with entities")
    
    if not articles:
        logger.warning("No articles with entities found!")
        return
    
    # Extract and count entities
    entity_counter = Counter()
    entity_types = {}
    article_entities = {}  # Track which articles mention which entities
    
    for article_id, entities_json in articles:
        if not entities_json:
            continue
        
        # Check if entities are in 'by_type' format
        if 'by_type' in entities_json and isinstance(entities_json['by_type'], dict):
            # Format: {"by_type": {"GPE": ["Taiwan", "USA"], "PERSON": ["Biden"]}}
            for entity_type, entity_list in entities_json['by_type'].items():
                if not isinstance(entity_list, list):
                    continue
                
                for entity_name in entity_list:
                    if not entity_name or not isinstance(entity_name, str):
                        continue
                    
                    # Clean entity name
                    entity_name = entity_name.strip()
                    if not entity_name:
                        continue
                    
                    key = (entity_name, entity_type)
                    entity_counter[key] += 1
                    entity_types[key] = entity_type
                    
                    if key not in article_entities:
                        article_entities[key] = []
                    article_entities[key].append(article_id)
        
        # Also check 'entities' array format as fallback
        elif 'entities' in entities_json and isinstance(entities_json['entities'], list):
            # Format: {"entities": [{"text": "Taiwan", "label": "GPE"}]}
            for entity in entities_json['entities']:
                if not isinstance(entity, dict):
                    continue
                
                entity_name = entity.get('text')
                entity_type = entity.get('label', 'UNKNOWN')
                
                if not entity_name:
                    continue
                
                entity_name = entity_name.strip()
                if not entity_name:
                    continue
                
                key = (entity_name, entity_type)
                entity_counter[key] += 1
                entity_types[key] = entity_type
                
                if key not in article_entities:
                    article_entities[key] = []
                article_entities[key].append(article_id)
    
    logger.info(f"Found {len(entity_counter)} unique entities")
    
    # Save entities to database
    saved_count = 0
    
    for (entity_name, entity_type), mention_count in entity_counter.most_common():
        entity_id = db.save_entity(
            name=entity_name,
            entity_type=entity_type,
            metadata={'mention_count': mention_count}
        )
        
        if entity_id:
            saved_count += 1
            
            # Save entity-article relationships (batch insert)
            article_ids = article_entities[(entity_name, entity_type)]
            with db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.executemany("""
                        INSERT INTO entity_mentions (entity_id, article_id)
                        VALUES (%s, %s)
                        ON CONFLICT (entity_id, article_id) DO NOTHING
                    """, [(entity_id, aid) for aid in article_ids])
    
    logger.info(f"✓ Saved {saved_count} entities to database")
    
    # Print statistics by type
    logger.info("\nEntity Statistics by Type:")
    type_counts = Counter()
    for (_, entity_type), count in entity_counter.items():
        type_counts[entity_type] += count
    
    for entity_type, count in type_counts.most_common():
        logger.info(f"  {entity_type}: {count}")
    
    # Print top entities
    logger.info("\nTop 10 Most Mentioned Entities:")
    for (entity_name, entity_type), count in entity_counter.most_common(10):
        logger.info(f"  {entity_name} ({entity_type}): {count} mentions")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract entities from articles")
    parser.add_argument('--days', type=int, default=0,
                        help='Only process articles from last N days (0 = all)')
    args = parser.parse_args()
    extract_entities_from_articles(days=args.days)
