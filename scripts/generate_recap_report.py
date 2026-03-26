#!/usr/bin/env python3
"""
Generate recap meta-analysis from weekly reports.

This script analyzes the evolution of trends across multiple weeks by reading
weekly intelligence reports, creating a higher-level strategic analysis.

Usage:
    python scripts/generate_recap_report.py --start 2024-11-25 --end 2024-12-31
    python scripts/generate_recap_report.py --start 2024-11-25 --end 2024-12-31 --no-save-db
"""

import sys
import os
import argparse
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.storage.database import DatabaseManager
from src.llm.report_generator import ReportGenerator
from src.utils.logger import get_logger

logger = get_logger(__name__)


def get_weekly_reports(db: DatabaseManager, start_date: datetime.date, end_date: datetime.date) -> List[Dict]:
    """
    Retrieve weekly reports from the specified date range.

    Weekly reports are identified by having 'reports_count' in metadata.

    Args:
        db: Database manager instance
        start_date: Start date (inclusive)
        end_date: End date (inclusive)

    Returns:
        List of weekly report dictionaries ordered by date
    """
    logger.info(f"Fetching weekly reports from {start_date} to {end_date}...")

    reports = db.get_weekly_reports_by_date_range(
        start_date=start_date,
        end_date=end_date
    )

    if len(reports) < 2:
        logger.warning(
            f"Only {len(reports)} weekly reports found. "
            f"Minimum 2 recommended for meaningful recap analysis."
        )

    logger.info(f"✓ Retrieved {len(reports)} weekly reports for recap analysis")
    return reports


def aggregate_recap_metadata(reports: List[Dict]) -> Dict[str, Any]:
    """
    Aggregate metadata across weekly reports.

    Args:
        reports: List of weekly report dictionaries

    Returns:
        Aggregated metadata dictionary
    """
    total_daily_reports = 0
    total_articles = 0
    total_unique_sources = 0
    all_weekly_sources = []

    for report in reports:
        metadata = report.get('metadata', {})
        sources = report.get('sources', {})

        # Count from each weekly report
        total_daily_reports += metadata.get('reports_count', 0)
        total_articles += metadata.get('total_articles_analyzed', 0)
        total_unique_sources += metadata.get('unique_sources_count', 0)

        # Collect source info from weekly reports
        all_weekly_sources.extend(sources.get('daily_reports', []))

    return {
        'weekly_reports_count': len(reports),
        'total_daily_reports_covered': total_daily_reports,
        'total_articles_analyzed': total_articles,
        'total_unique_sources': total_unique_sources,
        'date_range': {
            'start': reports[0]['report_date'].isoformat() if reports else None,
            'end': reports[-1]['report_date'].isoformat() if reports else None
        },
        'weekly_reports_covered': [
            {
                'id': r['id'],
                'date': r['report_date'].isoformat(),
                'status': r['status']
            } for r in reports
        ]
    }


