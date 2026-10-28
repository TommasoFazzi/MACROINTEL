"""condense_macro_context(): schema-constrained call, one retry, raw output logged on failure."""
import json
from unittest.mock import MagicMock

import pytest

from src.llm.report_generator import (
    CONDENSE_TIMEOUT_S, MACRO_CONDENSED_SCHEMA, ReportGenerator,
)

pytestmark = pytest.mark.unit

VALID = json.dumps({
    "key_themes": ["Taiwan escalation risk", "Defense spending surge", "Oil supply shock"],
    "dominant_sentiment": "RISK_OFF",
    "priority_sectors": ["Defense"],
    "tickers_mentioned": ["LMT"],
    "geopolitical_hotspots": ["Taiwan Strait"],
    "time_horizon_focus": "SHORT_TERM",
})
# Shape of the 2026-10-28 failure: unescaped quote inside a theme.
MALFORMED = '{\n  "key_themes": [\n    "The "Fortress Europe" doctrine"\n  ]\n}'


def _generator(*texts):
    gen = object.__new__(ReportGenerator)
    gen.model = MagicMock()
    gen.model.generate_content.side_effect = [MagicMock(text=t) for t in texts]
    return gen


def test_sends_schema_and_timeout():
    gen = _generator(VALID)
    result = gen.condense_macro_context("report")
    assert result["success"] is True
    kwargs = gen.model.generate_content.call_args.kwargs
    assert kwargs["generation_config"]["response_schema"] is MACRO_CONDENSED_SCHEMA
    assert kwargs["request_options"] == {"timeout": CONDENSE_TIMEOUT_S}


def test_retries_once_after_malformed_json():
    gen = _generator(MALFORMED, VALID)
    result = gen.condense_macro_context("report")
    assert result["success"] is True
    assert result["condensed"]["dominant_sentiment"] == "RISK_OFF"
    assert gen.model.generate_content.call_count == 2


def test_fails_after_two_malformed_replies_and_logs_raw(caplog):
    gen = _generator(MALFORMED, MALFORMED)
    result = gen.condense_macro_context("report")
    assert result["success"] is False and result["condensed"] is None
    assert "json_invalid" in result["error"]
    assert "Fortress Europe" in caplog.text


def test_api_error_is_retried_without_raw_output():
    gen = object.__new__(ReportGenerator)
    gen.model = MagicMock()
    gen.model.generate_content.side_effect = [TimeoutError("read timeout"), MagicMock(text=VALID)]
    assert gen.condense_macro_context("report")["success"] is True


def test_schema_requires_every_model_field():
    from src.llm.schemas import MacroCondensedContext
    assert set(MACRO_CONDENSED_SCHEMA["required"]) == set(MacroCondensedContext.model_fields)
    assert set(MACRO_CONDENSED_SCHEMA["properties"]) == set(MacroCondensedContext.model_fields)
