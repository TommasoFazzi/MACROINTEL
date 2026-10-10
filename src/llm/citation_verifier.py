"""
Post-generation citation verifier for daily reports (report-faithfulness-guardrails).

Judges every cited event claim against EXACTLY the evidence the writer saw,
taken from report['generation_context'] (see report_generator.py):
  [Article N]   -> articles_in_prompt[n=N].excerpt
  [Storyline N] -> storylines_in_prompt[rank=N]

Two kinds of T3 (DeepSeek) call per report — a different vendor from the T1
Gemini writer, so the writer never grades itself:
  1. extraction: up to 40 claims with kind + citation markers (one call)
  2. judging: event claims with resolvable refs, ~10 per call

Prompts are ported from the 2026-10 audit scripts, whose judge agreed with
100 human labels 88% of the time (kappa 0.83) and over-calls CONTRADICTED.
Verdicts are advisory: this module never modifies the report text.
"""

import json
from collections import Counter
from typing import Any, Dict, List, Tuple

from pydantic import ValidationError

from .llm_factory import LLMFactory
from .schemas import ClaimVerdict, ExtractedClaim, FaithfulnessReport
from ..utils.logger import get_logger

logger = get_logger(__name__)

# Bump when prompts or scoring change, so metrics stay comparable over time.
VERIFIER_VERSION = "1.0"

_MAX_REPORT_CHARS = 30000
_MAX_CLAIMS = 40
_JUDGE_BATCH_SIZE = 10
_MAX_REFS_PER_CLAIM = 3
_MAX_FLAGGED = 30
_T3_TIMEOUT_S = 180  # extraction emits up to ~4k tokens; the tier default (45s) is too short

_VERDICTS = ("SUPPORTED", "PARTIAL", "NOT_SUPPORTED", "CONTRADICTED")
_FLAG_ORDER = {"CONTRADICTED": 0, "NOT_SUPPORTED": 1, "PARTIAL": 2}

EXTRACT_SYSTEM = """You extract verifiable factual claims from an intelligence briefing and the citation markers attached to each one.
Return JSON {"claims":[{"claim":str,"kind":"event"|"numeric"|"inference","article_refs":[int],"storyline_refs":[int]}]}.
- event: a concrete statement that something happened or was said/announced; numeric: market price/percentage/index level; inference: analyst interpretation or forecast (extract but do not worry about it).
- article_refs: the numbers N of every "[Article N]" / "[Article N, M]" marker attached to THAT sentence (same sentence or immediately after it). Copy exactly; use [] if the sentence has none. Do NOT borrow markers from other sentences.
- storyline_refs: the numbers of "[Storyline N]" markers attached to that sentence ([] if none).
Rules: claim must be self-contained (resolve pronouns, include names), max 35 words. Skip headings and generic background. Extract at most 40 claims, prioritising specific event claims."""

JUDGE_SYSTEM = """You are a strict fact-checker. You get several numbered CLAIMS from an intelligence briefing; each comes with the CITED ITEM(S) it points to, exactly as the writer saw them. Judge each claim ONLY against its own cited items.
Return JSON {"verdicts":[{"id":int,"verdict":"SUPPORTED"|"PARTIAL"|"NOT_SUPPORTED"|"CONTRADICTED","quote":str,"reason":str}]} with one entry per claim id.
SUPPORTED: every specific of the claim (actors, action, numbers, places, dates) is stated in the cited item(s). PARTIAL: the core is stated but some specifics are missing or stronger than the source.
NOT_SUPPORTED: the cited item(s) do not contain the claim. CONTRADICTED: they say something incompatible (different number, different actor, opposite).
Be literal, do not use outside knowledge. quote = shortest deciding phrase (empty if none). reason = one sentence."""


def _int_refs(values: Any) -> List[int]:
    """Keep integer-like refs only; the extractor sometimes returns strings."""
    out = []
    for v in values or []:
        if isinstance(v, int) or (isinstance(v, str) and v.strip().isdigit()):
            out.append(int(v))
    return out


