#!/usr/bin/env python3
"""
Daily Intelligence Pipeline Orchestrator

Esegue la pipeline giornaliera completa:
1. Ingestion (RSS feeds)
1.5. PDF Ingestion (documenti istituzionali)
2. Market Data (OpenBB)
3. NLP Processing
4. Database Loading
5. Narrative Processing (clustering e storyline evolution)
6. Report Generation
7. Weekly Report (solo domenica)
8. Monthly Recap (prima domenica del mese)

Usage:
    python scripts/daily_pipeline.py              # Run completo
    python scripts/daily_pipeline.py --dry-run    # Solo verifica senza eseguire
    python scripts/daily_pipeline.py --step 3     # Solo step specifico
    python scripts/daily_pipeline.py --from-step 3 # Da step 3 in poi
    python scripts/daily_pipeline.py --verbose    # Log DEBUG
    python scripts/daily_pipeline.py --skip-weekly # Salta weekly/monthly report
"""

import sys
import os
import subprocess
import time
import argparse
import logging
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Optional

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env", override=False)  # optional: env vars from Docker take precedence

from src.utils.logger import get_logger
from scripts.pipeline_manifest import create_manifest, cleanup_old_manifests


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class StepResult:
    """Risultato di un singolo step della pipeline."""
    step_name: str
    success: bool
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    error: Optional[str] = None


@dataclass
class PipelineStep:
    """Configurazione di uno step della pipeline."""
    name: str
    command: str
    description: str
    timeout_seconds: int = 600
    continue_on_failure: bool = False


@dataclass
class PipelineResult:
    """Risultato complessivo della pipeline."""
    run_id: str
    success: bool
    total_duration: float
    steps_completed: int
    steps_total: int
    step_results: List[StepResult]
    error: Optional[str] = None


# =============================================================================
# Pipeline Configuration
# =============================================================================

DEFAULT_STEPS = [
    PipelineStep(
        name="ingestion",
        command="python -m src.ingestion.pipeline",
        description="Fetch e parsing degli RSS feeds",
        timeout_seconds=900,  # 15 min
        continue_on_failure=False
    ),
    PipelineStep(
        name="market_data",
        command="python scripts/fetch_daily_market_data.py",
        description="Fetch dati di mercato via OpenBB",
        timeout_seconds=300,  # 5 min
        continue_on_failure=True  # Opzionale, non blocca la pipeline
    ),
    PipelineStep(
        name="nlp_processing",
        command="python scripts/process_nlp.py",
        description="Elaborazione NLP degli articoli",
        timeout_seconds=1800,  # 30 min
        continue_on_failure=False
    ),
    PipelineStep(
        name="load_to_database",
        command="python scripts/load_to_database.py",
        description="Caricamento articoli nel database",
        timeout_seconds=600,  # 10 min
        continue_on_failure=False
    ),
    PipelineStep(
        name="narrative_processing",
        command="python scripts/process_narratives.py --days 1",
        description="Clustering narrativo e evoluzione storyline",
        timeout_seconds=1800,  # 30 min safety net (gemini-2.0-flash ~3-5s/call)
        continue_on_failure=True  # Report generato anche senza storyline
    ),
    PipelineStep(
        name="community_detection",
        command="python scripts/compute_communities.py --min-weight 0.25",
        description="Community detection (Louvain) sul grafo narrativo",
        timeout_seconds=300,  # 5 min safety net
        continue_on_failure=True  # Non blocca il report se fallisce
    ),
    PipelineStep(
        name="entity_extraction",
        command="python scripts/extract_entities.py --days 2",
        description="Estrazione entità dagli articoli e popolamento tabella entities",
        timeout_seconds=300,  # 5 min (solo articoli ultimi 2 giorni)
        continue_on_failure=True  # Map enrichment, non blocca il report
    ),
    PipelineStep(
        name="geocoding",
        command="python scripts/geocode_geonames.py --limit 200",
        description="Geocoding entità via GeoNames+Gemini CoT (con Photon fallback)",
        timeout_seconds=300,  # 5 min
        continue_on_failure=True  # Map enrichment, non blocca il report
    ),
    PipelineStep(
        name="refresh_map_data",
        command="python scripts/refresh_map_data.py",
        description="Refresh bridge + intelligence scores + invalida cache mappa",
        timeout_seconds=120,  # 2 min
        continue_on_failure=True  # Map enrichment, non blocca il report
    ),
    PipelineStep(
        name="generate_report",
        command="python scripts/generate_report.py --macro-first --skip-article-signals",
        description="Generazione report giornaliero",
        timeout_seconds=900,  # 15 min
        continue_on_failure=False
    ),
]

