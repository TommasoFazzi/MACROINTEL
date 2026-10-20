"""RAGTool — hybrid RAG search over articles and reports."""

import math
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from .base import BaseTool, ToolResult
from ..rag_evidence import EvidenceDoc, render_verbatim, select_context_documents
from ...utils.logger import get_logger
from ...utils.stopwords import clean_query

logger = get_logger(__name__)

# ── Time-weighted decay constants ─────────────────────────────────────────────
DEFAULT_DECAY_K = 0.025          # half-life ~28 days
OVER_FETCH_MULTIPLIER = 3        # fetch 3x to avoid Top-K bias before decay
MIN_DECAYED_SCORE = 0.15         # floor for reports' decayed similarity (hard filter with fallback)

# Source-type override for decay K.
# Think tank reports retain value for months/years, not days.
# Keys are the exact intelligence_sources.source_type labels written by
# scripts/seed_sources.py (Italian display labels). Unlisted labels use the intent K.
SOURCE_TYPE_DECAY_K = {
    "Think Tank":       0.002,   # half-life ~346 days (~1 year)
    "Accademia":        0.002,   # half-life ~346 days
    "Ufficiale/Gov":    0.003,   # half-life ~231 days (~8 months)
    "Think Tank/Media": 0.005,   # half-life ~139 days (analysis essays, e.g. War on the Rocks)
}

# Score field name per search type
SEARCH_TYPE_SCORE_FIELD = {
    "vector": "similarity",
    "keyword": "fts_score",
    "hybrid": "fusion_score",
}

# Singleton embedding model (shared with oracle_engine.py)
_embedding_model = None
# Singleton cross-encoder reranker (same model used by report_generator)
_reranker = None


def _get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer
        _embedding_model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')
        logger.info("RAGTool: loaded embedding model")
    return _embedding_model


def _get_reranker():
    global _reranker
    if _reranker is None:
        from sentence_transformers import CrossEncoder
        _reranker = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')
        logger.info("RAGTool: loaded cross-encoder reranker")
    return _reranker


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

        # Source-type override: think tank/government reports decay slower — never faster
        # than the intent K (REFERENCE uses 0.001, below the think-tank override).
        effective_k = min(decay_k, SOURCE_TYPE_DECAY_K.get(r.get("source_type"), decay_k))

        decay_factor = math.exp(-effective_k * days_old)

        r[score_field] = raw_score * decay_factor
        r["time_decay_factor"] = round(decay_factor, 4)
        r["days_old"] = days_old
        r["_sort_score"] = r[score_field]

    results.sort(key=lambda x: x.get(score_field, 0), reverse=True)
    return results


def apply_authority_rerank(
    results: List[Dict],
    score_field: str,
    authority_alpha: float = 0.15,
) -> List[Dict]:
    """
    Re-rank results using a normalized weighted sum of relevance and source authority.

    final_score = (1 - alpha) * norm_relevance + alpha * norm_authority

    Both dimensions are normalized to [0, 1] before combining, so absolute score
    quality is preserved (a chunk at 0.75 similarity is meaningfully different from
    one at 0.51, unlike pure rank-based RRF which treats them equally).

    Args:
        results: Chunks already sorted by decayed score (output of apply_time_decay).
        score_field: Name of the relevance score field (e.g. "similarity",
            "fusion_score", "fts_score"). Normalized within the batch to [0, 1].
        authority_alpha: Weight of the authority component (default 0.15 → authority
            is 15% of final score). Authority acts as a tiebreaker for similar
            relevance scores, not a dominant factor.

    Returns:
        Results re-sorted by authority_final_score descending.
        Each result gains: authority_final_score, norm_relevance, norm_authority.

    Notes:
        - norm_relevance = score / max_score_in_batch (preserves absolute quality)
        - norm_authority = authority_score / 5.0 (maps 1–5 → 0.2–1.0)
        - Chunks with authority_score=None (pre-migration-024 articles) default to
          3.0/5.0 = 0.6 — the neutral mid-point of the authority scale.
        - High-relevance low-authority chunks (e.g. sole breaking-news source) are
          preserved: their high norm_relevance outweighs the authority penalty.
    """
    if not results:
        return results

    # Normalization: floor = min(0, min_score_in_batch)
    #   • For non-negative scores (similarity 0–1, fusion_score):
    #     floor=0 → behaves as score/max_score, preserving absolute values
    #     (0.82 similarity stays near 1.0, not collapsed to 0.0)
    #   • For cross-encoder logits (can be negative, e.g. -10 to +5):
    #     floor=min_score → maps the full range to [0, 1]
    raw_scores = [r.get(score_field, 0) for r in results]
    floor = min(0.0, min(raw_scores))
    max_score = max(raw_scores)
    score_range = (max_score - floor) or 1.0

    for r in results:
        norm_rel = (r.get(score_field, 0) - floor) / score_range
        # authority: 1.0–5.0 → 0.2–1.0; None → 0.6 (neutral, equivalent to score 3.0)
        auth = r.get("authority_score")
        norm_auth = float(auth) / 5.0 if auth is not None else 0.6

        r["norm_relevance"] = round(norm_rel, 4)
        r["norm_authority"] = round(norm_auth, 4)
        r["authority_final_score"] = (1 - authority_alpha) * norm_rel + authority_alpha * norm_auth

    results.sort(key=lambda x: x.get("authority_final_score", 0), reverse=True)
    return results