def generate_recap_prompt(reports: List[Dict], aggregated_meta: Dict) -> str:
    """
    Generate prompt for recap meta-analysis.

    Args:
        reports: List of weekly reports (ordered by date)
        aggregated_meta: Aggregated metadata

    Returns:
        Complete prompt string for LLM
    """

    system_prompt = """You are a CHIEF STRATEGIC ANALYST at a global think tank.

LANGUAGE REQUIREMENT: Write the entire report in ENGLISH. No exceptions.

TASK: Write the "PERIOD RECAP REPORT" — a strategic meta-analysis based on the weekly briefings of the period.

IMPORTANT: Do not summarize individual weekly reports. Identify META-PATTERNS and long-term trends.

REPORT STRUCTURE:

1. 🎯 EXECUTIVE SUMMARY
   - What was the DOMINANT NARRATIVE of the period?
   - What significant GEOPOLITICAL SHIFTS occurred?
   - What is the "big picture" emerging from the full set of weekly reports?

2. 📊 TREND ANALYSIS
   - CONSOLIDATED TRENDS: Patterns that strengthened over time
   - REVERSED TRENDS: Dynamics that changed direction
   - EMERGING TRENDS: New patterns beginning to form
   - DISCONTINUITIES: Events that broke continuity

3. 🌍 GEOGRAPHIC HOTSPOTS
   - Which geographic areas remained critical throughout the period?
   - How did the situation in each key area evolve?
   - New areas of tension that emerged

4. ⚔️ ACTORS ANALYSIS
   - WINNERS: Who gained influence/power during the period?
   - LOSERS: Who lost ground?
   - RISING POWERS: Emerging actors to monitor
   - DECLINING POWERS: Actors in a decline phase

5. 🔮 STRATEGIC OUTLOOK
   - Probable scenarios for the next period
   - Key watch items (what to monitor closely)
   - Identified systemic risks
   - Strategic opportunities

STYLE:
- Strategic, not tactical
- Focus on TEMPORAL EVOLUTION of trends
- Highlight CONNECTIONS between apparently unrelated events
- Use temporal references (e.g. "from the second week...")
- Quantify when possible (e.g. "3 consecutive weeks of...")
"""

    # Build context from weekly reports
    context_parts = []
    for i, report in enumerate(reports, 1):
        date_obj = report['report_date']

        # Get week number in period
        week_label = f"WEEK {i}"

        # Prefer final_content (human-reviewed) over draft_content
        content = report.get('final_content') or report.get('draft_content', '')
        status = report.get('status', 'draft')
        status_indicator = "✓" if status == 'approved' else f"({status})"

        # Extract date range from metadata if available
        meta = report.get('metadata', {})
        date_range = meta.get('date_range', {})
        period_str = ""
        if date_range:
            period_str = f" [{date_range.get('start', '')} → {date_range.get('end', '')}]"

        context_parts.append(
            f"=== {week_label}: {date_obj.strftime('%d %B %Y')}{period_str} {status_indicator} ===\n{content}\n"
        )

    context = "\n".join(context_parts)

    # Metadata summary
    meta_summary = f"""
PERIOD METADATA:
- Overall period: {aggregated_meta['date_range']['start']} → {aggregated_meta['date_range']['end']}
- Weekly reports analyzed: {aggregated_meta['weekly_reports_count']}
- Daily reports covered: {aggregated_meta['total_daily_reports_covered']}
- Total articles processed: {aggregated_meta['total_articles_analyzed']}
"""

    # Build final prompt
    prompt_parts = [
        system_prompt,
        meta_summary,
        "\n" + "=" * 80,
        "WEEKLY INTELLIGENCE BRIEFINGS FOR THE PERIOD:",
        "=" * 80,
        context
    ]

    return "\n".join(prompt_parts)


