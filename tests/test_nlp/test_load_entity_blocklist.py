"""Test for narrative_processor._load_entity_blocklist.

Verifies that only `media_artifacts` is loaded — other sections
(generic_terms, noise, numbers_years) are intentionally excluded.
"""

import textwrap
from pathlib import Path

from src.nlp.narrative_processor import _load_entity_blocklist


def _write_yaml(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "entity_blocklist.yaml"
    path.write_text(textwrap.dedent(body).strip() + "\n", encoding="utf-8")
    return path


def test_loads_only_media_artifacts(tmp_path: Path) -> None:
    """The loader SHALL load only `media_artifacts`, lower-cased."""
    path = _write_yaml(tmp_path, """
        media_artifacts:
          - "Reuters"
          - "Tass"
          - "Bloomberg"

        generic_terms:
          - "President"
          - "Ministry"

        noise:
          - "etcetera"
    """)
    blocklist = _load_entity_blocklist(path)

    assert "reuters" in blocklist
    assert "tass" in blocklist
    assert "bloomberg" in blocklist
    # generic_terms section must NOT be loaded in Phase 1
    assert "president" not in blocklist
    assert "ministry" not in blocklist
    # noise section must NOT be loaded
    assert "etcetera" not in blocklist


def test_returns_empty_set_if_file_missing(tmp_path: Path) -> None:
    """If the file does not exist, return set() (warning logged)."""
    missing = tmp_path / "nope.yaml"
    blocklist = _load_entity_blocklist(missing)
    assert blocklist == set()


def test_returns_empty_set_if_yaml_malformed(tmp_path: Path) -> None:
    """Unparseable YAML SHALL return set() and not raise."""
    path = tmp_path / "broken.yaml"
    path.write_text("this is :: not valid: yaml: [broken", encoding="utf-8")
    blocklist = _load_entity_blocklist(path)
    assert blocklist == set()


def test_handles_missing_media_artifacts_section(tmp_path: Path) -> None:
    """If `media_artifacts` section absent, return empty set (no KeyError)."""
    path = _write_yaml(tmp_path, """
        generic_terms:
          - "President"
    """)
    blocklist = _load_entity_blocklist(path)
    assert blocklist == set()


def test_skips_empty_or_null_entries(tmp_path: Path) -> None:
    """Empty strings or null YAML entries SHALL be skipped."""
    path = _write_yaml(tmp_path, """
        media_artifacts:
          - "Reuters"
          - ""
          - null
          - "Tass"
    """)
    blocklist = _load_entity_blocklist(path)
    assert blocklist == {"reuters", "tass"}