# Conditional steps (run after main pipeline based on day of week)
WEEKLY_REPORT_STEP = PipelineStep(
    name="weekly_report",
    command="python scripts/generate_weekly_report.py",
    description="Generazione report settimanale (meta-analisi)",
    timeout_seconds=900,  # 15 min
    continue_on_failure=True  # Non blocca se fallisce
)

def get_monthly_recap_command() -> str:
    """Genera il comando per il recap mensile con date dinamiche."""
    from datetime import timedelta
    today = datetime.now().date()
    start_date = today - timedelta(days=28)  # 4 settimane fa
    return f"python scripts/generate_recap_report.py --start {start_date} --end {today}"


MONTHLY_RECAP_STEP = PipelineStep(
    name="monthly_recap",
    command="",  # Sarà impostato dinamicamente
    description="Generazione recap mensile (ultime 4 settimane)",
    timeout_seconds=1200,  # 20 min
    continue_on_failure=True  # Non blocca se fallisce
)


def is_sunday() -> bool:
    """Verifica se oggi è domenica."""
    return datetime.now().weekday() == 6


def is_first_sunday_of_month() -> bool:
    """Verifica se oggi è la prima domenica del mese."""
    today = datetime.now()
    if today.weekday() != 6:  # Non è domenica
        return False
    return today.day <= 7  # Prima settimana del mese


def count_weekly_reports_since_last_recap() -> int:
    """
    Conta quanti weekly report sono stati generati dall'ultimo recap.

    Returns:
        Numero di weekly report dall'ultimo recap (o totali se non ci sono recap)
    """
    try:
        from src.storage.database import DatabaseManager
        db = DatabaseManager()

        with db.get_connection() as conn:
            with conn.cursor() as cur:
                # Trova la data dell'ultimo recap
                cur.execute("""
                    SELECT report_date FROM reports
                    WHERE report_type = 'recap'
                    ORDER BY report_date DESC
                    LIMIT 1
                """)
                last_recap = cur.fetchone()

                if last_recap:
                    last_recap_date = last_recap[0]
                    # Conta weekly reports dopo l'ultimo recap
                    cur.execute("""
                        SELECT COUNT(*) FROM reports
                        WHERE report_type = 'weekly'
                        AND report_date > %s
                    """, (last_recap_date,))
                else:
                    # Nessun recap precedente, conta tutti i weekly
                    cur.execute("""
                        SELECT COUNT(*) FROM reports
                        WHERE report_type = 'weekly'
                    """)

                count = cur.fetchone()[0]
                return count

    except Exception as e:
        # Se c'è un errore (es. DB non disponibile), ritorna 0
        logging.getLogger(__name__).warning(f"Could not count weekly reports: {e}")
        return 0


def should_generate_monthly_recap() -> tuple[bool, int]:
    """
    Verifica se è il momento di generare il monthly recap.

    Returns:
        Tuple (should_generate, weekly_count)
    """
    weekly_count = count_weekly_reports_since_last_recap()
    return weekly_count >= 4, weekly_count


# =============================================================================
# Main Pipeline Class
# =============================================================================

