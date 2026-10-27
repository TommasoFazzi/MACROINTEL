"""IngestionRunStats — Structured metrics accumulator for the ingestion pipeline.

Collects per-stage counters, timings, blocklist hits with pattern labels, and
extraction method distribution during a single pipeline run. Call log_report()
at the end of the run to emit a human-readable summary for debugging.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .logger import get_logger

logger = get_logger(__name__)


@dataclass
class IngestionRunStats:
    """Accumulates all ingestion metrics for a single pipeline run."""

    run_id: str = field(default_factory=lambda: datetime.now().strftime("%Y%m%d_%H%M%S"))

    # ── Stage timings ────────────────────────────────────────────────────────
    _stage_starts: Dict[str, float] = field(default_factory=dict, repr=False)
    stage_timings: Dict[str, float] = field(default_factory=dict)

    # ── Article funnel counts ─────────────────────────────────────────────────
    count_raw: int = 0             # After RSS/fallback parsing
    count_post_dedup: int = 0      # After quick-hash deduplication
    count_post_blocklist: int = 0  # After keyword blocklist filter
    count_post_age: int = 0        # After selection (new link, within window, capped)
    extraction_attempted: int = 0  # Sent to content extractor
    extraction_ok: int = 0         # Successfully extracted text

    # ── Deduplication ─────────────────────────────────────────────────────────
    duplicates_removed: int = 0

    # ── Blocklist hits (false-positive review) ────────────────────────────────
    # Each entry: (pattern_string, feed_source, title[:80])
    blocklist_hits: List[Tuple[str, str, str]] = field(default_factory=list)

    # ── Selection (stage 1.7) ─────────────────────────────────────────────────
    age_filtered: int = 0          # Outside the window (or legacy 24h filter on fallback)
    already_attempted: int = 0     # Link in articles or ingestion_seen_links
    over_cap: int = 0              # Eligible but beyond MAX_SELECTED_PER_RUN
    scraped_too_old: int = 0       # Scraped page date older than the window (after extraction)

    # ── Extraction method distribution ────────────────────────────────────────
    extraction_by_method: Dict[str, int] = field(default_factory=dict)
    pdf_direct: int = 0   # Level 1: direct .pdf URL
    pdf_level2: int = 0   # Level 2: PDF link found in HTML landing page

    # ── Failed extractions ────────────────────────────────────────────────────
    failed_urls: List[str] = field(default_factory=list)

    # ── Stage timing helpers ──────────────────────────────────────────────────

    def stage_start(self, name: str) -> None:
        """Record start time for a named pipeline stage."""
        self._stage_starts[name] = time.monotonic()

    def stage_end(self, name: str) -> float:
        """Record end time and return elapsed seconds. Logs nothing by itself."""
        elapsed = time.monotonic() - self._stage_starts.get(name, time.monotonic())
        self.stage_timings[name] = round(elapsed, 2)
        return elapsed

    # ── Data collection helpers ───────────────────────────────────────────────

    def record_blocked(self, pattern: str, source: str, title: str) -> None:
        """Record a single blocklist hit for false-positive analysis."""
        self.blocklist_hits.append((pattern, source or "?", title[:80]))

    def record_extraction_method(self, method: Optional[str]) -> None:
        """Increment counter for the given extraction method."""
        key = method or "unknown"
        self.extraction_by_method[key] = self.extraction_by_method.get(key, 0) + 1

    # ── End-of-run structured report ──────────────────────────────────────────

    def log_report(self) -> None:
        """
        Emit a structured end-of-run summary to the logger.

        Called once at the end of IngestionPipeline.run(). Provides:
        - Stage timings for pipeline optimization
        - Article funnel for sanity checks
        - Extraction method distribution for extractor health
        - Blocklist hits grouped by pattern for false-positive review
        - Failed URLs list for network/anti-bot debugging
        """
        sep = "─" * 72

        logger.info(sep)
        logger.info(f"INGESTION RUN REPORT  [run_id={self.run_id}]")
        logger.info(sep)

        # Stage timings
        if self.stage_timings:
            logger.info("  Stage timings:")
            total_secs = sum(self.stage_timings.values())
            for stage, secs in self.stage_timings.items():
                bar = "█" * max(1, int(secs / max(total_secs, 1) * 20))
                logger.info(f"    {stage:<30s}  {secs:>6.1f}s  {bar}")
            logger.info(f"    {'TOTAL':<30s}  {total_secs:>6.1f}s")

        # Article funnel
        logger.info(sep)
        logger.info("  Article funnel:")
        logger.info(f"    RSS/scraper parsed    {self.count_raw:>6,}")
        dedup_pct = (self.duplicates_removed / max(self.count_raw, 1)) * 100
        logger.info(f"    After dedup           {self.count_post_dedup:>6,}  "
                    f"(-{self.duplicates_removed:,} = {dedup_pct:.1f}%)")
        n_blocked = len(self.blocklist_hits)
        blocklist_pct = (n_blocked / max(self.count_post_dedup, 1)) * 100
        logger.info(f"    After blocklist       {self.count_post_blocklist:>6,}  "
                    f"(-{n_blocked:,} = {blocklist_pct:.1f}%)")
        logger.info(f"    After selection       {self.count_post_age:>6,}  "
                    f"(-{self.already_attempted:,} attempted, -{self.age_filtered:,} old, "
                    f"-{self.over_cap:,} over cap)")
        ok_pct = (self.extraction_ok / max(self.extraction_attempted, 1)) * 100
        logger.info(f"    Extraction success    {self.extraction_ok:>6,}  "
                    f"/ {self.extraction_attempted} ({ok_pct:.1f}%)")
        if self.scraped_too_old:
            logger.info(f"    Old scraped pages     {self.scraped_too_old:>6,}  dropped after extraction")

        # Extraction method distribution
        if self.extraction_by_method:
            logger.info(sep)
            logger.info("  Extraction method distribution:")
            total_ext = max(self.extraction_attempted, 1)
            for method, count in sorted(self.extraction_by_method.items(), key=lambda x: -x[1]):
                pct = count / total_ext * 100
                bar = "█" * max(1, int(pct / 5))
                logger.info(f"    {method:<38s} {count:>4}  ({pct:5.1f}%)  {bar}")
            if self.pdf_direct:
                logger.info(f"    └─ of which: direct PDF (Level 1)      {self.pdf_direct:>4}")
            if self.pdf_level2:
                logger.info(f"    └─ of which: landing page PDF (Level 2){self.pdf_level2:>4}")

        # Blocklist hits — grouped by pattern for false-positive review
        if self.blocklist_hits:
            logger.info(sep)
            logger.info(f"  Blocklist hits ({n_blocked}) — review for false positives:")
            by_pattern: Dict[str, List[Tuple[str, str]]] = {}
            for pat, src, title in self.blocklist_hits:
                by_pattern.setdefault(pat, []).append((src, title))
            for pat, entries in sorted(by_pattern.items(), key=lambda x: -len(x[1])):
                logger.info(f"    [{len(entries):>3}x]  pattern: {pat}")
                for src, title in entries[:3]:
                    logger.info(f"           [{src}]  {title}")
                if len(entries) > 3:
                    logger.info(f"           ... +{len(entries) - 3} more")

        # Failed extractions
        if self.failed_urls:
            logger.info(sep)
            n_fail = len(self.failed_urls)
            logger.info(f"  Extraction failures ({n_fail}):")
            for url in self.failed_urls[:15]:
                logger.info(f"    ✗  {url}")
            if n_fail > 15:
                logger.info(f"    ... +{n_fail - 15} more")

        logger.info(sep)
