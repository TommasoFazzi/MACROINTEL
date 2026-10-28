"""report_run_warning writes to the GitHub run summary and never raises."""
import pytest

from src.utils.run_summary import report_run_warning

pytestmark = pytest.mark.unit


def test_line_appended_to_step_summary(tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    summary.write_text("existing\n")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    report_run_warning("Stale source", "CSIS: 109 days")

    lines = summary.read_text().splitlines()
    assert lines[0] == "existing"
    assert "Stale source" in lines[1] and "CSIS: 109 days" in lines[1]


def test_unset_only_logs(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.chdir(tmp_path)

    report_run_warning("Fallback", "24h filter")

    assert "Fallback: 24h filter" in caplog.text
    assert list(tmp_path.iterdir()) == []


def test_unwritable_path_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "missing-dir" / "summary.md"))
    report_run_warning("Fallback", "24h filter")