class DailyPipeline:
    """Orchestratore della pipeline giornaliera."""

    def __init__(
        self,
        steps: List[PipelineStep] = None,
        dry_run: bool = False,
        verbose: bool = False,
        skip_weekly: bool = False
    ):
        self.steps = steps or DEFAULT_STEPS
        self.dry_run = dry_run
        self.verbose = verbose
        self.skip_weekly = skip_weekly
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.project_root = PROJECT_ROOT
        self.log_dir = PROJECT_ROOT / "logs"
        self.log_file = self.log_dir / f"daily_pipeline_{self.run_id}.log"

        # Create pipeline manifest for deterministic file passing
        self.manifest_path = create_manifest(self.run_id)

        # Setup logging
        self._setup_logging()

    def _setup_logging(self):
        """Configura il logging con output su file e console."""
        self.logger = logging.getLogger(f"daily_pipeline.{self.run_id}")
        self.logger.setLevel(logging.DEBUG if self.verbose else logging.INFO)
        self.logger.handlers = []  # Reset handlers

        # Formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG if self.verbose else logging.INFO)
        console_handler.setFormatter(formatter)
        self.logger.addHandler(console_handler)

        # File handler
        self.log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(self.log_file)
        file_handler.setLevel(logging.DEBUG)  # Sempre DEBUG su file
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)

    def run(
        self,
        from_step: int = None,
        only_step: int = None
    ) -> PipelineResult:
        """
        Esegue la pipeline.

        Args:
            from_step: Inizia da questo step (1-indexed)
            only_step: Esegue solo questo step (1-indexed)

        Returns:
            PipelineResult con i risultati
        """
        start_time = time.time()
        step_results: List[StepResult] = []

        # Header
        self._log_header()

        # Validate environment
        if not self._validate_environment():
            return PipelineResult(
                run_id=self.run_id,
                success=False,
                total_duration=time.time() - start_time,
                steps_completed=0,
                steps_total=len(self.steps),
                step_results=[],
                error="Environment validation failed"
            )

        # Determine which steps to run
        steps_to_run = self._get_steps_to_run(from_step, only_step)

        self.logger.info(f"Steps to execute: {len(steps_to_run)}")
        if self.dry_run:
            self.logger.info("DRY-RUN MODE: commands will not be executed")
        self.logger.info("")

        # Execute steps
        pipeline_failed = False
        for i, step in enumerate(steps_to_run, 1):
            step_num = self.steps.index(step) + 1

            self.logger.info("=" * 80)
            self.logger.info(f"[STEP {step_num}] {step.name}")
            self.logger.info(f"Description: {step.description}")
            self.logger.info(f"Command: {step.command}")
            self.logger.info(f"Timeout: {step.timeout_seconds}s")
            self.logger.info("=" * 80)

            if self.dry_run:
                self.logger.info("DRY-RUN: Skipping execution")
                result = StepResult(
                    step_name=step.name,
                    success=True,
                    exit_code=0,
                    duration_seconds=0.0
                )
            else:
                result = self._execute_step(step)

            step_results.append(result)

            # Log result
            emoji = "\u2713" if result.success else "\u2717"
            status = "completed" if result.success else "FAILED"
            self.logger.info(f"[STEP {step_num}] {emoji} {step.name} {status} ({result.duration_seconds:.1f}s)")

            if not result.success:
                if result.error:
                    self.logger.error(f"Error: {result.error}")
                if result.stderr:
                    self.logger.error(f"STDERR:\n{result.stderr}")

                if not step.continue_on_failure:
                    self.logger.error(f"Pipeline stopped at step {step_num} (fail-fast)")
                    pipeline_failed = True
                    break
                else:
                    self.logger.warning(f"Step {step_num} failed but continue_on_failure=True, continuing...")

            self.logger.info("")

        # Summary
        total_duration = time.time() - start_time
        success = not pipeline_failed and all(
            r.success or self.steps[i].continue_on_failure
            for i, r in enumerate(step_results)
        )

        pipeline_result = PipelineResult(
            run_id=self.run_id,
            success=success,
            total_duration=total_duration,
            steps_completed=len([r for r in step_results if r.success]),
            steps_total=len(steps_to_run),
            step_results=step_results,
            error="Pipeline failed" if pipeline_failed else None
        )

        self._log_summary(pipeline_result)

        # Run conditional steps (weekly/monthly) if main pipeline succeeded
        if pipeline_result.success and not self.skip_weekly and only_step is None:
            self._run_conditional_steps(step_results)

        # Notification
        if not self.dry_run:
            self._send_notification(pipeline_result)

        # Cleanup old logs and manifests
        self._cleanup_old_logs()
        cleanup_old_manifests(keep_days=30)

        return pipeline_result

    def _run_conditional_steps(self, step_results: List[StepResult]):
        """Esegue gli step condizionali basati sul giorno della settimana."""

        # Weekly report: solo domenica
        if is_sunday():
            self.logger.info("")
            self.logger.info("=" * 80)
            self.logger.info("CONDITIONAL STEPS (Sunday)")
            self.logger.info("=" * 80)

            # Step 6: Weekly Report
            self.logger.info("")
            self.logger.info(f"[STEP 6] {WEEKLY_REPORT_STEP.name}")
            self.logger.info(f"Description: {WEEKLY_REPORT_STEP.description}")
            self.logger.info(f"Command: {WEEKLY_REPORT_STEP.command}")

            if self.dry_run:
                self.logger.info("DRY-RUN: Skipping execution")
                weekly_result = StepResult(
                    step_name=WEEKLY_REPORT_STEP.name,
                    success=True,
                    exit_code=0,
                    duration_seconds=0.0
                )
            else:
                weekly_result = self._execute_step(WEEKLY_REPORT_STEP)

            step_results.append(weekly_result)
            emoji = "\u2713" if weekly_result.success else "\u2717"
            status = "completed" if weekly_result.success else "FAILED"
            self.logger.info(f"[STEP 6] {emoji} {WEEKLY_REPORT_STEP.name} {status} ({weekly_result.duration_seconds:.1f}s)")

            if not weekly_result.success and weekly_result.stderr:
                self.logger.warning(f"Weekly report error (non-blocking): {weekly_result.stderr[:500]}")

            # Step 7: Monthly Recap (dopo 4 weekly report)
            should_recap, weekly_count = should_generate_monthly_recap()
            if should_recap:
                # Genera comando con date dinamiche
                recap_step = PipelineStep(
                    name=MONTHLY_RECAP_STEP.name,
                    command=get_monthly_recap_command(),
                    description=MONTHLY_RECAP_STEP.description,
                    timeout_seconds=MONTHLY_RECAP_STEP.timeout_seconds,
                    continue_on_failure=MONTHLY_RECAP_STEP.continue_on_failure
                )
                self.logger.info("")
                self.logger.info(f"[STEP 7] {recap_step.name} ({weekly_count} weekly reports since last recap)")
                self.logger.info(f"Description: {recap_step.description}")
                self.logger.info(f"Command: {recap_step.command}")

                if self.dry_run:
                    self.logger.info("DRY-RUN: Skipping execution")
                    recap_result = StepResult(
                        step_name=recap_step.name,
                        success=True,
                        exit_code=0,
                        duration_seconds=0.0
                    )
                else:
                    recap_result = self._execute_step(recap_step)

                step_results.append(recap_result)
                emoji = "\u2713" if recap_result.success else "\u2717"
                status = "completed" if recap_result.success else "FAILED"
                self.logger.info(f"[STEP 7] {emoji} {recap_step.name} {status} ({recap_result.duration_seconds:.1f}s)")

                if not recap_result.success and recap_result.stderr:
                    self.logger.warning(f"Monthly recap error (non-blocking): {recap_result.stderr[:500]}")
            else:
                self.logger.info("")
                self.logger.info(f"Skipping monthly recap ({weekly_count}/4 weekly reports since last recap)")
        else:
            self.logger.info("")
            self.logger.info("Skipping weekly/monthly reports (not Sunday)")

    def _get_steps_to_run(
        self,
        from_step: int = None,
        only_step: int = None
    ) -> List[PipelineStep]:
        """Determina quali step eseguire."""
        if only_step is not None:
            if 1 <= only_step <= len(self.steps):
                return [self.steps[only_step - 1]]
            else:
                self.logger.error(f"Invalid step number: {only_step}")
                return []

        if from_step is not None:
            if 1 <= from_step <= len(self.steps):
                return self.steps[from_step - 1:]
            else:
                self.logger.error(f"Invalid from-step number: {from_step}")
                return []

        return self.steps

    def _execute_step(self, step: PipelineStep) -> StepResult:
        """Esegue un singolo step."""
        start_time = time.time()

        try:
            result = subprocess.run(
                step.command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=step.timeout_seconds,
                cwd=str(self.project_root),
                env={
                    **os.environ,
                    'PYTHONPATH': str(self.project_root),
                    'PIPELINE_MANIFEST_PATH': str(self.manifest_path),
                }
            )

            duration = time.time() - start_time

            # Log stdout if verbose
            if result.stdout and self.verbose:
                self.logger.debug(f"STDOUT:\n{result.stdout}")

            return StepResult(
                step_name=step.name,
                success=(result.returncode == 0),
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_seconds=duration
            )

        except subprocess.TimeoutExpired as e:
            duration = time.time() - start_time
            return StepResult(
                step_name=step.name,
                success=False,
                exit_code=-1,
                stdout=e.stdout.decode() if e.stdout else "",
                stderr=e.stderr.decode() if e.stderr else "",
                duration_seconds=duration,
                error=f"Timeout after {step.timeout_seconds}s"
            )

        except Exception as e:
            duration = time.time() - start_time
            return StepResult(
                step_name=step.name,
                success=False,
                exit_code=-1,
                duration_seconds=duration,
                error=str(e)
            )

    def _validate_environment(self) -> bool:
        """Verifica che l'ambiente sia configurato correttamente."""
        self.logger.info("Validating environment...")

        # Check .env file (optional in Docker — env vars injected by compose)
        env_file = self.project_root / ".env"
        if env_file.exists():
            self.logger.info("\u2713 .env file found")
        elif os.getenv("DATABASE_URL"):
            self.logger.info("\u2713 Environment variables set (Docker mode)")
        else:
            self.logger.error(f".env file not found and DATABASE_URL not set")
            return False

        # Check required directories
        required_dirs = ["src", "scripts", "data"]
        for dir_name in required_dirs:
            dir_path = self.project_root / dir_name
            if not dir_path.exists():
                self.logger.error(f"Required directory not found: {dir_path}")
                return False
        self.logger.info("\u2713 Required directories exist")

        # Check scripts exist
        for step in self.steps:
            if step.command.startswith("python scripts/"):
                script_name = step.command.split()[1]
                script_path = self.project_root / script_name
                if not script_path.exists():
                    self.logger.error(f"Script not found: {script_path}")
                    return False
        self.logger.info("\u2713 All scripts exist")

        self.logger.info("Environment validation passed")
        self.logger.info("")
        return True

    def _log_header(self):
        """Log l'header della pipeline."""
        self.logger.info("=" * 80)
        self.logger.info("DAILY INTELLIGENCE PIPELINE")
        self.logger.info("=" * 80)
        self.logger.info(f"Run ID: {self.run_id}")
        self.logger.info(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.logger.info(f"Log file: {self.log_file}")
        self.logger.info(f"Manifest: {self.manifest_path}")
        self.logger.info(f"Dry-run: {self.dry_run}")
        self.logger.info(f"Verbose: {self.verbose}")
        self.logger.info("")

    def _log_summary(self, result: PipelineResult):
        """Log il summary finale."""
        self.logger.info("")
        self.logger.info("=" * 80)
        self.logger.info("PIPELINE SUMMARY")
        self.logger.info("=" * 80)

        # Format duration
        minutes = int(result.total_duration // 60)
        seconds = int(result.total_duration % 60)
        duration_str = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"

        status = "SUCCESS" if result.success else "FAILED"
        emoji = "\u2713" if result.success else "\u2717"

        self.logger.info(f"Status: {emoji} {status}")
        self.logger.info(f"Total duration: {duration_str}")
        self.logger.info(f"Steps completed: {result.steps_completed}/{result.steps_total}")
        self.logger.info("")
        self.logger.info("Step Results:")

        for i, step_result in enumerate(result.step_results, 1):
            emoji = "\u2713" if step_result.success else "\u2717"
            self.logger.info(
                f"  [{i}] {emoji} {step_result.step_name:<20} - {step_result.duration_seconds:.1f}s"
            )

        self.logger.info("=" * 80)

        if result.error:
            self.logger.error(f"Error: {result.error}")

    def _send_notification(self, result: PipelineResult):
        """Send pipeline notification (cross-platform)."""
        notify_on_success = os.getenv("PIPELINE_NOTIFY_ON_SUCCESS", "true").lower() == "true"
        notify_on_failure = os.getenv("PIPELINE_NOTIFY_ON_FAILURE", "true").lower() == "true"

        if result.success and not notify_on_success:
            return
        if not result.success and not notify_on_failure:
            return

        status = "SUCCESS" if result.success else "FAILED"
        title = f"[Intelligence ITA] Pipeline {status}"
        if result.success:
            message = f"Pipeline completed successfully ({result.steps_completed}/{result.steps_total} steps)"
        else:
            message = f"Pipeline FAILED at step {result.steps_completed + 1}"

        # Try email notification first (production / Linux)
        smtp_host = os.getenv("SMTP_HOST")
        notify_email = os.getenv("NOTIFY_EMAIL")
        if smtp_host and notify_email:
            self._send_email_notification(title, message, smtp_host, notify_email)
            return

        # Fallback to macOS desktop notification (local dev)
        try:
            subprocess.run(
                ["terminal-notifier", "-title", title, "-message", message, "-sound", "default"],
                capture_output=True,
                timeout=5
            )
        except FileNotFoundError:
            try:
                subprocess.run(
                    ["osascript", "-e", f'display notification "{message}" with title "{title}"'],
                    capture_output=True,
                    timeout=5
                )
            except Exception:
                pass
        except Exception:
            pass

    def _send_email_notification(self, subject: str, body: str, smtp_host: str, to_email: str):
        """Send email notification for production deployments."""
        import smtplib
        from email.message import EmailMessage

        try:
            msg = EmailMessage()
            msg['Subject'] = subject
            msg['From'] = os.getenv("SMTP_FROM", "pipeline@intelligence-ita.com")
            msg['To'] = to_email
            msg.set_content(body)

            smtp_port = int(os.getenv("SMTP_PORT", "587"))
            smtp_user = os.getenv("SMTP_USER", "")
            smtp_pass = os.getenv("SMTP_PASS", "")

            with smtplib.SMTP(smtp_host, smtp_port) as s:
                if smtp_port != 25:
                    s.starttls()
                if smtp_user:
                    s.login(smtp_user, smtp_pass)
                s.send_message(msg)

            self.logger.info(f"Notification email sent to {to_email}")
        except Exception as e:
            self.logger.warning(f"Failed to send email notification: {e}")

    def _cleanup_old_logs(self, max_days: int = None):
        """Rimuove log files piu vecchi di max_days."""
        if max_days is None:
            max_days = int(os.getenv("PIPELINE_MAX_LOG_DAYS", "30"))

        try:
            cutoff = datetime.now().timestamp() - (max_days * 24 * 60 * 60)
            removed = 0

            for log_file in self.log_dir.glob("daily_pipeline_*.log"):
                if log_file.stat().st_mtime < cutoff:
                    log_file.unlink()
                    removed += 1

            if removed > 0:
                self.logger.info(f"Cleaned up {removed} old log files (>{max_days} days)")

        except Exception as e:
            self.logger.warning(f"Failed to cleanup old logs: {e}")


# =============================================================================
# Main Entry Point
# =============================================================================

def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(
        description="Daily Intelligence Pipeline Orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python scripts/daily_pipeline.py              # Run completo
    python scripts/daily_pipeline.py --dry-run    # Solo verifica
    python scripts/daily_pipeline.py --step 3     # Solo step 3
    python scripts/daily_pipeline.py --from-step 3 # Da step 3 in poi
    python scripts/daily_pipeline.py --verbose    # Log DEBUG
    python scripts/daily_pipeline.py --skip-weekly # Salta weekly/monthly

Steps:
    1. ingestion        - Fetch RSS feeds
    2. market_data      - Fetch market data
    3. nlp_processing   - NLP processing
    4. load_to_database - Load to database
    5. generate_report  - Generate daily report
    6. weekly_report    - Weekly meta-analysis (Sunday only)
    7. monthly_recap    - Monthly recap (after 4 weekly reports)
        """
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate without executing"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG logging"
    )
    parser.add_argument(
        "--step",
        type=int,
        metavar="N",
        help="Execute only step N (1-5)"
    )
    parser.add_argument(
        "--from-step",
        type=int,
        metavar="N",
        help="Start from step N (1-5)"
    )
    parser.add_argument(
        "--skip-weekly",
        action="store_true",
        help="Skip weekly/monthly reports even on Sunday"
    )

    args = parser.parse_args()

    # Create and run pipeline
    pipeline = DailyPipeline(
        dry_run=args.dry_run,
        verbose=args.verbose,
        skip_weekly=args.skip_weekly
    )

    result = pipeline.run(
        from_step=args.from_step,
        only_step=args.step
    )

    return 0 if result.success else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(130)
