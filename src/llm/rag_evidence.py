"""Attributed evidence for Oracle `rag_search` results.

What Sonnet receives from a `rag_search` call is built here, not by a free-text LLM
summary. Every document block starts with a header written by code — global citation
number, type, source, date, title — so attribution can never be lost in condensation.
T5 (Flash-Lite) only writes the per-document points, keyed by citation number.

Diagnosed 2026-10-18 (oracle_query_log id 285): the previous free-text summary kept
the content but dropped titles, sources and document numbers, so Sonnet denied that
sources existed and guessed citation numbers.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, ValidationError

from ..utils.logger import get_logger

logger = get_logger(__name__)

VERBATIM_MAX_CHARS = 1500     # rendered result at or below this goes to Sonnet as-is
REPORT_BUDGET = 12000         # max chars of report text per call (~2 reports)
REPORT_EXCERPT_CHARS = 5000   # head-truncation fallback when no relevant_excerpt
HEADER_ALLOWANCE = 300        # chars reserved per document for header + framing
MAX_POINTS = 4
SNIPPET_CHARS = 400

# Plain JSON-schema dict for Gemini. google-generativeai 0.8.5 rejects Pydantic
# constraint keywords (`maxItems`), so limits are enforced in the prompt and in code.
RAG_DIGEST_SCHEMA = {
    "type": "object",
    "properties": {
        "documents": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "points": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["n", "points"],
            },
        },
    },
    "required": ["documents"],
}


class DigestEntry(BaseModel):
    n: int
    points: List[str]


class RagDigest(BaseModel):
    documents: List[DigestEntry]


def _date_str(value: Any) -> str:
    if hasattr(value, "strftime"):
        return value.strftime("%d/%m/%Y")
    return str(value)[:10] if value else "N/A"


@dataclass
class EvidenceDoc:
    """One document whose full text is rendered into Sonnet's context."""

    kind: str                 # "ARTICOLO" | "REPORT"
    id: Any                   # article_id or report id
    title: str
    date_str: str
    text: str
    source: Optional[str] = None
    authority: Optional[float] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def key(self) -> Tuple[str, Any]:
        return (self.kind, self.id)

    @classmethod
    def from_chunk(cls, chunk: Dict[str, Any]) -> "EvidenceDoc":
        auth = chunk.get("authority_score")
        return cls(
            kind="ARTICOLO",
            id=chunk.get("article_id"),
            title=chunk.get("title") or "Articolo senza titolo",
            date_str=_date_str(chunk.get("published_date")),
            text=chunk.get("content", ""),
            source=chunk.get("source") or "Unknown",
            authority=float(auth) if auth is not None else None,
            raw=chunk,
        )

    @classmethod
    def from_report(cls, report: Dict[str, Any]) -> "EvidenceDoc":
        text = report.get("relevant_excerpt") or (
            report.get("final_content") or report.get("draft_content", "")
        )[:REPORT_EXCERPT_CHARS]
        date_str = _date_str(report.get("report_date"))
        return cls(
            kind="REPORT",
            id=report.get("id"),
            title=f"Report #{report.get('id')} - {date_str}",
            date_str=date_str,
            text=text,
            raw=report,
        )

    def header(self, n: int) -> str:
        if self.kind == "REPORT":
            return f"[{n}] REPORT — Briefing interno #{self.id} — {self.date_str}"
        auth = f" ({self.authority:.1f}/5)" if self.authority is not None else ""
        return f"[{n}] ARTICOLO — {self.source}{auth} — {self.date_str} — {self.title}"

    def to_source(self) -> Dict[str, Any]:
        """Entry for the response `sources` list (sidebar). Field set unchanged."""
        r = self.raw
        entry = {
            "type": self.kind,
            "id": self.id,
            "title": self.title,
            "date_str": self.date_str,
            "similarity": r.get("similarity", 0),
            "similarity_raw": r.get("similarity_raw"),
            "time_decay_factor": r.get("time_decay_factor"),
            "days_old": r.get("days_old"),
        }
        if self.kind == "REPORT":
            entry["status"] = r.get("status", "draft")
            entry["preview"] = (r.get("final_content") or r.get("draft_content", ""))[:200]
        else:
            entry["source"] = self.source
            entry["preview"] = self.text[:200]
            entry["link"] = r.get("link")
        return entry


def select_context_documents(data: Dict[str, Any], max_chars: int) -> List[EvidenceDoc]:
    """The single source of what a `rag_search` call puts in Sonnet's context.

    Chunks in rank order, then reports within REPORT_BUDGET, whole documents only and
    at most `max_chars` in total. A document that does not fit is excluded, never cut,
    so the documents registered as citable are exactly the ones Sonnet reads.
    """
    selected: List[EvidenceDoc] = []
    total = 0
    for chunk in data.get("chunks", []):
        doc = EvidenceDoc.from_chunk(chunk)
        size = len(doc.text) + HEADER_ALLOWANCE
        if total + size > max_chars:
            break
        selected.append(doc)
        total += size

    report_total = 0
    for report in data.get("reports", []):
        doc = EvidenceDoc.from_report(report)
        size = len(doc.text) + HEADER_ALLOWANCE
        if report_total + size > REPORT_BUDGET or total + size > max_chars:
            break
        selected.append(doc)
        report_total += size
        total += size
    return selected


