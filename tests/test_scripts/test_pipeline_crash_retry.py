"""daily_pipeline.py: one automatic retry when an idempotent step dies from a native crash."""
from unittest.mock import MagicMock

import pytest

import scripts.daily_pipeline as dp

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("code,crash", [(-6, True), (-11, True), (134, True), (139, True),
                                        (0, False), (1, False), (3, False), (-1, False)])
def test_is_native_crash(code, crash):
    assert dp.is_native_crash(code) is crash


def _pipeline(monkeypatch, exit_codes):
    p = object.__new__(dp.DailyPipeline)
    p.dry_run = False
    p.logger = MagicMock()
    calls = []

    def fake_execute(step):
        code = exit_codes[len(calls)]
        calls.append(step.name)
        return dp.StepResult(step_name=step.name, success=code == 0, exit_code=code,
                             stderr="free(): invalid pointer" if code else "")

    monkeypatch.setattr(p, "_execute_step", fake_execute)
    return p, calls


def _run_first_step(p):
    step = next(s for s in dp.DEFAULT_STEPS if s.name == "ingestion")
    return p._run_with_crash_retry(step, 1)


def test_ingestion_retried_once_after_crash(monkeypatch):
    p, calls = _pipeline(monkeypatch, [-6, 0])
    result = _run_first_step(p)
    assert result.success and calls == ["ingestion", "ingestion"]


def test_no_retry_on_ordinary_failure(monkeypatch):
    p, calls = _pipeline(monkeypatch, [1, 0])
    assert not _run_first_step(p).success and calls == ["ingestion"]


def test_gives_up_after_one_retry(monkeypatch):
    p, calls = _pipeline(monkeypatch, [-6, -6, 0])
    assert not _run_first_step(p).success and len(calls) == 2


def test_only_ingestion_has_crash_retry():
    assert {s.name for s in dp.DEFAULT_STEPS if s.retries_on_crash} == {"ingestion"}
