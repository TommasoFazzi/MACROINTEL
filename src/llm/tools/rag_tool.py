"""RAGTool — hybrid RAG search over articles and reports."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from .base import BaseTool, ToolResult
from ...utils.logger import get_logger
from ...utils.stopwords import clean_query

logger = get_logger(__name__)

# Singleton embedding model (shared with oracle_engine.py)
_embedding_model = None


def _get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer
        _embedding_model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')
        logger.info("RAGTool: loaded embedding model")
    return _embedding_model


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

        chunks: List[Dict] = []
        reports: List[Dict] = []

        if mode in ("both", "factual"):
            if search_type == "vector":
                chunks = self.db.semantic_search(
                    query_embedding=embedding,
                    top_k=top_k,
                    category=categories[0] if categories else None,
                    start_date=start_date,
                    end_date=end_date,
                    sources=sources,
                    gpe_entities=gpe_filter,
                )
            elif search_type == "keyword":
                chunks = self.db.full_text_search(
                    query=cleaned,
                    top_k=top_k,
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
                    top_k=top_k,
                    category=categories[0] if categories else None,
                    start_date=start_date,
                    end_date=end_date,
                    sources=sources,
                    gpe_entities=gpe_filter,
                )

        if mode in ("both", "strategic"):
            reports = self.db.semantic_search_reports(
                query_embedding=embedding,
                top_k=top_k,
                min_similarity=self.DEFAULT_MIN_SIMILARITY,
                start_date=start_date,
                end_date=end_date,
            )

        data = {"chunks": chunks, "reports": reports}
        metadata = {
            "chunks_found": len(chunks),
            "reports_found": len(reports),
            "mode": mode,
            "search_type": search_type,
        }

        return ToolResult(success=True, data=data, metadata=metadata)

    def _format_success(self, data: Any, metadata: Dict) -> str:
        reports = data.get("reports", [])
        chunks = data.get("chunks", [])
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
            doc = (
                f"\n<DOCUMENTO_{i}>\n"
                f"Tipo: REPORT\nID: {report.get('id', 'N/A')}\n"
                f"Data: {date_str}\nStatus: {report.get('status', 'draft')}\n"
                f"Similarity: {report.get('similarity', 0):.2f}\n\n"
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
            doc = (
                f"\n<DOCUMENTO_{j}>\n"
                f"Tipo: ARTICOLO\nTitolo: {chunk.get('title', 'N/A')}\n"
                f"Fonte: {chunk.get('source', 'Unknown')}\nData: {date_str}\n"
                f"Similarity: {chunk.get('similarity', 0):.2f}\n\n"
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
                "preview": chunk.get("content", "")[:200],
                "link": chunk.get("link"),
            })
        sources.sort(key=lambda x: x.get("similarity", 0), reverse=True)
        return sources
