"""Degraded exit path: generate_report.py exits 3, daily_pipeline.py keeps delivering and ends red."""
import logging
import sys
from unittest.mock import MagicMock

import pytest

import scripts.daily_pipeline as dp
import scripts.generate_report as gr

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# scripts/generate_report.py --macro-first exit codes
# ---------------------------------------------------------------------------

def _run_generate_report(monkeypatch, pipeline_result):
    generator = MagicMock()
    generator.run_macro_first_pipeline.return_value = pipeline_result
    monkeypatch.setattr(gr, "ReportGenerator", MagicMock(return_value=generator))
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(sys, "argv", ["generate_report.py", "--macro-first", "--skip-article-signals"])
    return gr.main()


def _report(**extra):
    return {"success": True, "report_text": "body", "report_signals": [], "article_signals": [], **extra}


def test_generate_report_exit_0_when_healthy(monkeypatch):
    assert _run_generate_report(monkeypatch, _report(degraded_reasons=[])) == 0


def test_generate_report_exit_3_when_degraded(monkeypatch, caplog):
    with caplog.at_level(logging.ERROR):
        code = _run_generate_report(monkeypatch, _report(degraded_reasons=["signals_zero"]))
    assert code == gr.EXIT_DEGRADED == 3
    assert any("signals_zero" in r.getMessage() and r.levelno == logging.ERROR for r in caplog.records)


def test_generate_report_exit_1_on_hard_failure(monkeypatch):
    assert _run_generate_report(monkeypatch, {"success": False, "error": "no articles"}) == 1


# ---------------------------------------------------------------------------
# scripts/daily_pipeline.py handling of exit 3
# ---------------------------------------------------------------------------

def _step(name, code, continue_on_failure=False):
    return dp.PipelineStep(
        name=name,
        command=f'"{sys.executable}" -c "import sys; sys.exit({code})"',
        description=name,
        timeout_seconds=30,
        continue_on_failure=continue_on_failure,
    )


@pytest.fixture
def pipeline_factory(tmp_path, monkeypatch):
    for d in ("src", "scripts", "data", "logs"):
        (tmp_path / d).mkdir()
    (tmp_path / ".env").write_text("")
    monkeypatch.setattr(dp, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(dp, "create_manifest", lambda run_id: tmp_path / "manifest.json")
    monkeypatch.setattr(dp, "cleanup_old_manifests", lambda keep_days: None)
    monkeypatch.setattr(dp.DailyPipeline, "_send_notification", lambda self, result: None)

    def make(steps):
        pipeline = dp.DailyPipeline(steps=steps)
        pipeline._run_conditional_steps = MagicMock()
        return pipeline
    return make


def test_degraded_report_still_delivers_and_ends_red(pipeline_factory):
    pipeline = pipeline_factory([
        _step("generate_report", dp.EXIT_DEGRADED),
        _step("generate_romania_report", 0, continue_on_failure=True),
        _step("send_report_email", 0, continue_on_failure=True),
    ])
    result = pipeline.run()

    assert [r.step_name for r in result.step_results] == [
        "generate_report", "generate_romania_report", "send_report_email"]
    assert result.step_results[0].success and result.step_results[0].degraded
    assert result.degraded_steps == ["generate_report"]
    assert result.success is False
    assert result.error is None
    assert dp.DailyPipeline._pipeline_status(result) == "DEGRADED"
    pipeline._run_conditional_steps.assert_called_once()  # weekly/monthly still run


def test_healthy_run_unchanged(pipeline_factory):
    pipeline = pipeline_factory([_step("generate_report", 0), _step("send_report_email", 0)])
    result = pipeline.run()
    assert result.success is True
    assert result.degraded_steps == []


def test_hard_failure_still_stops(pipeline_factory):
    pipeline = pipeline_factory([
        _step("generate_report", 1),
        _step("send_report_email", 0, continue_on_failure=True),
    ])
    result = pipeline.run()
    assert [r.step_name for r in result.step_results] == ["generate_report"]
    assert result.success is False
    assert dp.DailyPipeline._pipeline_status(result) == "FAILED"
    pipeline._run_conditional_steps.assert_not_called()


def test_main_exits_1_when_degraded(monkeypatch):
    fake = MagicMock()
    fake.run.return_value = dp.PipelineResult(
        run_id="x", success=False, total_duration=0, steps_completed=1, steps_total=1,
        step_results=[dp.StepResult("generate_report", True, 3, degraded=True)])
    monkeypatch.setattr(dp, "DailyPipeline", MagicMock(return_value=fake))
    monkeypatch.setattr(sys, "argv", ["daily_pipeline.py"])
    assert dp.main() == 1
