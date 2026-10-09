"""run_macro_first_pipeline() surfaces degraded signal extraction instead of swallowing it."""
from unittest.mock import MagicMock

import pytest

from src.llm.report_generator import ReportGenerator

pytestmark = pytest.mark.unit

_SIGNAL = {
    "ticker": "LMT", "signal": "BULLISH", "timeframe": "MEDIUM_TERM",
    "rationale": "Defense spending.", "confidence": 0.8, "supporting_themes": [],
}


def _generator(condense_ok=True, extract=None):
    gen = object.__new__(ReportGenerator)
    gen.generate_report = MagicMock(return_value={
        "success": True, "report_text": "Report body [Article 1].", "metadata": {},
        "sources": {"recent_articles": [], "historical_context": []},
    })
    gen.condense_macro_context = MagicMock(return_value=(
        {"success": True, "condensed": {"key_themes": []}, "token_estimate": 100}
        if condense_ok else {"success": False, "error": "boom", "condensed": None}
    ))
    gen.extract_macro_signals = MagicMock(
        return_value=extract or {"success": True, "signals": [_SIGNAL]}
    )
    gen.db = MagicMock()
    gen.db.save_report.return_value = 42
    gen._compute_and_save_report_embedding = MagicMock()
    gen.save_trade_signals = MagicMock(return_value={})
    return gen


def _run(gen):
    return gen.run_macro_first_pipeline(save=False, skip_article_signals=True)


def test_healthy_run_has_no_degraded_reasons():
    report = _run(_generator())
    assert report["success"] is True
    assert report["degraded_reasons"] == []


def test_extraction_failure_is_degraded_and_report_still_saved():
    gen = _generator(extract={"success": False, "error": "401 Unauthorized", "signals": []})
    report = _run(gen)
    assert report["success"] is True
    assert report["report_id"] == 42
    gen.db.save_report.assert_called_once()
    assert report["degraded_reasons"] == ["signals_extraction_failed"]


def test_zero_signals_is_degraded():
    report = _run(_generator(extract={"success": True, "signals": []}))
    assert report["degraded_reasons"] == ["signals_zero"]


def test_condensation_failure_is_degraded():
    report = _run(_generator(condense_ok=False))
    assert report["degraded_reasons"] == ["condensation_failed"]


def test_report_failure_is_not_degraded():
    gen = _generator()
    gen.generate_report.return_value = {"success": False, "error": "no articles"}
    report = _run(gen)
    assert report["success"] is False
    assert "degraded_reasons" not in report
    gen.db.save_report.assert_not_called()
