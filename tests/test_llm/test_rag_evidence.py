"""Tests for src/llm/rag_evidence — attributed rag_search digest (diagnosis 2026-10-18)."""

import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from src.llm.rag_evidence import (
    HEADER_ALLOWANCE,
    MAX_POINTS,
    RAG_DIGEST_SCHEMA,
    REPORT_BUDGET,
    EvidenceDoc,
    digest_rag_result,
    render_verbatim,
    select_context_documents,
)

WOTR_TITLE = "Putting Armageddon on Autopilot: How Artificial Intelligence Could Make Nuclear Threats More Effective"


def _chunk(article_id, title="Title", source="War on the Rocks", text=None, auth=4.5):
    return {
        "article_id": article_id,
        "title": title,
        "source": source,
        "authority_score": auth,
        "published_date": datetime(2026, 8, 11),
        "content": text if text is not None else f"Sentence about article {article_id}. " * 20,
        "similarity": 0.6,
        "link": f"https://example.com/{article_id}",
    }


def _report(report_id, excerpt_len=5000):
    return {
        "id": report_id,
        "report_date": datetime(2026, 10, 12),
        "relevant_excerpt": "R" * excerpt_len,
        "final_content": "full",
        "status": "approved",
        "similarity": 0.5,
    }


def _llm_returning(payload):
    llm = MagicMock()
    llm.generate.return_value = json.dumps(payload)
    return llm


# ── Selection ─────────────────────────────────────────────────────────────────

class TestSelectContextDocuments:

    def test_articles_first_then_reports_within_budget(self):
        data = {"chunks": [_chunk(1), _chunk(2)], "reports": [_report(i) for i in range(10)]}
        docs = select_context_documents(data, max_chars=50000)
        kinds = [d.kind for d in docs]
        assert kinds[:2] == ["ARTICOLO", "ARTICOLO"]
        # 10 reports returned, only those fitting REPORT_BUDGET are selected (~2)
        n_reports = kinds.count("REPORT")
        assert n_reports == REPORT_BUDGET // (5000 + HEADER_ALLOWANCE)
        assert n_reports < 10

    def test_whole_documents_only_under_char_budget(self):
        big = [_chunk(i, text="x" * 9000) for i in range(10)]   # ~93k chars total
        docs = select_context_documents({"chunks": big, "reports": []}, max_chars=50000)
        assert sum(len(d.text) + HEADER_ALLOWANCE for d in docs) <= 50000
        assert all(len(d.text) == 9000 for d in docs)          # never truncated
        assert [d.id for d in docs] == list(range(len(docs)))  # rank order kept

    def test_rag_tool_rendering_never_truncated_by_history_limit(self):
        from src.llm.tools.rag_tool import RAGTool
        tool = RAGTool(db=MagicMock())
        data = {"chunks": [_chunk(i, text="y" * 9000) for i in range(10)], "reports": []}
        rendered = tool._format_success(data, {})
        assert len(rendered) <= tool.HISTORY_MAX_CHARS
        assert "chars omitted" not in tool.format_for_history(MagicMock(success=True, data=data, metadata={}))


# ── Headers ──────────────────────────────────────────────────────────────────

class TestHeaders:

    def test_article_header(self):
        doc = EvidenceDoc.from_chunk(_chunk(7, title=WOTR_TITLE))
        assert doc.header(7) == f"[7] ARTICOLO — War on the Rocks (4.5/5) — 11/08/2026 — {WOTR_TITLE}"

    def test_article_header_without_authority(self):
        doc = EvidenceDoc.from_chunk(_chunk(7, source="FDD", auth=None))
        assert doc.header(2).startswith("[2] ARTICOLO — FDD — 11/08/2026")

    def test_report_header(self):
        doc = EvidenceDoc.from_report(_report(812))
        assert doc.header(3) == "[3] REPORT — Briefing interno #812 — 12/10/2026"

    def test_source_entry_keeps_sidebar_fields(self):
        src = EvidenceDoc.from_chunk(_chunk(5)).to_source()
        assert {"type", "id", "title", "date_str", "source", "similarity", "preview", "link"} <= set(src)
        assert src["type"] == "ARTICOLO" and src["id"] == 5


