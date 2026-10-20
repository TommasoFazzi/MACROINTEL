"""Wiring of rag_search results into the Oracle agentic loop (diagnosis 2026-10-18).

Deterministic: Claude, T5 and the tools are mocked. Checks that the citation number a
document gets in Sonnet's context is the one the sidebar and the query log use, that
only rendered documents are citable, and that rag_search bypasses the free-text
summarizer.
"""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.llm.oracle_orchestrator import OracleOrchestrator
from src.llm.tools.base import ToolResult


def _chunk(article_id, title):
    return {"article_id": article_id, "title": title, "source": "War on the Rocks",
            "authority_score": 4.5, "published_date": datetime(2026, 8, 11),
            "content": f"Short text {article_id}.", "similarity": 0.6}


def _report(report_id):
    return {"id": report_id, "report_date": datetime(2026, 10, 12),
            "relevant_excerpt": "Briefing excerpt.", "status": "approved", "similarity": 0.4}


RAG_RESULTS = {
    "q-a": {"chunks": [_chunk(1, "Putting Armageddon on Autopilot"), _chunk(2, "Unmanned Nukes")],
            "reports": []},
    "q-b": {"chunks": [_chunk(2, "Unmanned Nukes"), _chunk(3, "New space wars")],
            "reports": [_report(900)]},
}


def _tool_use(block_id, name, tool_input):
    return SimpleNamespace(type="tool_use", id=block_id, name=name, input=tool_input)


@pytest.fixture
def run_query():
    """Run one Oracle query; return (result, tool_result texts, registry text, log metadata, summarize mock)."""
    claude = MagicMock()
    claude.generate_with_tools.side_effect = [
        SimpleNamespace(content=[
            _tool_use("t1", "rag_search", {"rationale": "r", "query": "q-a",
                                           "filters": {"time_decay_k": 0.005}}),
            _tool_use("t2", "rag_search", {"rationale": "r", "query": "q-b"}),
            _tool_use("t3", "sql_query", {"rationale": "r", "query": "SELECT 1"}),
        ]),
        SimpleNamespace(content=[SimpleNamespace(type="text", text="Answer [1] [3].")]),
    ]

    def fake_rag_execute(self, **kwargs):
        return ToolResult(success=True, data=RAG_RESULTS[kwargs["query"]], metadata={})

    db = MagicMock()
    with patch("src.llm.oracle_orchestrator.LLMFactory.get", side_effect=[claude, MagicMock()]), \
         patch.object(OracleOrchestrator, "_cleanup_expired_sessions", lambda self: None), \
         patch("src.llm.tools.rag_tool.RAGTool._execute", fake_rag_execute), \
         patch("src.llm.tools.sql_tool.SQLTool._execute",
               lambda self, **kw: ToolResult(success=True, data={"rows": []}, metadata={})), \
         patch("src.llm.tools.sql_tool.SQLTool.format_for_history", return_value="x" * 2000):
        orch = OracleOrchestrator(db=db)
        with patch.object(orch, "_summarize_for_history", return_value="sql summary") as summarize:
            result = orch.process_query(
                query="How will AI affect NATO nuclear deterrence?", session_id="s1",
                ui_filters={"start_date": datetime(2026, 1, 1)},
            )

    second_call_messages = claude.generate_with_tools.call_args_list[1].kwargs["messages"]
    tool_msg = next(m for m in second_call_messages
                    if m["role"] == "user" and isinstance(m["content"], list))
    tool_texts = {b["tool_use_id"]: b["content"] for b in tool_msg["content"] if b["type"] == "tool_result"}
    registry = next(b["text"] for b in tool_msg["content"] if b["type"] == "text")
    metadata = db.log_oracle_query.call_args.kwargs["metadata"]
    return result, tool_texts, registry, metadata, summarize


def _flatten(content):
    return content if isinstance(content, str) else "".join(b.get("text", "") for b in content)


@pytest.mark.unit
def test_shared_article_keeps_one_number(run_query):
    result, tool_texts, _, _, _ = run_query
    a, b = _flatten(tool_texts["t1"]), _flatten(tool_texts["t2"])
    assert "[1] ARTICOLO — War on the Rocks (4.5/5) — 11/08/2026 — Putting Armageddon on Autopilot" in a
    assert "[2] ARTICOLO" in a and "[2] ARTICOLO" in b           # article 2 reused its number
    assert "[3] ARTICOLO" in b and "[4] REPORT — Briefing interno #900" in b
    assert [(s["type"], s["id"]) for s in result["sources"]] == [
        ("ARTICOLO", 1), ("ARTICOLO", 2), ("ARTICOLO", 3), ("REPORT", 900)]


@pytest.mark.unit
def test_registry_matches_rendered_documents(run_query):
    result, tool_texts, registry, _, _ = run_query
    rendered = _flatten(tool_texts["t1"]) + _flatten(tool_texts["t2"])
    for n, src in enumerate(result["sources"], 1):
        assert f"[{n}] {src['title']}" in registry
        assert f"[{n}] {src['type']}" in rendered


@pytest.mark.unit
def test_rag_search_bypasses_free_text_summarizer(run_query):
    _, tool_texts, _, _, summarize = run_query
    assert [c.args[0] for c in summarize.call_args_list] == ["sql_query"]
    assert _flatten(tool_texts["t3"]) == "sql summary"


@pytest.mark.unit
def test_system_prompt_sourcing_rules():
    """D8: cite-or-omit, absence-claim and source-strength rules replace the old ones.

    The old "every fact MUST carry [N]" rule forced guessed numbers, and the fallback
    text invited "topic assente nel DB" claims with no check (query 285).
    """
    prompt = OracleOrchestrator._build_system_prompt(None)
    assert "REGOLA FONTI — CITA O OMETTI" in prompt
    assert "OMETTI il dettaglio" in prompt
    assert "REGOLA ASSENZA" in prompt
    assert "SOLO se nessun blocco restituito" in prompt
    assert "Rispetta la forza della fonte" in prompt
    assert "attribuiscila alla fonte" in prompt
    assert "topic assente nel DB" not in prompt
    assert "Non omettere mai le citazioni" not in prompt


@pytest.mark.unit
def test_query_log_records_retrieval_evidence(run_query):
    _, _, _, metadata, _ = run_query
    json.dumps(metadata)  # ui_filters inject datetimes — the row must stay serializable
    calls = metadata["rag_calls"]
    assert [c["query"] for c in calls] == ["q-a", "q-b"]
    assert calls[0]["time_decay_k"] == 0.005
    assert calls[1]["time_decay_k"] == 0.025          # default recorded when omitted
    assert calls[1]["n_chunks"] == 2 and calls[1]["n_reports_rendered"] == 1
    assert all(c["digest_mode"] == "verbatim" for c in calls)
    assert [s["n"] for s in metadata["sources"]] == [1, 2, 3, 4]
    assert metadata["sources"][0]["title"] == "Putting Armageddon on Autopilot"