class RAGTool(BaseTool):
    name = "rag_search"
    description = (
        "Hybrid RAG search (vector + keyword) over articles and intelligence reports. "
        "Use for: factual news queries, narrative analysis, document retrieval, overview research. "
        "Supports time-weighted decay (pass time_decay_k in filters), GPE filtering, and recency boost. "
        "PATH FACTUAL: mode='both', top_k=10, filters.time_decay_k=0.03. "
        "PATH OVERVIEW: mode='both', filters.search_type='vector', top_k=15, filters.time_decay_k=0.005. "
        "PATH NARRATIVE: mode='both', top_k=8, filters.time_decay_k=0.02. "
        "Always extract dates from query and pass as filters.start_date/end_date (ISO YYYY-MM-DD). "
        "Always extract geographic entities and pass as filters.gpe_filter (in English)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "rationale": {
                "type": "string",
                "description": (
                    "Think step-by-step: which SOP path are you following (FACTUAL/OVERVIEW/NARRATIVE/etc.)? "
                    "Why is rag_search the right tool here? What temporal/geographic filters will you apply? "
                    "What decay rate matches this query type?"
                ),
            },
            "query": {"type": "string", "description": "Natural language search query (core topic, without temporal noise)"},
            "mode": {
                "type": "string",
                "enum": ["both", "factual", "strategic"],
                "description": "'both' searches articles+reports, 'factual' articles only, 'strategic' reports only",
            },
            "top_k": {
                "type": "integer",
                "description": "Number of results to return (5-20 recommended)",
            },
            "filters": {
                "type": "object",
                "description": "Optional search filters",
                "properties": {
                    "start_date": {"type": "string", "description": "ISO YYYY-MM-DD — filter articles from this date"},
                    "end_date": {"type": "string", "description": "ISO YYYY-MM-DD — filter articles up to this date"},
                    "gpe_filter": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Geographic/political entities in English, e.g. ['China', 'Taiwan', 'South China Sea']",
                    },
                    "categories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Article categories: GEOPOLITICS, DEFENSE, ECONOMY, CYBER, ENERGY",
                    },
                    "search_type": {
                        "type": "string",
                        "enum": ["hybrid", "vector", "keyword"],
                        "description": "Use 'vector' for overview/panoramic queries to avoid AND-matching issues",
                    },
                    "time_decay_k": {
                        "type": "number",
                        "description": (
                            "Exponential decay rate for recency bias. "
                            "FACTUAL=0.03, ANALYTICAL=0.015, NARRATIVE=0.02, MARKET=0.04, "
                            "OVERVIEW=0.005, REFERENCE=0.001, SPATIAL=0.005, COMPARATIVE=0.015"
                        ),
                    },
                    "recency_boost": {
                        "type": "boolean",
                        "description": "Set true when query contains 'latest', 'recent', 'today' keywords",
                    },
                },
            },
        },
        "required": ["rationale", "query"],
    }

    DEFAULT_MIN_SIMILARITY = 0.30
    DEFAULT_CONTEXT_MAX_CHARS = 60000
    HISTORY_MAX_CHARS = 50000  # Override base class (8000) — Gemini 2.5 Flash supports 1M tokens

    def _execute(self, **kwargs) -> ToolResult:
        query: str = kwargs["query"]
        mode: str = kwargs.get("mode", "both")
        top_k: int = int(kwargs.get("top_k", 10))
        filters: Dict = kwargs.get("filters") or {}
        multi_query: Optional[List[str]] = kwargs.get("multi_query")

        cleaned = clean_query(query)
        model = _get_embedding_model()
        embedding = model.encode(cleaned).tolist()

        # Convert Protobuf types to Python native types
        start_date = filters.get("start_date")
        end_date = filters.get("end_date")
        categories = filters.get("categories")
        if categories and hasattr(categories, '__iter__') and not isinstance(categories, (str, bytes)):
            categories = list(categories)
        gpe_filter = filters.get("gpe_filter")
        if gpe_filter and hasattr(gpe_filter, '__iter__') and not isinstance(gpe_filter, (str, bytes)):
            gpe_filter = list(gpe_filter)
        sources = filters.get("sources")
        if sources and hasattr(sources, '__iter__') and not isinstance(sources, (str, bytes)):
            sources = list(sources)
        search_type = filters.get("search_type", "hybrid")

        # Time decay config
        time_decay_k = filters.get("time_decay_k", DEFAULT_DECAY_K)
        decay_active = time_decay_k is not None and time_decay_k > 0
        # Over-fetch when decay active to avoid Top-K bias
        fetch_k = int(top_k * OVER_FETCH_MULTIPLIER) if decay_active else top_k

        chunks: List[Dict] = []
        reports: List[Dict] = []

        # Build list of (query_text, embedding) pairs for multi-query expansion
        query_variants = [(cleaned, embedding)]
        if multi_query:
            # Convert Protobuf list to Python list if needed
            if hasattr(multi_query, '__iter__') and not isinstance(multi_query, (str, bytes, list)):
                multi_query = list(multi_query)
            for sq in multi_query[:3]:  # cap at 3 sub-queries
                sq_cleaned = clean_query(sq)
                sq_emb = model.encode(sq_cleaned).tolist()
                query_variants.append((sq_cleaned, sq_emb))
            logger.info(f"Multi-query expansion: {len(query_variants)} total queries")

        if mode in ("both", "factual"):
            per_query_k = int(max(fetch_k // len(query_variants), top_k)) if len(query_variants) > 1 else fetch_k
            all_chunks = []
            for q_text, q_emb in query_variants:
                if search_type == "vector":
                    results = self.db.semantic_search(
                        query_embedding=q_emb,
                        top_k=per_query_k,
                        category=categories[0] if categories else None,
                        start_date=start_date,
                        end_date=end_date,
                        sources=sources,
                        gpe_entities=gpe_filter,
                    )
                    if not results and gpe_filter:
                        results = self.db.semantic_search(
                            query_embedding=q_emb, top_k=per_query_k,
                            category=categories[0] if categories else None,
                            start_date=start_date, end_date=end_date, sources=sources,
                            gpe_entities=None,
                        )
                elif search_type == "keyword":
                    results = self.db.full_text_search(
                        query=q_text,
                        top_k=per_query_k,
                        category=categories[0] if categories else None,
                        start_date=start_date,
                        end_date=end_date,
                        sources=sources,
                        gpe_entities=gpe_filter,
                    )
                    if not results and gpe_filter:
                        results = self.db.full_text_search(
                            query=q_text, top_k=per_query_k,
                            category=categories[0] if categories else None,
                            start_date=start_date, end_date=end_date, sources=sources,
                            gpe_entities=None,
                        )
                else:  # hybrid
                    results = self.db.hybrid_search(
                        query=q_text,
                        query_embedding=q_emb,
                        top_k=per_query_k,
                        category=categories[0] if categories else None,
                        start_date=start_date,
                        end_date=end_date,
                        sources=sources,
                        gpe_entities=gpe_filter,
                    )
                    if not results and gpe_filter:
                        results = self.db.hybrid_search(
                            query=q_text, query_embedding=q_emb, top_k=per_query_k,
                            category=categories[0] if categories else None,
                            start_date=start_date, end_date=end_date, sources=sources,
                            gpe_entities=None,
                        )
                all_chunks.append(results)

            if len(all_chunks) > 1:
                # Reciprocal Rank Fusion across sub-queries
                chunks = self._rrf_merge(all_chunks, id_field="chunk_id")
            else:
                chunks = all_chunks[0] if all_chunks else []

        if mode in ("both", "strategic"):
            reports = self.db.semantic_search_reports(
                query_embedding=embedding,
                top_k=fetch_k,
                min_similarity=self.DEFAULT_MIN_SIMILARITY,
                start_date=start_date,
                end_date=end_date,
            )
            # Pre-extract focused excerpt per report (Executive Summary + most relevant section)
            # Avoids naive head-truncation which misses sections in the middle of 30k reports
            for report in reports:
                full_content = report.get("final_content") or report.get("draft_content", "")
                report["relevant_excerpt"] = self._extract_report_excerpt(full_content, query)

        # ── Deduplicate chunks: keep highest-scoring chunk per article ────
        if chunks:
            seen_articles = {}
            unique_chunks = []
            for chunk in chunks:
                aid = chunk.get("article_id")
                if aid not in seen_articles:
                    seen_articles[aid] = True
                    unique_chunks.append(chunk)
            if len(unique_chunks) < len(chunks):
                logger.info(f"Chunk dedup: {len(chunks)} → {len(unique_chunks)} (removed {len(chunks) - len(unique_chunks)} duplicates)")
            chunks = unique_chunks

        # ── Cross-encoder reranking ───────────────────────────────────────
        has_reranked = False
        if chunks and len(chunks) > top_k:
            try:
                reranker = _get_reranker()
                pairs = [[query, c.get("content", "")] for c in chunks]
                scores = reranker.predict(pairs, batch_size=32, show_progress_bar=False)
                for i, chunk in enumerate(chunks):
                    logit = float(scores[i])
                    chunk["rerank_score"] = logit
                    # Logits are mostly negative on long queries; decay needs a [0, 1]
                    # score, otherwise multiplying by e^(-k·days) raises old chunks.
                    chunk["rerank_prob"] = 1.0 / (1.0 + math.exp(-logit))
                chunks.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
                has_reranked = True
                logger.info(f"Cross-encoder reranking: {len(chunks)} chunks reranked")
            except Exception as e:
                logger.warning(f"Reranking failed, using original order: {e}")

        # ── Apply time-weighted decay ─────────────────────────────────────
        if decay_active:
            score_field = SEARCH_TYPE_SCORE_FIELD.get(search_type, "similarity")
            # Time-shifting: use end_date as reference for historical queries
            ref_date = filters.get("time_decay_reference")

            # When cross-encoder has reranked, decay on its sigmoid probability so we
            # preserve the cross-encoder ordering instead of reverting to raw similarity.
            effective_score_field = "rerank_prob" if has_reranked else score_field

            if chunks:
                chunks = apply_time_decay(
                    chunks, decay_k=time_decay_k,
                    date_field="published_date", score_field=effective_score_field,
                    reference_date=ref_date,
                )

                # Authority-weighted re-ranking as final tiebreaker (alpha=0.15)
                chunks = apply_authority_rerank(chunks, score_field=effective_score_field)
                chunks = chunks[:top_k]

            if reports:
                reports = apply_time_decay(
                    reports, decay_k=time_decay_k,
                    date_field="report_date", score_field="similarity",
                    reference_date=ref_date,
                )
                filtered = [r for r in reports if r.get("similarity", 0) >= MIN_DECAYED_SCORE]
                reports = (filtered if filtered else reports)[:top_k]

            ref_label = ref_date.isoformat()[:10] if ref_date else "now"
            logger.info(
                f"Time decay applied: k={time_decay_k:.3f}, ref={ref_label}, "
                f"score_field={effective_score_field}, chunks={len(chunks)}, reports={len(reports)}"
            )

        # ── Recency boost: ensure fresh reports are present (ONLY when explicitly requested) ──
        if mode in ("both", "strategic"):
            RECENCY_SLOTS = 2
            recency_boost = filters.get("recency_boost", False)

            # Only inject recent reports when recency_boost is explicitly set
            # (triggered by keywords like "ultimo", "recente", "latest")
            # This prevents irrelevant recent reports from polluting topic-specific queries
            if recency_boost:
                existing_ids = {r.get("id") for r in reports}
                try:
                    latest = self.db.get_latest_reports(n=RECENCY_SLOTS + 1, days_back=14)
                    added = 0
                    for lr in latest:
                        if lr["id"] not in existing_ids:
                            lr["similarity"] = 0.5
                            reports.append(lr)
                            existing_ids.add(lr["id"])
                            added += 1
                    if added:
                        logger.info(f"Recency boost: added {added} recent reports")
                except Exception as e:
                    logger.warning(f"Recency boost failed: {e}")

                # Guarantee recency slots in final results
                if reports and len(reports) > RECENCY_SLOTS:
                    by_date = sorted(
                        reports,
                        key=lambda r: r.get("report_date") or date.min,
                        reverse=True,
                    )
                    top_by_score = reports[:top_k]
                    top_ids = {r.get("id") for r in top_by_score}
                    for fresh in by_date[:RECENCY_SLOTS]:
                        if fresh.get("id") not in top_ids:
                            if len(top_by_score) >= top_k:
                                top_by_score[-1] = fresh
                            else:
                                top_by_score.append(fresh)
                    reports = top_by_score

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
    def _extract_report_excerpt(content: str, query: str, exec_summary_chars: int = 2500, section_chars: int = 2500) -> str:
        """Extract a focused excerpt from a report: Executive Summary + most query-relevant section.

        Reports follow a fixed structure (Executive Summary → Focus Areas → Trend Analysis →
        Implications → Storylines). Section titles are generic ("Geopolitics", "Economy"),
        so relevance is scored by keyword frequency in the section BODY, not the title.

        Args:
            content: Full report markdown content (~30,000 chars).
            query: The user's search query (used to score section relevance).
            exec_summary_chars: Max chars to include from the Executive Summary section.
            section_chars: Max chars to include from the most relevant non-summary section.

        Returns:
            Focused excerpt: Executive Summary + best matching section (max ~5,000 chars total).
            Falls back to first `exec_summary_chars + section_chars` chars if no sections found.
        """
        import re

        # Tokenize query into lowercase keywords (2+ chars, alpha only)
        query_tokens = {w.lower() for w in query.replace("-", " ").split() if len(w) >= 2 and w.isalpha()}

        # Split by level-2 markdown headers (## Title), keeping the header in each chunk
        section_pattern = re.compile(r'(?=\n## )', re.MULTILINE)
        raw_sections = section_pattern.split(content)

        if not raw_sections or len(raw_sections) < 2:
            # No markdown structure — naive truncation as fallback
            return content[:exec_summary_chars + section_chars]

        # Parse sections into (title_lower, full_text) pairs
        header_re = re.compile(r'^##\s+(.+)', re.MULTILINE)
        parsed = []
        for sec in raw_sections:
            m = header_re.search(sec)
            title = m.group(1).strip().lower() if m else ""
            parsed.append((title, sec))

        # Find Executive Summary section by title keyword
        exec_summary = ""
        EXEC_KEYWORDS = ("executive", "sintesi", "summary", "esecutiva", "overview")
        for title, body in parsed:
            if any(kw in title for kw in EXEC_KEYWORDS):
                exec_summary = body[:exec_summary_chars]
                break
        # Fallback: use preamble (first raw chunk, before any ## header)
        if not exec_summary:
            exec_summary = parsed[0][1][:exec_summary_chars]

        # Score ALL sections by keyword frequency in body text (excluding exec summary)
        best_section = ""
        best_score = 0
        for title, body in parsed:
            if any(kw in title for kw in EXEC_KEYWORDS):
                continue  # skip exec summary — already included above
            body_lower = body.lower()
            score = sum(body_lower.count(tok) for tok in query_tokens)
            if score > best_score:
                best_score = score
                best_section = body[:section_chars]

        if best_score == 0 or not best_section:
            # No keyword match (generic/broad query) — return exec summary with more room
            return exec_summary[:exec_summary_chars + section_chars]

        return exec_summary + "\n\n---\n" + best_section

    @staticmethod
    def _rrf_merge(result_lists: List[List[Dict]], id_field: str = "chunk_id", k: int = 60) -> List[Dict]:
        """Reciprocal Rank Fusion across multiple result lists."""
        scores: Dict[Any, float] = {}
        items: Dict[Any, Dict] = {}
        for results in result_lists:
            for rank, item in enumerate(results):
                item_id = item.get(id_field)
                if item_id is None:
                    continue
                scores[item_id] = scores.get(item_id, 0) + 1.0 / (k + rank + 1)
                if item_id not in items:
                    items[item_id] = item
        # Sort by RRF score and assign fusion_score
        sorted_ids = sorted(scores, key=scores.get, reverse=True)
        merged = []
        for item_id in sorted_ids:
            item = items[item_id]
            item["rrf_score"] = scores[item_id]
            merged.append(item)
        return merged

    def select_context_documents(self, data: Dict) -> List[EvidenceDoc]:
        """Documents rendered into the LLM context (articles first, then ≤2 reports).

        Uses the tighter of the two char limits so `format_for_history` never has to
        truncate: what gets registered as citable is exactly what the LLM reads.
        """
        return select_context_documents(
            data, max_chars=min(self.DEFAULT_CONTEXT_MAX_CHARS, self.HISTORY_MAX_CHARS)
        )

    def _format_success(self, data: Any, metadata: Dict) -> str:
        # Local numbering (1..N). The Oracle loop renders with global citation numbers
        # via src/llm/rag_evidence.digest_rag_result instead.
        docs = self.select_context_documents(data)
        return render_verbatim(docs, range(1, len(docs) + 1))