# ── Digest ───────────────────────────────────────────────────────────────────

class TestDigestRagResult:

    def _docs(self, n=3):
        # ~1100 chars each, so even 2 documents exceed the 1500-char verbatim threshold
        docs = [EvidenceDoc.from_chunk(_chunk(i, title=f"T{i}", text=f"Fact {i}. " * 140)) for i in range(n)]
        return docs, [10 + i for i in range(n)]

    def test_llm_points_under_code_headers(self):
        docs, numbers = self._docs()
        llm = _llm_returning({"documents": [{"n": n, "points": [f"fact {n}"]} for n in numbers]})
        result = digest_rag_result(docs, numbers, llm)
        assert result.mode == "llm"
        assert result.fallback_docs == []
        for doc, n in zip(docs, numbers):
            assert f"{doc.header(n)}\n- fact {n}" in result.text
        kwargs = llm.generate.call_args.kwargs
        assert kwargs["response_schema"] is RAG_DIGEST_SCHEMA
        assert kwargs["max_tokens"] == min(4096, 256 + 160 * len(docs))

    def test_unknown_number_discarded(self):
        docs, numbers = self._docs(2)
        llm = _llm_returning({"documents": [{"n": numbers[0], "points": ["a"]},
                                            {"n": numbers[1], "points": ["b"]},
                                            {"n": 999, "points": ["invented"]}]})
        result = digest_rag_result(docs, numbers, llm)
        assert "[999]" not in result.text
        assert "invented" not in result.text

    def test_points_capped(self):
        docs, numbers = self._docs(1)
        docs = [EvidenceDoc.from_chunk(_chunk(0, text="z" * 3000))]
        llm = _llm_returning({"documents": [{"n": numbers[0], "points": [f"p{i}" for i in range(7)]}]})
        result = digest_rag_result(docs, numbers[:1], llm)
        assert result.text.count("\n- ") == MAX_POINTS

    def test_missing_document_gets_snippet(self):
        docs, numbers = self._docs(2)
        llm = _llm_returning({"documents": [{"n": numbers[0], "points": ["a"]}]})
        result = digest_rag_result(docs, numbers, llm)
        assert result.mode == "llm"
        assert result.fallback_docs == [numbers[1]]
        assert f"{docs[1].header(numbers[1])}\n(estratto) «" in result.text

    def test_t5_failure_falls_back_with_all_headers(self):
        docs, numbers = self._docs()
        llm = MagicMock()
        llm.generate.side_effect = TimeoutError("15s")
        result = digest_rag_result(docs, numbers, llm)
        assert result.mode == "fallback"
        assert result.fallback_docs == numbers
        for doc, n in zip(docs, numbers):
            assert doc.header(n) in result.text

    def test_invalid_json_falls_back(self):
        docs, numbers = self._docs()
        llm = MagicMock()
        llm.generate.return_value = '{"documents": "nope"'
        assert digest_rag_result(docs, numbers, llm).mode == "fallback"

    def test_short_result_verbatim_without_t5(self):
        docs = [EvidenceDoc.from_chunk(_chunk(1, text="Short text."))]
        llm = MagicMock()
        result = digest_rag_result(docs, [4], llm)
        assert result.mode == "verbatim"
        assert result.text == render_verbatim(docs, [4])
        llm.generate.assert_not_called()

    def test_empty_result(self):
        result = digest_rag_result([], [], MagicMock())
        assert result.mode == "verbatim"
        assert "nessun documento" in result.text


# ── Schema guard ─────────────────────────────────────────────────────────────

def test_schema_accepted_by_gemini_sdk():
    """google-generativeai 0.8.5 rejects Pydantic constraint keywords (maxItems)."""
    generation_types = pytest.importorskip("google.generativeai.types.generation_types")
    generation_types.to_generation_config_dict(
        {"response_mime_type": "application/json", "response_schema": RAG_DIGEST_SCHEMA}
    )
    # The real-model counterpart lives in tests/evals/test_oracle_attribution.py (nightly).
