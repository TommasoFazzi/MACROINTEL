"""Warnings that must be visible on the GitHub Actions run page.

`::warning` workflow commands printed by a pipeline step never reach GitHub:
scripts/daily_pipeline.py captures child output and re-logs it with a prefix,
so the runner no longer sees the command at the start of a line. Instead,
append to the file named by GITHUB_STEP_SUMMARY, which the runner renders on
the run page whatever the step's outcome. The variable is inherited by the
pipeline's child processes.
"""

import os

from .logger import get_logger

logger = get_logger(__name__)


def report_run_warning(title: str, message: str) -> None:
    """Log a warning and, under GitHub Actions, add it to the run summary. Never raises."""
    logger.warning(f"{title}: {message}")
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"- ⚠️ **{title}**: {message}\n")
    except OSError as e:
        logger.warning(f"Could not write run summary {path}: {e}")