def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(
        description="Generate recap meta-analysis from weekly reports"
    )
    parser.add_argument(
        '--start',
        type=str,
        required=True,
        help='Start date in YYYY-MM-DD format'
    )
    parser.add_argument(
        '--end',
        type=str,
        required=True,
        help='End date in YYYY-MM-DD format'
    )
    parser.add_argument(
        '--no-save-db',
        action='store_true',
        help='Do not save recap report to database'
    )
    parser.add_argument(
        '--output-dir',
        type=str,
        default='reports',
        help='Output directory for report file (default: reports)'
    )
    parser.add_argument(
        '--model',
        type=str,
        default='gemini-2.5-flash',
        help='Gemini model to use (default: gemini-2.5-flash)'
    )

    args = parser.parse_args()

    # Parse dates
    try:
        start_date = datetime.strptime(args.start, '%Y-%m-%d').date()
        end_date = datetime.strptime(args.end, '%Y-%m-%d').date()
    except ValueError as e:
        logger.error(f"Invalid date format. Use YYYY-MM-DD. Error: {e}")
        return 1

    if start_date > end_date:
        logger.error("Start date must be before or equal to end date")
        return 1

    logger.info("=" * 80)
    logger.info("PERIOD RECAP REPORT GENERATION")
    logger.info("=" * 80)
    logger.info(f"Period: {start_date} → {end_date}")

    # Check for API key
    if not os.getenv('GEMINI_API_KEY'):
        logger.error("GEMINI_API_KEY not found in environment")
        logger.error("Please add it to your .env file")
        return 1

    # Step 1: Connect to database
    logger.info("\n[STEP 1] Connecting to database...")
    try:
        db = DatabaseManager()
        logger.info("✓ Database connection established")
    except Exception as e:
        logger.error(f"Failed to connect to database: {e}")
        return 1

    # Step 2: Retrieve weekly reports
    logger.info(f"\n[STEP 2] Retrieving weekly reports ({start_date} → {end_date})...")
    try:
        reports = get_weekly_reports(db, start_date, end_date)

        if not reports:
            logger.error("No weekly reports found in the specified date range")
            logger.error("Weekly reports are identified by metadata->>'reports_count' IS NOT NULL")
            logger.error("Make sure you have generated weekly reports with generate_weekly_report.py")
            return 1

        logger.info(f"✓ Found {len(reports)} weekly reports")

        # Log which reports were found
        for report in reports:
            status_emoji = "✓" if report['status'] == 'approved' else "○"
            meta = report.get('metadata', {})
            daily_count = meta.get('reports_count', '?')
            logger.info(f"  {status_emoji} {report['report_date']} (covers {daily_count} daily reports)")

    except Exception as e:
        logger.error(f"Failed to retrieve weekly reports: {e}")
        return 1

    # Step 3: Aggregate metadata
    logger.info("\n[STEP 3] Aggregating metadata...")
    try:
        aggregated_meta = aggregate_recap_metadata(reports)
        logger.info(f"✓ Aggregated metadata from {aggregated_meta['weekly_reports_count']} weekly reports")
        logger.info(f"  Total daily reports covered: {aggregated_meta['total_daily_reports_covered']}")
        logger.info(f"  Total articles analyzed: {aggregated_meta['total_articles_analyzed']}")
    except Exception as e:
        logger.error(f"Failed to aggregate metadata: {e}")
        return 1

    # Step 4: Generate recap prompt
    logger.info("\n[STEP 4] Generating recap prompt...")
    try:
        prompt = generate_recap_prompt(reports, aggregated_meta)
        prompt_length = len(prompt)
        estimated_tokens = prompt_length // 4  # Rough estimate
        logger.info(f"✓ Prompt generated ({prompt_length} chars, ~{estimated_tokens} tokens)")
    except Exception as e:
        logger.error(f"Failed to generate prompt: {e}")
        return 1

    # Step 5: Call Gemini for recap analysis
    logger.info(f"\n[STEP 5] Generating period recap with {args.model}...")
    try:
        generator = ReportGenerator(model_name=args.model)

        response = generator.model.generate_content(
            prompt,
            generation_config={'temperature': 0.3}  # Deterministic for analysis
        )

        recap_report_text = response.text
        logger.info(f"✓ Recap analysis generated ({len(recap_report_text)} characters)")
    except Exception as e:
        logger.error(f"Failed to generate recap analysis: {e}")
        return 1

    # Step 6: Save to file
    logger.info(f"\n[STEP 6] Saving report to file...")
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = Path(args.output_dir)
        output_dir.mkdir(exist_ok=True)

        # Include date range in filename
        start_str = start_date.strftime("%Y%m%d")
        end_str = end_date.strftime("%Y%m%d")
        output_file = output_dir / f"RECAP_REPORT_{start_str}_{end_str}_{timestamp}.md"

        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(f"# Period Recap Report\n")
            f.write(f"**Period**: {start_date} to {end_date}\n")
            f.write(f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"**Weekly reports analyzed**: {aggregated_meta['weekly_reports_count']}\n")
            f.write(f"**Daily reports covered**: {aggregated_meta['total_daily_reports_covered']}\n")
            f.write(f"**Model**: {args.model}\n\n")
            f.write("---\n\n")
            f.write(recap_report_text)

        logger.info(f"✓ Report saved to: {output_file}")

    except Exception as e:
        logger.error(f"Failed to save report to file: {e}")
        return 1

    # Step 7: Save to database (optional)
    if not args.no_save_db:
        logger.info("\n[STEP 7] Saving recap report to database...")
        try:
            # Prepare report dictionary for database
            recap_report_dict = {
                'report_text': recap_report_text,
                'report_type': 'recap',  # Mark as recap report
                'metadata': {
                    'model_used': args.model,
                    'period_start': start_date.isoformat(),
                    'period_end': end_date.isoformat(),
                    'weekly_reports_count': aggregated_meta['weekly_reports_count'],
                    'total_daily_reports_covered': aggregated_meta['total_daily_reports_covered'],
                    'total_articles_analyzed': aggregated_meta['total_articles_analyzed'],
                    'date_range': aggregated_meta['date_range']
                },
                'sources': {
                    'weekly_reports': aggregated_meta['weekly_reports_covered']
                }
            }

            report_id = db.save_report(recap_report_dict)

            if report_id:
                logger.info(f"✓ Recap report saved to database (ID: {report_id})")
            else:
                logger.warning("Failed to save recap report to database")

        except Exception as e:
            logger.error(f"Error saving to database: {e}")
            # Don't fail entire script if DB save fails
    else:
        logger.info("\n[STEP 7] Skipping database save (--no-save-db flag)")

    # Step 8: Print report
    logger.info("\n" + "=" * 80)
    logger.info("PERIOD RECAP REPORT")
    logger.info("=" * 80)
    print(f"\n{recap_report_text}\n")
    logger.info("=" * 80)

    # Final summary
    logger.info(f"\n✓ Period recap complete!")
    logger.info(f"  Period: {start_date} → {end_date}")
    logger.info(f"  Weekly reports analyzed: {aggregated_meta['weekly_reports_count']}")
    logger.info(f"  Output: {output_file}")

    db.close()
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
