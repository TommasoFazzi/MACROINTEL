"""
Oracle attribution evals (change oracle-rag-attribution-fix, diagnosis 2026-10-18).

SLOW EVAL (eval_slow), real models:
- T5 digest on a 6-document fixture: every document keeps its header and the digest
  does not fall back (a schema the SDK rejects would make every call fall back).
- Sonnet on query 285's text with a fixture where [1] is the War on the Rocks paper:
  the answer must cite it and must not claim the DB lacks analyses on AI and nuclear
  deterrence. Requires a real ANTHROPIC_API_KEY (the nightly workflow currently passes
  a fake one, so this case is skipped there).
"""

import os
import re
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tests.evals.conftest import skip_if_no_gemini_key

FIXTURE_DOCS = [
    ("Putting Armageddon on Autopilot: How Artificial Intelligence Could Make Nuclear Threats More Effective",
     "War on the Rocks", (2026, 8, 11),
     "For nuclear-armed states, bolstering their second-strike capabilities would be the surest way to avoid "
     "getting caught in an automated threat trap. Developing automated systems will be especially tempting for "
     "states that fear decapitation, as the Soviets did when they built Dead Hand. While Washington and Beijing "
     "have informally agreed not to automate nuclear decision-making, a legally binding agreement with more "
     "nuclear-armed powers would provide stronger safeguards. NATO countries should engage in wargaming to "
     "respond to automated nuclear threats."),
    ("The US and China Need to Talk About Unmanned Nukes", "Security - The Diplomat", (2026, 4, 12),
     "Uncertainties with AI, such as data poisoning and adversarial attacks, could lead to loss of control of "
     "autonomous weapon systems and a nuclear crisis. China and the US should reiterate the 2024 Lima agreement "
     "on maintaining human control of nuclear weapons and strengthen it with a ban on uploading nuclear weapons "
     "to autonomous systems."),
    ("North Korea's Risky Bet on Military AI", "ASEAN Beat - The Diplomat", (2026, 3, 25),
     "North Korea can improve the quality of its nuclear arsenal by using AI for target identification and "
     "early warning, which raises the risk of miscalculation on the peninsula."),
    ("The new space wars: lessons from Ukraine and the Middle East", "SpaceNews", (2026, 7, 31),
     "Jamming and spoofing of satellite navigation and attacks on ground stations have become routine in Ukraine "
     "and the Middle East, exposing how dependent militaries are on commercial and military space systems."),
    ("Pentagon names 4 directed energy weapons for counter-drone program", "Breaking Defense", (2026, 10, 6),
     "The Pentagon picked four directed-energy systems on October 5, 2026 for a one-year counter-drone pilot at "
     "U.S. bases: three lasers and Epirus' Leonidas high-powered microwave."),
    ("Kyber ransomware gang toys with post-quantum encryption", "BleepingComputer", (2026, 4, 22),
     "The Windows variant of the Kyber ransomware uses Kyber1024 (ML-KEM-1024) with X25519 to protect its own "
     "symmetric file keys; the ESXi variant advertises post-quantum encryption but uses RSA-4096."),
]

QUERY_285 = ("how will new technologies like AI, autonomous weapons, quantum computing, and space systems "
             "affect nuclear deterrence for nato?")


def _fixture_chunks():
    return [
        {"article_id": 1000 + i, "title": title, "source": source, "authority_score": 4.5,
         "published_date": datetime(*ymd), "content": text * 2, "similarity": 0.6}
        for i, (title, source, ymd, text) in enumerate(FIXTURE_DOCS)
    ]


def _has_real_anthropic_key() -> bool:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    return bool(key) and not key.startswith("ci-fake")


@pytest.mark.eval_slow
@skip_if_no_gemini_key
def test_real_t5_digest_keeps_every_document():
    from src.llm.llm_factory import LLMFactory
    from src.llm.rag_evidence import EvidenceDoc, digest_rag_result

    docs = [EvidenceDoc.from_chunk(c) for c in _fixture_chunks()]
    numbers = list(range(1, len(docs) + 1))
    result = digest_rag_result(docs, numbers, LLMFactory.get("t5"))

    assert result.mode == "llm", result.text
    assert len(result.fallback_docs) <= 1, result.fallback_docs
    for doc, n in zip(docs, numbers):
        assert doc.header(n) in result.text


ABSENCE_RE = re.compile(
    r"(non contiene|non sono (state )?trovat|nessuna analisi|assenza di analisi|assente nel (db|database))",
    re.IGNORECASE,
)
AI_RE = re.compile(r"(intelligenza artificiale|\bAI\b|\bIA\b|automazion)", re.IGNORECASE)


@pytest.mark.eval_slow
@skip_if_no_gemini_key
@pytest.mark.skipif(not _has_real_anthropic_key(), reason="ANTHROPIC_API_KEY reale non disponibile")
def test_sonnet_cites_dedicated_analysis_instead_of_denying_it():
    from src.llm.oracle_orchestrator import OracleOrchestrator
    from src.llm.tools.base import BaseTool, ToolResult
    from src.llm.tools.rag_tool import RAGTool

    def fake_rag(self, **kwargs):
        return ToolResult(success=True, data={"chunks": _fixture_chunks(), "reports": []}, metadata={})

    def unavailable(self, **kwargs):
        raise RuntimeError("tool not available in this eval")

    tool_classes = [c for c in BaseTool.__subclasses__() if c is not RAGTool]
    patches = [patch.object(c, "_execute", unavailable) for c in tool_classes]
    with patch.object(RAGTool, "_execute", fake_rag), \
         patch.object(OracleOrchestrator, "_cleanup_expired_sessions", lambda self: None):
        for p in patches:
            p.start()
        try:
            orch = OracleOrchestrator(db=MagicMock())
            result = orch.process_query(query=QUERY_285, session_id="eval-285")
        finally:
            for p in patches:
                p.stop()

    answer = result["answer"]
    cited = {int(n) for n in re.findall(r"\[(\d+)\]", answer)}
    assert 1 in cited, answer
    assert cited <= set(range(1, len(result["sources"]) + 1)), (cited, len(result["sources"]))
    denials = [s for s in re.split(r"(?<=[.!?])\s+", answer) if ABSENCE_RE.search(s) and AI_RE.search(s)]
    assert not denials, denials
