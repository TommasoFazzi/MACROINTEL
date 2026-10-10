"""generation_context: the writer's exact inputs, captured in-process (report-faithfulness-guardrails)."""
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.llm import report_generator as rg
from src.llm.report_generator import ReportGenerator

pytestmark = pytest.mark.unit


def _article(i, summary_len=800):
    return {
        "id": 100 + i, "link": f"https://ex.com/{i}", "title": f"Title {i}", "source": "Reuters",
        "published_date": datetime(2026, 10, 9, 6, 0), "category": "geo",
        "summary": f"S{i} " + "x" * summary_len, "full_text": f"F{i} " + "y" * 3000,
    }


_STORYLINE = {
    "id": 7, "rank": 1, "title": "Red Sea shipping", "summary": "z" * 900, "momentum": 0.9,
    "status": "active", "article_count": 5, "entities": [], "recent_articles": [],
}


# ---------------------------------------------------------------------------
# v2: _generate_strategic_report
# ---------------------------------------------------------------------------

def test_v2_context_has_top10_with_exact_excerpts():
    gen = object.__new__(ReportGenerator)
    gen._reasoning_model = MagicMock()
    gen._reasoning_model.generate.return_value = "report body"
    articles = [_article(i) for i in range(12)]

    with patch("src.macro.macro_regime_persistence.get_macro_regime_persistence_singleton",
               side_effect=RuntimeError("no db")):
        result = gen._generate_strategic_report(
            macro_analysis_json={}, articles=articles, storylines_xml="<s/>",
            target_date=date(2026, 10, 9), data_quality_flags=[],
        )

    assert result["success"]
    ctx_articles = result["articles_in_prompt"]
    assert [a["n"] for a in ctx_articles] == list(range(1, 11))
    assert [a["article_id"] for a in ctx_articles] == [100 + i for i in range(10)]
    # The excerpt is exactly what the prompt rendered for [Article N]
    for a in ctx_articles:
        assert len(a["excerpt"]) == 500
        assert f"[Article {a['n']}]" in result["user_prompt"]
        assert f"Summary:  {a['excerpt']}" in result["user_prompt"]
    assert "[Article 11]" not in result["user_prompt"]
    # user_prompt / system_prompt are the strings passed to the writer
    args, kwargs = gen._reasoning_model.generate.call_args
    assert args[0] == result["user_prompt"]
    assert kwargs["system"] == result["system_prompt"]


# ---------------------------------------------------------------------------
# v1: full generate_report() path
# ---------------------------------------------------------------------------

def _v1_generator(articles, storylines):
    gen = object.__new__(ReportGenerator)
    gen.db = MagicMock()
    gen.db.get_recent_articles.return_value = articles
    gen.enable_reranking = False
    gen.model = MagicMock(model_name="legacy-model")
    gen._reasoning_model = MagicMock(model_name="gemini-3.1-pro")
    gen._reasoning_model.generate_content_raw.return_value = "## 1. Executive Summary\nBody [Article 1]."
    gen.filter_relevant_articles = MagicMock(return_value=articles)
    gen.expand_rag_queries = MagicMock(return_value=[])
    gen.deduplicate_chunks_advanced = MagicMock(return_value=[])
    gen.format_rag_context = MagicMock(return_value="")
    gen._get_narrative_context = MagicMock(return_value={"storylines": storylines, "edges": []})
    gen._generate_report_title = MagicMock(return_value="")
    return gen


def test_v1_context_shape():
    articles = [_article(i) for i in range(3)]
    gen = _v1_generator(articles, [_STORYLINE])
    with patch.object(rg, "get_openbb_service", return_value=None):
        report = gen.generate_report(focus_areas=["geo"])

    ctx = report["generation_context"]
    assert ctx["writer_path"] == "v1"
    assert ctx["writer_model"] == "gemini-3.1-pro"
    assert ctx["user_prompt"] == gen._reasoning_model.generate_content_raw.call_args[0][0]
    assert [a["n"] for a in ctx["articles_in_prompt"]] == [1, 2, 3]
    first = ctx["articles_in_prompt"][0]
    assert first["excerpt"] == f"{articles[0]['summary']}\n\n{articles[0]['full_text'][:2000]}"
    assert articles[0]["full_text"][:2000] in ctx["user_prompt"]
    assert ctx["storylines_in_prompt"] == [
        {"rank": 1, "storyline_id": 7, "title": "Red Sea shipping", "summary": "z" * 500}]
    assert ctx["macro_snapshot"] is None
    # existing keys unchanged
    assert {"report_text", "metadata", "sources"} <= report.keys()


# ---------------------------------------------------------------------------
# cite-or-omit rule in both writer prompts
# ---------------------------------------------------------------------------

def test_cite_or_omit_rule_in_v2_prompt():
    from src.macro.strategic_intelligence_prompt import (
        CITE_OR_OMIT_RULE, build_strategic_intelligence_prompt)
    system, user = build_strategic_intelligence_prompt(
        {}, "<regime_history/>", "<strategic_storylines/>", [{"title": "t", "summary": "s"}],
        "2026-10-09", [])
    assert CITE_OR_OMIT_RULE in user
    assert 'rank="N"' in CITE_OR_OMIT_RULE and "[Storyline N]" in CITE_OR_OMIT_RULE
    # structure otherwise unchanged: the 7 sections and the articles block are still there
    for header in ("## Executive Summary", "## Key Developments", "## Strategic Storyline Tracker",
                   "=== TODAY'S OSINT ARTICLES", "=== MACRO-NEWS CROSS-VALIDATION RULES ==="):
        assert header in user


def test_cite_or_omit_rule_in_v1_prompt():
    from src.macro.strategic_intelligence_prompt import CITE_OR_OMIT_RULE
    gen = _v1_generator([_article(0)], [])
    with patch.object(rg, "get_openbb_service", return_value=None):
        gen.generate_report(focus_areas=["geo"])
    prompt = gen._reasoning_model.generate_content_raw.call_args[0][0]
    assert CITE_OR_OMIT_RULE in prompt
    assert "## 1. Executive Summary" in prompt


# ---------------------------------------------------------------------------
# macro snapshot
# ---------------------------------------------------------------------------

def test_macro_snapshot_is_json_safe_and_looks_back():
    svc = MagicMock()
    row = {"indicator_key": "BRENT", "value": Decimal("81.25"), "unit": "USD"}
    svc._get_macro_indicators.side_effect = lambda d: [row] if d == date(2026, 10, 8) else []

    snap = rg._capture_macro_snapshot(svc, date(2026, 10, 9))

    assert snap["requested_date"] == "2026-10-09"
    assert snap["target_date"] == "2026-10-08"
    assert snap["rows"] == [{"indicator_key": "BRENT", "value": 81.25, "unit": "USD"}]
    assert snap["captured_at"].endswith("+00:00")


def test_macro_snapshot_empty_when_no_data():
    svc = MagicMock()
    svc._get_macro_indicators.return_value = []
    snap = rg._capture_macro_snapshot(svc, date(2026, 10, 9))
    assert snap["rows"] == [] and snap["target_date"] is None
