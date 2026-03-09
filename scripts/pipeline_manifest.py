#!/usr/bin/env python3
"""
Pipeline Manifest — deterministic file passing between pipeline steps.

Instead of each step globbing data/ and picking the most-recently-modified file,
the orchestrator creates a manifest and each step reads/writes to it.

When PIPELINE_MANIFEST_PATH is set:
  - Steps read their input file from the manifest
  - Steps write their output file to the manifest

When PIPELINE_MANIFEST_PATH is NOT set:
  - Scripts fall back to their original mtime-based discovery (backward compatible)

Manifest JSON structure:
{
    "run_id": "20260309_080000",
    "created_at": "2026-03-09T08:00:00",
    "steps": {
        "ingestion": {
            "output_file": "data/articles_20260309_080100.json",
            "completed_at": "2026-03-09T08:01:00",
            "article_count": 150
        },
        "nlp_processing": {
            "input_file": "data/articles_20260309_080100.json",
            "output_file": "data/articles_nlp_20260309_081500.json",
            "completed_at": "2026-03-09T08:15:00",
            "article_count": 142
        },
        "load_to_database": {
            "input_file": "data/articles_nlp_20260309_081500.json",
            "completed_at": "2026-03-09T08:16:00",
            "articles_saved": 140
        }
    }
}
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

# Project root — same convention as other scripts
PROJECT_ROOT = Path(__file__).parent.parent
MANIFESTS_DIR = PROJECT_ROOT / "data" / "manifests"


def get_manifest_path() -> Optional[Path]:
    """Return the manifest path from env var, or None if not set."""
    path = os.environ.get("PIPELINE_MANIFEST_PATH")
    if path:
        return Path(path)
    return None


def create_manifest(run_id: str) -> Path:
    """
    Create a new empty manifest for a pipeline run.

    Args:
        run_id: Unique identifier for this pipeline run (e.g. '20260309_080000')

    Returns:
        Path to the created manifest file
    """
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)

    manifest_path = MANIFESTS_DIR / f"run_{run_id}.json"
    manifest = {
        "run_id": run_id,
        "created_at": datetime.now().isoformat(),
        "steps": {}
    }

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    return manifest_path


def read_manifest(manifest_path: Optional[Path] = None) -> Optional[Dict]:
    """
    Read a manifest file.

    Args:
        manifest_path: Path to manifest. If None, reads from PIPELINE_MANIFEST_PATH env.

    Returns:
        Manifest dict, or None if no manifest path available.
    """
    if manifest_path is None:
        manifest_path = get_manifest_path()

    if manifest_path is None or not manifest_path.exists():
        return None

    with open(manifest_path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_step(
    step_name: str,
    data: Dict[str, Any],
    manifest_path: Optional[Path] = None
) -> bool:
    """
    Write step results to manifest.

    Args:
        step_name: Name of the pipeline step (e.g. 'ingestion', 'nlp_processing')
        data: Dict of step results (output_file, article_count, etc.)
        manifest_path: Path to manifest. If None, reads from PIPELINE_MANIFEST_PATH env.

    Returns:
        True if written, False if no manifest available.
    """
    if manifest_path is None:
        manifest_path = get_manifest_path()

    if manifest_path is None or not manifest_path.exists():
        return False

    manifest = read_manifest(manifest_path)
    if manifest is None:
        return False

    # Add completed_at timestamp
    data["completed_at"] = datetime.now().isoformat()

    manifest["steps"][step_name] = data

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    return True


def get_step_output(
    step_name: str,
    manifest_path: Optional[Path] = None
) -> Optional[str]:
    """
    Get the output file path from a previous step.

    Args:
        step_name: Name of the step whose output to retrieve
        manifest_path: Path to manifest. If None, reads from PIPELINE_MANIFEST_PATH env.

    Returns:
        Output file path string, or None if not available.
    """
    manifest = read_manifest(manifest_path)
    if manifest is None:
        return None

    step_data = manifest.get("steps", {}).get(step_name)
    if step_data is None:
        return None

    return step_data.get("output_file")


def cleanup_old_manifests(keep_days: int = 30):
    """Remove manifests older than keep_days."""
    if not MANIFESTS_DIR.exists():
        return

    import time
    cutoff = time.time() - (keep_days * 86400)

    for f in MANIFESTS_DIR.glob("run_*.json"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
