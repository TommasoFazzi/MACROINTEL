"""RAGTool — hybrid RAG search over articles and reports."""

import math
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from .base import BaseTool, ToolResult
from ...utils.logger import get_logger
from ...utils.stopwords import clean_query

logger = get_logger(__name__)

# ── Time-weighted decay constants ─────────────────────────────────────────────
DEFAULT_DECAY_K = 0.025          # half-life ~28 days
OVER_FETCH_MULTIPLIER = 3        # fetch 3x to avoid Top-K bias before decay
MIN_DECAYED_SCORE = 0.15         # floor: discard noise after decay

# Score field name per search type
SEARCH_TYPE_SCORE_FIELD = {
    "vector": "similarity",
    "keyword": "fts_score",
    "hybrid": "fusion_score",
}

# Singleton embedding model (shared with oracle_engine.py)
_embedding_model = None


def _get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer
        _embedding_model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')
        logger.info("RAGTool: loaded embedding model")
    return _embedding_model


def apply_time_decay(
    results: List[Dict],
    decay_k: float = DEFAULT_DECAY_K,
    date_field: str = "published_date",
    score_field: str = "similarity",
    reference_date: Optional[datetime] = None,
) -> List[Dict]:
    """
    Apply exponential time decay to search results.

    final_score = raw_score * exp(-k * days_old)

    Args:
        results: Search results with date and score fields.
        decay_k: Decay rate constant. Higher = more aggressive recency bias.
        date_field: Name of the date field in each result dict.
        score_field: Name of the score field to decay.
        reference_date: Reference point for age calculation.
            Defaults to now (UTC). For historical queries, pass end_date
            so decay is relative to the queried time window (time-shifting).

    Returns:
        Results re-sorted by decayed score descending.
        Each result gains: {score_field}_raw, time_decay_factor, days_old.
    """
    ref = reference_date or datetime.now(timezone.utc)
    # Normalize to naive date for safe subtraction (avoid aware vs naive TypeError)
    if isinstance(ref, datetime):
        ref_date = ref.replace(tzinfo=None).date() if ref.tzinfo else ref.date()
    else:
        ref_date = ref

    for r in results:
        pub = r.get(date_field)
        raw_score = r.get(score_field, 0)
        r[f"{score_field}_raw"] = raw_score

        if pub is None:
            r["time_decay_factor"] = 1.0
            r["days_old"] = 0
            continue

        # Normalize published_date to naive date
        if isinstance(pub, datetime):
            pub = pub.replace(tzinfo=None).date() if pub.tzinfo else pub.date()
        elif isinstance(pub, str):
            pub = date.fromisoformat(pub[:10])

        days_old = max((ref_date - pub).days, 0)
        decay_factor = math.exp(-decay_k * days_old)

        r[score_field] = raw_score * decay_factor
        r["time_decay_factor"] = round(decay_factor, 4)
        r["days_old"] = days_old
        r["_sort_score"] = r[score_field]

    results.sort(key=lambda x: x.get(score_field, 0), reverse=True)
    return results


