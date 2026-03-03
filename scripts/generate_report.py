#!/usr/bin/env python3
"""
Generate daily intelligence report using LLM with RAG.

Usage:
    python scripts/generate_report.py                    # Generate with default settings
    python scripts/generate_report.py --days 3           # Include last 3 days
    python scripts/generate_report.py --no-save          # Don't save to file
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm.report_generator import ReportGenerator
from src.utils.logger import get_logger

logger = get_logger(__name__)


def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(description="Generate intelligence report with RAG")
    parser.add_argument(
        '--days',
        type=int,
        default=1,
        help='Number of days to look back for articles (default: 1)'
    )
    parser.add_argument(
        '--from-time',
        type=str,
        default=None,
        help='Start time for articles (ISO format: YYYY-MM-DDTHH:MM, e.g., 2024-01-15T09:00)'
    )
    parser.add_argument(
        '--to-time',
        type=str,
        default=None,
        help='End time for articles (ISO format: YYYY-MM-DDTHH:MM, e.g., 2024-01-16T09:00)'
    )
    parser.add_argument(
        '--no-save',
        action='store_true',
        help='Do not save report to file'
    )
    parser.add_argument(
        '--output-dir',
        type=str,
        default='reports',
        help='Output directory for reports (default: reports)'
    )
    parser.add_argument(
        '--model',
        type=str,
        default='gemini-2.5-flash',
        help='Gemini model to use (default: gemini-2.5-flash)'
    )
    parser.add_argument(
        '--top-articles',
        type=int,
        default=60,
        help='Maximum number of top relevant articles to include (default: 60)'
    )
    parser.add_argument(
        '--min-similarity',
        type=float,
        default=0.30,
        help='Minimum similarity threshold for article relevance (default: 0.30)'
    )
    parser.add_argument(
        '--min-articles',
        type=int,
        default=10,
        help='Minimum number of articles to use even if below threshold (default: 10)'
    )
    parser.add_argument(
        '--analyze-articles',
        action='store_true',
        help='Generate structured analysis (Trade Signals, Impact Score) for each article (Sprint 2.2)'
    )
    parser.add_argument(
        '--macro-first',
        action='store_true',
        help='Enable serialized pipeline: Macro Report -> Condensed Context -> Trade Signals (recommended)'
    )
    parser.add_argument(
        '--skip-article-signals',
        action='store_true',
        help='With --macro-first, only extract report-level signals (faster, lower cost)'
    )
    parser.add_argument(
        '--sequential',
        action='store_true',
        help='Use sequential 5-call architecture (Cyber→Tech→Geo→Econ→Synthesis) instead of monolithic LLM call'
    )

    args = parser.parse_args()

    # Parse and validate time window arguments
    from_time = None
    to_time = None

    if args.from_time:
        try:
            from_time = datetime.fromisoformat(args.from_time)
        except ValueError:
            logger.error(f"Invalid --from-time format: {args.from_time}")
            logger.error("Expected ISO format: YYYY-MM-DDTHH:MM (e.g., 2024-01-15T09:00)")
            return 1

    if args.to_time:
        try:
            to_time = datetime.fromisoformat(args.to_time)
        except ValueError:
            logger.error(f"Invalid --to-time format: {args.to_time}")
            logger.error("Expected ISO format: YYYY-MM-DDTHH:MM (e.g., 2024-01-16T09:00)")
            return 1

    if from_time and to_time and from_time >= to_time:
        logger.error(f"--from-time ({args.from_time}) must be before --to-time ({args.to_time})")
        return 1

    logger.info("=" * 80)
    logger.info("INTELLIGENCE REPORT GENERATION")
    logger.info("=" * 80)

    # Check for API key
    import os
    if not os.getenv('GEMINI_API_KEY'):
        logger.error("GEMINI_API_KEY not found in environment")
        logger.error("Please add it to your .env file or export it:")
        logger.error("  export GEMINI_API_KEY='your-api-key-here'")
        return 1

    # Initialize report generator
    try:
        logger.info(f"\n[STEP 1] Initializing report generator with {args.model}...")
        generator = ReportGenerator(model_name=args.model)
        logger.info("✓ Report generator initialized")
    except Exception as e:
        logger.error(f"Failed to initialize report generator: {e}")
        return 1

    # Define focus areas
    focus_areas = [
    # Cybersecurity: Aggiungiamo l'intento malevolo e l'infrastruttura
    "cybersecurity threats, state-sponsored cyber attacks, ransomware campaigns, and critical infrastructure vulnerabilities",
    
    # Tech: Aggiungiamo la dimensione strategica (chip/supply chain)
    "breakthroughs in artificial intelligence, semiconductor supply chain shifts, and dual-use technology regulations",
    
    # Geopolitica (Generale): Rendiamola più attiva
    "escalation of military conflicts, diplomatic ruptures, and changing alliances in NATO, Russia, China, and Middle East",
    
    # NUOVO: Geografia dei Conflitti (Specifico per la tua richiesta)
    "territorial control changes, strategic military movements, maritime security in choke points, and border disputes",
    
    # Economia: Colleghiamola alla geopolitica
    "global economic impact of sanctions, energy market volatility, and trade protectionism policies"
]

    logger.info(f"\n[STEP 2] Focus areas:")
    for area in focus_areas:
        logger.info(f"  - {area}")

    # Check if sequential 5-call architecture is enabled
    if args.sequential:
        logger.info("\n" + "=" * 80)
        logger.info("[SEQUENTIAL MODE] 5-call architecture: Cyber→Tech→Geo→Econ→Synthesis")
        if from_time or to_time:
            logger.info(f"Time window: {from_time or 'N/A'} → {to_time or 'N/A'}")
        else:
            logger.info(f"Time window: last {args.days} day(s)")
        logger.info("=" * 80)

        # Fetch macro context (same pattern as generate_report())
        import os as _os
        from datetime import date as _date
        macro_dashboard_text = ""
        macro_context_text = ""

        try:
            from src.integrations.openbb_service import OpenBBMarketService
            openbb_svc = OpenBBMarketService(generator.db)
            today = _date.today()
            openbb_svc.ensure_daily_macro_data(today)
            macro_context_text = openbb_svc.get_macro_context_text(today)
            if macro_context_text:
                macro_result = generator._generate_macro_analysis(macro_context_text, today)
                if macro_result.get('success') or macro_result.get('result'):
                    macro_dashboard_text = generator._format_macro_dashboard(
                        macro_result.get('result', {}), today
                    )
                    logger.info(f"✓ Macro dashboard ready ({len(macro_dashboard_text)} chars)")
        except Exception as e:
            logger.warning(f"Macro context unavailable (non-blocking): {e}")

        try:
            report = generator.generate_report_sequential(
                focus_areas=focus_areas,  # ← FIX: passa le focus_areas definite sopra
                days=args.days,
                from_time=from_time,
                to_time=to_time,
                top_articles=args.top_articles,
                min_similarity=args.min_similarity,
                min_fallback=args.min_articles,
                macro_dashboard_text=macro_dashboard_text,
                macro_context_text=macro_context_text,
            )
        except Exception as e:
            logger.error(f"Sequential pipeline failed: {e}", exc_info=True)
            return 1

        if not report['success']:
            logger.error(f"Sequential pipeline failed: {report.get('error')}")
            return 1

        # Save report to file
        if not args.no_save:
            try:
                import json as _json
                from pathlib import Path as _Path
                out_dir = _Path(args.output_dir)
                out_dir.mkdir(parents=True, exist_ok=True)
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                report_path = out_dir / f"sequential_report_{timestamp}.md"
                report_path.write_text(report['report_text'], encoding='utf-8')
                logger.info(f"✓ Report saved to {report_path}")
            except Exception as e:
                logger.error(f"Error saving report: {e}")

        # Save to database (pass full report dict — same keys as generate_report())
        try:
            report_id = generator.db.save_report(report)
            if report_id:
                logger.info(f"✓ Report saved to database with ID: {report_id}")
        except Exception as e:
            logger.warning(f"Could not save report to database: {e}")

        print("\n" + "=" * 80)
        print("SEQUENTIAL INTELLIGENCE REPORT")
        print("=" * 80)
        print(report['report_text'])
        meta = report.get('metadata', {})
        logger.info(f"\n✓ Sequential pipeline complete! "
                    f"articles={meta.get('recent_articles_count', '?')} "
                    f"chunks={meta.get('historical_chunks_count', '?')} "
                    f"storylines={meta.get('narrative_storylines', '?')}")
        return 0

    # Check if macro-first pipeline is enabled
    if args.macro_first:
        logger.info("\n" + "=" * 80)
        logger.info("[MACRO-FIRST MODE] Serialized pipeline enabled")
        if from_time or to_time:
            logger.info(f"Time window: {from_time or 'N/A'} → {to_time or 'N/A'}")
        else:
            logger.info(f"Time window: last {args.days} day(s)")
        logger.info("=" * 80)

        try:
            report = generator.run_macro_first_pipeline(
                focus_areas=focus_areas,
                days=args.days,
                from_time=from_time,
                to_time=to_time,
                save=not args.no_save,
                save_to_db=True,
                output_dir=args.output_dir,
                top_articles=args.top_articles,
                min_similarity=args.min_similarity,
                min_fallback=args.min_articles,
                skip_article_signals=args.skip_article_signals
            )

            if not report['success']:
                logger.error(f"Macro-first pipeline failed: {report.get('error')}")
                return 1

            # Print trade signals summary
            print("\n" + "=" * 80)
            print("TRADE SIGNALS SUMMARY")
            print("=" * 80)

            if report.get('report_signals'):
                print("\n📊 REPORT-LEVEL SIGNALS (High-Conviction):")
                for sig in report['report_signals']:
                    emoji = "🟢" if sig['signal'] == 'BULLISH' else "🔴" if sig['signal'] == 'BEARISH' else "🟡"
                    print(f"\n  {emoji} {sig['ticker']} | {sig['signal']} | {sig['timeframe']}")
                    print(f"     Rationale: {sig['rationale']}")
                    print(f"     Confidence: {sig['confidence']:.0%}")
                    print(f"     Themes: {', '.join(sig.get('supporting_themes', []))}")

            if report.get('article_signals'):
                print(f"\n📰 ARTICLE-LEVEL SIGNALS: {sum(len(a['signals']) for a in report['article_signals'])} signals from {len(report['article_signals'])} articles")

                for article_data in report['article_signals'][:5]:  # Show first 5
                    print(f"\n  Article: {article_data['article_title'][:60]}...")
                    for sig in article_data['signals']:
                        emoji = "🟢" if sig['signal'] == 'BULLISH' else "🔴" if sig['signal'] == 'BEARISH' else "🟡"
                        print(f"    {emoji} {sig['ticker']}: {sig['signal']} (alignment: {sig['alignment_score']:.0%})")

                if len(report['article_signals']) > 5:
                    print(f"\n  ... and {len(report['article_signals']) - 5} more articles with signals")

            # Stats
            print("\n" + "-" * 80)
            print("PIPELINE STATS:")
            print(f"  Report-level signals: {len(report.get('report_signals', []))}")
            print(f"  Articles with tickers: {report.get('articles_with_tickers_count', 0)}")
            print(f"  Article-level signals: {sum(len(a['signals']) for a in report.get('article_signals', []))}")
            print(f"  Token savings: ~{5000 - report.get('token_savings_estimate', 500)} tokens/article")
            print("-" * 80)

            # Print full report
            print("\n" + "=" * 80)
            print("INTELLIGENCE REPORT")
            print("=" * 80)
            print(report['report_text'])

            logger.info("\n✓ Macro-first pipeline complete!")
            return 0

        except Exception as e:
            logger.error(f"Error during macro-first pipeline: {e}", exc_info=True)
            return 1

    # Generate report (original flow)
    try:
        if from_time or to_time:
            logger.info(f"\n[STEP 3] Generating report (time window: {from_time or 'N/A'} → {to_time or 'N/A'})...")
        else:
            logger.info(f"\n[STEP 3] Generating report (analyzing last {args.days} day(s))...")
        logger.info(f"Filtering parameters: top_articles={args.top_articles}, "
                   f"min_similarity={args.min_similarity}, min_fallback={args.min_articles}")
        report = generator.generate_report(
            focus_areas=focus_areas,
            days=args.days,
            from_time=from_time,
            to_time=to_time,
            rag_top_k=5,
            top_articles=args.top_articles,
            min_similarity=args.min_similarity,
            min_fallback=args.min_articles
        )

        if not report['success']:
            logger.error(f"Report generation failed: {report.get('error')}")
            return 1

        logger.info("✓ Report generated successfully")

    except Exception as e:
        logger.error(f"Error during report generation: {e}", exc_info=True)
        return 1

    # Save report to files
    if not args.no_save:
        try:
            logger.info(f"\n[STEP 4] Saving report to {args.output_dir}/...")
            report_file = generator.save_report(report, output_dir=args.output_dir)
            logger.info(f"✓ Report saved to files successfully")
        except Exception as e:
            logger.error(f"Error saving report to files: {e}")
            return 1

    # Save report to database (for HITL dashboard)
    try:
        logger.info(f"\n[STEP 5] Saving report to database for HITL review...")
        report_id = generator.db.save_report(report)
        if report_id:
            logger.info(f"✓ Report saved to database with ID: {report_id}")
            logger.info(f"✓ You can now review it at: http://localhost:8501")
        else:
            logger.warning("Failed to save report to database")
    except Exception as e:
        logger.error(f"Error saving report to database: {e}")
        # Don't fail the entire script if DB save fails

    # SPRINT 2.2: Generate structured analysis for each article (if enabled)
    if args.analyze_articles:
        try:
            logger.info(f"\n[STEP 6 - SPRINT 2.2] Generating structured analysis for articles...")
            logger.info(f"Processing {len(report['sources']['recent_articles'])} articles with full schema...")

            analysis_stats = {
                'success': 0,
                'failed': 0,
                'trade_signals_found': 0,
                'saved_to_db': 0
            }

            for i, article_ref in enumerate(report['sources']['recent_articles'], 1):
                try:
                    # Fetch full article from database
                    article = generator.db.get_article_by_link(article_ref['link'])
                    if not article:
                        logger.warning(f"  [{i}/{len(report['sources']['recent_articles'])}] Article not found in DB: {article_ref['title'][:50]}...")
                        analysis_stats['failed'] += 1
                        continue

                    logger.info(f"  [{i}/{len(report['sources']['recent_articles'])}] Analyzing: {article['title'][:60]}...")

                    # Generate full analysis
                    result = generator.generate_full_analysis(
                        article_text=article['full_text'],
                        article_metadata={
                            'title': article['title'],
                            'source': article['source'],
                            'published_date': article.get('published_date')
                        }
                    )

                    if result['success']:
                        analysis_stats['success'] += 1

                        # Count trade signals
                        signals = result['structured'].get('related_tickers', [])
                        if signals:
                            analysis_stats['trade_signals_found'] += len(signals)
                            logger.info(f"      💰 Trade Signals: {len(signals)} ({', '.join([s['ticker'] for s in signals])})")

                        # Save to database (ai_analysis column)
                        try:
                            generator.db.update_article_analysis(
                                article_id=article['id'],
                                analysis_data=result['structured']
                            )
                            analysis_stats['saved_to_db'] += 1
                            logger.info(f"      ✅ Saved to database")
                        except Exception as db_error:
                            logger.warning(f"      ⚠️ Failed to save analysis to DB: {db_error}")
                    else:
                        analysis_stats['failed'] += 1
                        logger.warning(f"      ❌ Analysis failed: {result.get('error', 'Unknown error')}")

                except Exception as article_error:
                    logger.error(f"  [{i}] Error processing article: {article_error}")
                    analysis_stats['failed'] += 1

            # Summary
            logger.info("\n" + "-" * 80)
            logger.info("STRUCTURED ANALYSIS SUMMARY (Sprint 2.2)")
            logger.info("-" * 80)
            success_rate = (analysis_stats['success'] / len(report['sources']['recent_articles']) * 100) if report['sources']['recent_articles'] else 0
            logger.info(f"Success Rate: {success_rate:.1f}% ({analysis_stats['success']}/{len(report['sources']['recent_articles'])})")
            logger.info(f"Trade Signals Extracted: {analysis_stats['trade_signals_found']}")
            logger.info(f"Saved to Database: {analysis_stats['saved_to_db']}")
            logger.info("-" * 80)

        except Exception as e:
            logger.error(f"Error during structured analysis: {e}", exc_info=True)
            # Don't fail the entire script if structured analysis fails

    # Print report summary
    logger.info("\n" + "=" * 80)
    logger.info("REPORT SUMMARY")
    logger.info("=" * 80)
    logger.info(f"\nGenerated: {report['timestamp']}")
    logger.info(f"Model: {report['metadata']['model_used']}")
    logger.info(f"Recent articles analyzed: {report['metadata']['recent_articles_count']}")
    logger.info(f"Historical context chunks: {report['metadata']['historical_chunks_count']}")
    logger.info(f"Report length: {len(report['report_text'])} characters")

    # Print report text
    print("\n" + "=" * 80)
    print("INTELLIGENCE REPORT")
    print("=" * 80)
    print(report['report_text'])
    print("\n" + "=" * 80)

    # Print sources
    print("\nSOURCES:")
    print(f"\nRecent Articles ({len(report['sources']['recent_articles'])}):")
    for i, article in enumerate(report['sources']['recent_articles'][:10], 1):
        print(f"  [{i}] {article['title']}")
        print(f"      {article['source']} - {article['published_date']}")
        print(f"      {article['link']}")

    if len(report['sources']['recent_articles']) > 10:
        print(f"  ... and {len(report['sources']['recent_articles']) - 10} more")

    print(f"\nHistorical Context ({len(report['sources']['historical_context'])}):")
    for i, ctx in enumerate(report['sources']['historical_context'][:5], 1):
        print(f"  [{i}] {ctx['title']} (similarity: {ctx['similarity']:.3f})")
        print(f"      {ctx['link']}")

    if len(report['sources']['historical_context']) > 5:
        print(f"  ... and {len(report['sources']['historical_context']) - 5} more")

    logger.info("\n✓ Report generation complete!")
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
