"""CitationVerifier with a mocked T3 client: no API calls."""
import json
from unittest.mock import MagicMock

import pytest

from src.llm.citation_verifier import JUDGE_SYSTEM, VERIFIER_VERSION, CitationVerifier
from src.llm.report_generator import ReportGenerator

pytestmark = pytest.mark.unit

CONTEXT = {
    "writer_path": "v2",
    "articles_in_prompt": [
        {"n": n, "source": "Reuters", "title": f"Title {n}", "excerpt": f"Excerpt {n}: X announced Y."}
        for n in range(1, 11)
    ],
    "storylines_in_prompt": [{"rank": 1, "storyline_id": 7, "title": "Red Sea", "summary": "Houthi attacks."}],
}


class FakeT3:
    """Answers the extraction call with `claims`, judge calls with `verdict_for(claim_text)`."""

    def __init__(self, claims, verdict_for=lambda claim: "SUPPORTED"):
        self.claims = claims
        self.verdict_for = verdict_for
        self.judge_prompts = []

    def generate(self, prompt, system=None, **kwargs):
        if system == JUDGE_SYSTEM:
            self.judge_prompts.append(prompt)
            blocks = prompt.split("### CLAIM ")[1:]
            verdicts = []
            for block in blocks:
                cid = int(block.split("\n", 1)[0])
                claim = block.split("CLAIM: ", 1)[1].split("\n", 1)[0]
                verdicts.append({"id": cid, "verdict": self.verdict_for(claim), "quote": "q", "reason": "r"})
            return json.dumps({"verdicts": verdicts})
        return json.dumps({"claims": self.claims})


def _claim(text, kind="event", a=(), s=()):
    return {"claim": text, "kind": kind, "article_refs": list(a), "storyline_refs": list(s)}


def test_supported_claim_not_flagged():
    t3 = FakeT3([_claim("X announced Y", a=[3])])
    result = CitationVerifier(client=t3).verify("X announced Y [Article 3].", CONTEXT)
    assert result.verdict_counts["SUPPORTED"] == 1
    assert result.flagged == []
    assert result.verifier_version == VERIFIER_VERSION
    assert "Excerpt 3: X announced Y." in t3.judge_prompts[0]  # evidence = exact excerpt


def test_specific_beyond_source_is_flagged():
    t3 = FakeT3([_claim("X announced a $4B deal", a=[2])], verdict_for=lambda c: "PARTIAL")
    result = CitationVerifier(client=t3).verify("...", CONTEXT)
    assert [f.verdict for f in result.flagged] == ["PARTIAL"]
    assert result.verdict_shares["PARTIAL"] == 1.0


def test_invalid_ref_not_judged():
    t3 = FakeT3([_claim("Z happened", a=[15])])
    result = CitationVerifier(client=t3).verify("Z happened [Article 15].", CONTEXT)
    assert result.invalid_refs == [15]
    assert t3.judge_prompts == []
    assert sum(result.verdict_counts.values()) == 0
    assert result.event_citation_coverage == 1.0  # it was cited, just wrongly


def test_storyline_ref_resolves_by_rank():
    t3 = FakeT3([_claim("Houthis attacked ships", s=[1]), _claim("Other", s=[9])])
    result = CitationVerifier(client=t3).verify("...", CONTEXT)
    assert "[Storyline 1] Red Sea. Houthi attacks." in t3.judge_prompts[0]
    assert result.invalid_storyline_refs == [9]


def test_inference_and_uncited_claims_not_judged():
    t3 = FakeT3([
        _claim("This likely signals escalation", kind="inference"),
        _claim("Brent at $81", kind="numeric"),
        _claim("Uncited event"),
        _claim("Cited event", a=[1]),
    ])
    result = CitationVerifier(client=t3).verify("...", CONTEXT)
    assert result.n_claims_by_kind == {"inference": 1, "numeric": 1, "event": 2}
    assert result.event_citation_coverage == 0.5
    assert sum(result.verdict_counts.values()) == 1


def test_batched_judging():
    t3 = FakeT3([_claim(f"Event {i}", a=[1 + i % 10]) for i in range(25)])
    result = CitationVerifier(client=t3).verify("...", CONTEXT)
    assert len(t3.judge_prompts) == 3  # 10 + 10 + 5
    assert sum(result.verdict_counts.values()) == 25


def test_flagged_ordering_and_cap():
    severity = {"0": "PARTIAL", "1": "CONTRADICTED", "2": "NOT_SUPPORTED"}
    t3 = FakeT3([_claim(f"Event {i}", a=[1]) for i in range(36)],
                verdict_for=lambda c: severity[str(int(c.split()[1]) % 3)])
    result = CitationVerifier(client=t3).verify("...", CONTEXT)
    assert len(result.flagged) == 30
    order = [f.verdict for f in result.flagged]
    assert order == sorted(order, key=["CONTRADICTED", "NOT_SUPPORTED", "PARTIAL"].index)


# ---------------------------------------------------------------------------
# wiring in run_macro_first_pipeline()
# ---------------------------------------------------------------------------

def _pipeline_generator():
    gen = object.__new__(ReportGenerator)
    gen.generate_report = MagicMock(return_value={
        "success": True, "report_text": "X announced Y [Article 1].", "metadata": {},
        "sources": {"recent_articles": []}, "generation_context": CONTEXT,
    })
    gen.condense_macro_context = MagicMock(return_value={"success": True, "condensed": {}})
    gen.extract_macro_signals = MagicMock(return_value={"success": True, "signals": [{"ticker": "LMT"}]})
    gen.db = MagicMock()
    gen.db.save_report.return_value = 1
    gen._compute_and_save_report_embedding = MagicMock()
    gen.save_trade_signals = MagicMock(return_value={})
    return gen


def test_verifier_metrics_saved_with_report(monkeypatch):
    monkeypatch.setattr("src.llm.citation_verifier.LLMFactory.get",
                        lambda tier, timeout=None: FakeT3([_claim("X announced Y", a=[1])]))
    gen = _pipeline_generator()
    report = gen.run_macro_first_pipeline(save=False, skip_article_signals=True)

    saved = gen.db.save_report.call_args[0][0]
    faith = saved["metadata"]["faithfulness"]
    assert faith["status"] == "ok"
    assert faith["verdict_counts"]["SUPPORTED"] == 1
    assert {"event_citation_coverage", "verdict_shares", "flagged"} <= faith.keys()
    assert report["degraded_reasons"] == []


def test_verifier_failure_is_degraded_but_report_saved(monkeypatch):
    def boom(tier, timeout=None):
        raise ValueError("401 Unauthorized")
    monkeypatch.setattr("src.llm.citation_verifier.LLMFactory.get", boom)
    gen = _pipeline_generator()
    report = gen.run_macro_first_pipeline(save=False, skip_article_signals=True)

    gen.db.save_report.assert_called_once()
    assert report["metadata"]["faithfulness"]["status"] == "error"
    assert "401" in report["metadata"]["faithfulness"]["error"]
    assert report["degraded_reasons"] == ["verifier_failed"]