class CitationVerifier:
    """Checks [Article N] / [Storyline N] claims of a daily report against the writer's context."""

    def __init__(self, client=None):
        self._client = client or LLMFactory.get("t3", timeout=_T3_TIMEOUT_S)

    # -- public ---------------------------------------------------------------

    def verify(self, report_text: str, generation_context: Dict[str, Any]) -> FaithfulnessReport:
        """Extract claims, judge the cited event claims, return per-report metrics.

        Raises on T3 or JSON failure: the caller records the verifier as failed.
        """
        articles = {a["n"]: a for a in generation_context.get("articles_in_prompt", [])}
        storylines = {s["rank"]: s for s in generation_context.get("storylines_in_prompt", [])}

        claims = self._extract(report_text)
        events = [c for c in claims if c.kind == "event"]

        invalid_articles: set = set()
        invalid_storylines: set = set()
        judgeable: List[Tuple[ExtractedClaim, str]] = []
        for claim in events:
            evidence, bad_a, bad_s = self._evidence(claim, articles, storylines)
            invalid_articles |= bad_a
            invalid_storylines |= bad_s
            if evidence:
                judgeable.append((claim, evidence))

        verdicts: List[ClaimVerdict] = []
        for start in range(0, len(judgeable), _JUDGE_BATCH_SIZE):
            verdicts.extend(self._judge(judgeable[start:start + _JUDGE_BATCH_SIZE]))

        cited = sum(1 for c in events if c.article_refs or c.storyline_refs)
        counts = Counter(v.verdict for v in verdicts)
        n_judged = len(verdicts)
        flagged = sorted(
            (v for v in verdicts if v.verdict != "SUPPORTED"),
            key=lambda v: _FLAG_ORDER[v.verdict],
        )[:_MAX_FLAGGED]

        return FaithfulnessReport(
            verifier_version=VERIFIER_VERSION,
            writer_path=generation_context.get("writer_path", "v2"),
            n_claims_by_kind=dict(Counter(c.kind for c in claims)),
            event_citation_coverage=round(cited / len(events), 4) if events else 0.0,
            invalid_refs=sorted(invalid_articles),
            invalid_storyline_refs=sorted(invalid_storylines),
            verdict_counts={v: counts.get(v, 0) for v in _VERDICTS},
            verdict_shares={
                v: round(counts.get(v, 0) / n_judged, 4) if n_judged else 0.0 for v in _VERDICTS
            },
            flagged=flagged,
        )

    # -- steps ----------------------------------------------------------------

    def _call(self, system: str, user: str, max_tokens: int) -> dict:
        raw = self._client.generate(
            user, system=system, temperature=0.0, max_tokens=max_tokens, json_mode=True
        )
        return json.loads(raw)

    def _extract(self, report_text: str) -> List[ExtractedClaim]:
        data = self._call(EXTRACT_SYSTEM, report_text[:_MAX_REPORT_CHARS], max_tokens=4500)
        claims = []
        for item in (data.get("claims") or [])[:_MAX_CLAIMS]:
            if not isinstance(item, dict) or not item.get("claim"):
                continue
            try:
                claims.append(ExtractedClaim(
                    claim=item["claim"],
                    kind=item.get("kind"),
                    article_refs=_int_refs(item.get("article_refs")),
                    storyline_refs=_int_refs(item.get("storyline_refs")),
                ))
            except ValidationError:
                logger.debug(f"Skipping malformed claim: {item!r}")
        return claims

    @staticmethod
    def _evidence(claim: ExtractedClaim, articles: dict, storylines: dict) -> Tuple[str, set, set]:
        """Evidence block for a claim + the refs that resolve to nothing in the prompt."""
        blocks, bad_articles, bad_storylines = [], set(), set()
        for n in claim.article_refs[:_MAX_REFS_PER_CLAIM]:
            a = articles.get(n)
            if a is None:
                bad_articles.add(n)
                continue
            blocks.append(f"[Article {n}] ({a.get('source') or 'n/a'}) {a.get('title') or ''}. {a.get('excerpt') or ''}")
        for n in claim.storyline_refs[:_MAX_REFS_PER_CLAIM]:
            s = storylines.get(n)
            if s is None:
                bad_storylines.add(n)
                continue
            blocks.append(f"[Storyline {n}] {s.get('title') or ''}. {s.get('summary') or ''}")
        return "\n\n".join(blocks), bad_articles, bad_storylines

    def _judge(self, batch: List[Tuple[ExtractedClaim, str]]) -> List[ClaimVerdict]:
        user = "\n\n".join(
            f"### CLAIM {i}\nCLAIM: {claim.claim}\nCITED ITEMS:\n{evidence}"
            for i, (claim, evidence) in enumerate(batch, 1)
        )
        data = self._call(JUDGE_SYSTEM, user, max_tokens=250 * len(batch) + 200)
        by_id = {}
        for item in data.get("verdicts") or []:
            if isinstance(item, dict) and str(item.get("id", "")).isdigit():
                by_id[int(item["id"])] = item

        verdicts = []
        for i, (claim, _) in enumerate(batch, 1):
            item = by_id.get(i)
            if item is None or item.get("verdict") not in _VERDICTS:
                logger.warning(f"Judge returned no valid verdict for claim {i}: {claim.claim[:80]}")
                continue
            verdicts.append(ClaimVerdict(
                claim=claim.claim,
                article_refs=claim.article_refs,
                storyline_refs=claim.storyline_refs,
                verdict=item["verdict"],
                quote=str(item.get("quote") or ""),
                reason=str(item.get("reason") or ""),
            ))
        return verdicts