def render_verbatim(docs: Sequence[EvidenceDoc], numbers: Sequence[int]) -> str:
    """Header + full text per document (no condensation)."""
    if not docs:
        return "[RAG: nessun documento trovato]"
    return "\n\n".join(f"{doc.header(n)}\n{doc.text}" for doc, n in zip(docs, numbers))


def _snippet(text: str) -> str:
    """First ~SNIPPET_CHARS chars, cut at a sentence boundary when one is close."""
    text = " ".join(text.split())
    if len(text) <= SNIPPET_CHARS:
        return text
    head = text[:SNIPPET_CHARS]
    cut = head.rfind(". ")
    if cut >= SNIPPET_CHARS // 3:
        return head[:cut + 1]
    return head[:head.rfind(" ")] + " …"


def build_digest_prompt(docs: Sequence[EvidenceDoc], numbers: Sequence[int]) -> str:
    blocks = "\n\n".join(f"[{n}]\n{doc.text}" for doc, n in zip(docs, numbers))
    numbers_list = ", ".join(str(n) for n in numbers)
    return (
        "Below are numbered source documents returned by a search.\n"
        f"For EACH document ({numbers_list}) return one entry with its number `n` and "
        f"up to {MAX_POINTS} points.\n"
        "Rules:\n"
        "- Each point is one factual statement from THAT document only, at most 40 words.\n"
        "- Keep numbers, dates, names, places and the document's own claims and recommendations.\n"
        "- Keep the source's strength: 'reportedly' or 'plans to' must not become 'confirmed'.\n"
        "- Never merge information across documents. Never add anything not in the document.\n"
        "- Write the points in English.\n\n"
        f"{blocks}"
    )


@dataclass
class DigestResult:
    text: str
    mode: str                          # "llm" | "fallback" | "verbatim"
    fallback_docs: List[int] = field(default_factory=list)


def render_digest(
    docs: Sequence[EvidenceDoc],
    numbers: Sequence[int],
    digest: Optional[RagDigest],
) -> Tuple[str, List[int]]:
    """Code-written headers + LLM points; snippet for any document without points.

    Returns (text, citation numbers that fell back to the snippet).
    """
    points_by_n: Dict[int, List[str]] = {}
    if digest is not None:
        valid = set(numbers)
        for entry in digest.documents:
            if entry.n not in valid:
                logger.warning(f"RAG digest: discarding entry for unknown document [{entry.n}]")
                continue
            points = [p.strip() for p in entry.points if p and p.strip()]
            points_by_n.setdefault(entry.n, points[:MAX_POINTS])

    blocks, fallback_docs = [], []
    for doc, n in zip(docs, numbers):
        points = points_by_n.get(n)
        if points:
            body = "\n".join(f"- {p}" for p in points)
        else:
            body = f"(estratto) «{_snippet(doc.text)}»"
            fallback_docs.append(n)
        blocks.append(f"{doc.header(n)}\n{body}")
    return "\n\n".join(blocks), fallback_docs


def digest_rag_result(docs: Sequence[EvidenceDoc], numbers: Sequence[int], llm) -> DigestResult:
    """Build the tool_result text Sonnet receives for one `rag_search` call.

    `llm` is the T5 client (`LLMFactory.get("t5")`). Short results go verbatim; on
    any T5 failure every document falls back to its snippet, headers intact.
    """
    full = render_verbatim(docs, numbers)
    if not docs or len(full) <= VERBATIM_MAX_CHARS:
        return DigestResult(text=full, mode="verbatim")

    digest: Optional[RagDigest] = None
    try:
        raw = llm.generate(
            prompt=build_digest_prompt(docs, numbers),
            temperature=0.1,
            max_tokens=min(4096, 256 + 160 * len(docs)),
            response_schema=RAG_DIGEST_SCHEMA,
        )
    except Exception as e:
        logger.warning(f"RAG digest: T5 call failed, using snippets: {e}")
    else:
        try:
            digest = RagDigest.model_validate_json(raw or "")
        except ValidationError as e:
            logger.warning(f"RAG digest: invalid T5 output, using snippets: {e}")

    text, fallback_docs = render_digest(docs, numbers, digest)
    if digest is None:
        return DigestResult(text=text, mode="fallback", fallback_docs=list(numbers))
    return DigestResult(text=text, mode="llm", fallback_docs=fallback_docs)