class RAGTool(BaseTool):
    name = "rag_search"
    description = (
        "Hybrid RAG search over articles and intelligence reports. "
        "Use for factual queries, narrative analysis, and document retrieval."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Natural language query"},
            "mode": {
                "type": "string",
                "enum": ["both", "factual", "strategic"],
                "default": "both",
            },
            "top_k": {"type": "integer", "default": 10},
            "filters": {
                "type": "object",
                "description": "Optional filters: start_date, end_date, categories, gpe_filter, sources, search_type",
            },
        },
        "required": ["query"],
    }

    DEFAULT_MIN_SIMILARITY = 0.30
    DEFAULT_CONTEXT_MAX_CHARS = 60000

    def _execute(self, **kwargs) -> ToolResult:
        query: str = kwargs["query"]
        mode: str = kwargs.get("mode", "both")
        top_k: int = kwargs.get("top_k", 10)
        filters: Dict = kwargs.get("filters") or {}

        cleaned = clean_query(query)
        embedding = _get_embedding_model().encode(cleaned).tolist()

        start_date = filters.get("start_date")
        end_date = filters.get("end_date")
        categories = filters.get("categories")
        gpe_filter = filters.get("gpe_filter")
        sources = filters.get("sources")
        search_type = filters.get("search_type", "hybrid")

        # Time decay config
        time_decay_k = filters.get("time_decay_k", DEFAULT_DECAY_K)
        decay_active = time_decay_k is not None and time_decay_k > 0
        # Over-fetch when decay active to avoid Top-K bias
        fetch_k = top_k * OVER_FETCH_MULTIPLIER if decay_active else top_k

        chunks: List[Dict] = []
        reports: List[Dict] = []

        if mode in ("both", "factual"):
            if search_type == "vector":
                chunks = self.db.semantic_search(
                    query_embedding=embedding,
                    top_k=fetch_k,
                    category=categories[0] if categories else None,
                    start_date=start_date,
                    end_date=end_date,
                    sources=sources,
                    gpe_entities=gpe_filter,
                )
            elif search_type == "keyword":
                chunks = self.db.full_text_search(
                    query=cleaned,
                    top_k=fetch_k,
                    category=categories[0] if categories else None,
                    start_date=start_date,
                    end_date=end_date,
                    sources=sources,
                    gpe_entities=gpe_filter,
                )
            else:  # hybrid
                chunks = self.db.hybrid_search(
                    query=cleaned,
                    query_embedding=embedding,
                    top_k=fetch_k,
                    category=categories[0] if categories else None,
                    start_date=start_date,
                    end_date=end_date,
                    sources=sources,
                    gpe_entities=gpe_filter,
                )

        if mode in ("both", "strategic"):
            reports = self.db.semantic_search_reports(
                query_embedding=embedding,
                top_k=fetch_k,
                min_similarity=self.DEFAULT_MIN_SIMILARITY,
                start_date=start_date,
                end_date=end_date,
            )

        # ── Apply time-weighted decay ─────────────────────────────────────
        if decay_active:
            score_field = SEARCH_TYPE_SCORE_FIELD.get(search_type, "similarity")
            # Time-shifting: use end_date as reference for historical queries
            ref_date = filters.get("time_decay_reference")

            if chunks:
                chunks = apply_time_decay(
                    chunks, decay_k=time_decay_k,
                    date_field="published_date", score_field=score_field,
                    reference_date=ref_date,
                )
                chunks = [c for c in chunks if c.get(score_field, 0) >= MIN_DECAYED_SCORE]
                chunks = chunks[:top_k]

            if reports:
                reports = apply_time_decay(
                    reports, decay_k=time_decay_k,
                    date_field="report_date", score_field="similarity",
                    reference_date=ref_date,
                )
                reports = [r for r in reports if r.get("similarity", 0) >= MIN_DECAYED_SCORE]
                reports = reports[:top_k]

            ref_label = ref_date.isoformat()[:10] if ref_date else "now"
            logger.info(
                f"Time decay applied: k={time_decay_k:.3f}, ref={ref_label}, "
                f"chunks={len(chunks)}, reports={len(reports)}"
            )

        data = {"chunks": chunks, "reports": reports}
        metadata = {
            "chunks_found": len(chunks),
            "reports_found": len(reports),
            "mode": mode,
            "search_type": search_type,
            "time_decay_k": time_decay_k if decay_active else None,
        }

        return ToolResult(success=True, data=data, metadata=metadata)

    @staticmethod
    def _score_line(result: Dict, score_field: str = "similarity") -> str:
        """Format score line with optional decay transparency."""
        score = result.get(score_field, 0)
        raw = result.get(f"{score_field}_raw")
        decay = result.get("time_decay_factor")
        line = f"Score: {score:.2f}"
        if raw is not None and decay is not None:
            line += f" (raw: {raw:.2f}, freshness: {decay:.2f})"
        return line

    def _format_success(self, data: Any, metadata: Dict) -> str:
        reports = data.get("reports", [])
        chunks = data.get("chunks", [])
        search_type = metadata.get("search_type", "hybrid")
        chunk_score_field = SEARCH_TYPE_SCORE_FIELD.get(search_type, "similarity")
        parts = []
        current_len = 0

        for i, report in enumerate(reports, 1):
            report_date = report.get("report_date")
            date_str = (
                report_date.strftime("%d/%m/%Y")
                if hasattr(report_date, "strftime")
                else (str(report_date)[:10] if report_date else "N/A")
            )
            content = (report.get("final_content") or report.get("draft_content", ""))[:28000]
            score_line = self._score_line(report, "similarity")
            doc = (
                f"\n<DOCUMENTO_{i}>\n"
                f"Tipo: REPORT\nID: {report.get('id', 'N/A')}\n"
                f"Data: {date_str}\nStatus: {report.get('status', 'draft')}\n"
                f"{score_line}\n\n"
                f"[INIZIO_TESTO]\n{content}\n[FINE_TESTO]\n"
                f"</DOCUMENTO_{i}>\n"
            )
            if current_len + len(doc) > self.DEFAULT_CONTEXT_MAX_CHARS:
                break
            parts.append(doc)
            current_len += len(doc)

        offset = len(reports) + 1
        for j, chunk in enumerate(chunks, offset):
            pub_date = chunk.get("published_date")
            date_str = (
                pub_date.strftime("%d/%m/%Y")
                if hasattr(pub_date, "strftime")
                else (str(pub_date)[:10] if pub_date else "N/A")
            )
            score_line = self._score_line(chunk, chunk_score_field)
            doc = (
                f"\n<DOCUMENTO_{j}>\n"
                f"Tipo: ARTICOLO\nTitolo: {chunk.get('title', 'N/A')}\n"
                f"Fonte: {chunk.get('source', 'Unknown')}\nData: {date_str}\n"
                f"{score_line}\n\n"
                f"[INIZIO_TESTO]\n{chunk.get('content', '')}\n[FINE_TESTO]\n"
                f"</DOCUMENTO_{j}>\n"
            )
            if current_len + len(doc) > self.DEFAULT_CONTEXT_MAX_CHARS:
                break
            parts.append(doc)
            current_len += len(doc)

        return "\n".join(parts) if parts else "[RAG: nessun documento trovato]"

    def prepare_sources(self, reports: List[Dict], chunks: List[Dict]) -> List[Dict]:
        """Return structured source list for API response."""
        sources = []
        for report in reports:
            rd = report.get("report_date")
            date_str = (
                rd.strftime("%d/%m/%Y")
                if hasattr(rd, "strftime")
                else (str(rd)[:10] if rd else "N/A")
            )
            sources.append({
                "type": "REPORT",
                "id": report.get("id"),
                "title": f"Report #{report.get('id')} - {date_str}",
                "date_str": date_str,
                "similarity": report.get("similarity", 0),
                "similarity_raw": report.get("similarity_raw"),
                "time_decay_factor": report.get("time_decay_factor"),
                "days_old": report.get("days_old"),
                "status": report.get("status", "draft"),
                "preview": (report.get("final_content") or report.get("draft_content", ""))[:200],
            })
        for chunk in chunks:
            pd = chunk.get("published_date")
            date_str = (
                pd.strftime("%d/%m/%Y")
                if hasattr(pd, "strftime")
                else (str(pd)[:10] if pd else "N/A")
            )
            sources.append({
                "type": "ARTICOLO",
                "id": chunk.get("article_id"),
                "title": chunk.get("title", "Articolo senza titolo"),
                "date_str": date_str,
                "source": chunk.get("source", "Unknown"),
                "similarity": chunk.get("similarity", 0),
                "similarity_raw": chunk.get("similarity_raw"),
                "time_decay_factor": chunk.get("time_decay_factor"),
                "days_old": chunk.get("days_old"),
                "preview": chunk.get("content", "")[:200],
                "link": chunk.get("link"),
            })
        sources.sort(
            key=lambda x: x.get("_sort_score", x.get("similarity", 0)),
            reverse=True,
        )
        return sources
