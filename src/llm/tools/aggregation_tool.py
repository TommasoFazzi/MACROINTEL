"""AggregationTool — pre-parametrized statistical aggregations."""

from typing import Any, Dict, List, Optional

from .base import BaseTool, ToolResult
from ...utils.logger import get_logger

logger = get_logger(__name__)

VALID_TARGETS = {"articles", "entities", "storylines", "trade_signals"}
VALID_AGGREGATION_TYPES = {"trend_over_time", "top_n", "distribution", "statistics"}
VALID_TIME_BUCKETS = {"hour", "day", "week", "month", "year"}


class AggregationTool(BaseTool):
    name = "aggregation"
    description = (
        "Statistical aggregations over intelligence data: trends over time, "
        "top-N rankings, distributions, and statistics."
    )
    parameters = {
        "type": "object",
        "properties": {
            "rationale": {
                "type": "string",
                "description": (
                    "Think step-by-step: why use aggregation here? Which pre-defined aggregation type "
                    "fits the user request (trend_over_time/top_n/distribution/statistics)? "
                    "Which target dataset is relevant?"
                ),
            },
            "aggregation_type": {
                "type": "string",
                "enum": list(VALID_AGGREGATION_TYPES),
                "description": "Type of aggregation to perform",
            },
            "target": {
                "type": "string",
                "enum": list(VALID_TARGETS),
                "description": "Target dataset",
            },
            "filters": {
                "type": "object",
                "description": "Optional: start_date, end_date, category, gpe_filter",
            },
            "time_bucket": {
                "type": "string",
                "enum": list(VALID_TIME_BUCKETS),
            },
            "limit": {"type": "integer"},
        },
        "required": ["rationale", "aggregation_type", "target"],
    }

    def _execute(self, **kwargs) -> ToolResult:
        agg_type: str = kwargs["aggregation_type"]
        target: str = kwargs["target"]
        filters: Dict = kwargs.get("filters") or {}
        time_bucket: str = kwargs.get("time_bucket", "day")
        limit: int = min(kwargs.get("limit", 10), 100)

        if agg_type not in VALID_AGGREGATION_TYPES:
            return ToolResult(success=False, data=None, error=f"Invalid aggregation_type: {agg_type}")
        if target not in VALID_TARGETS:
            return ToolResult(success=False, data=None, error=f"Invalid target: {target}")
        if time_bucket not in VALID_TIME_BUCKETS:
            time_bucket = "day"

        method = getattr(self, f"_agg_{agg_type}", None)
        if method is None:
            return ToolResult(success=False, data=None, error=f"No handler for {agg_type}")

        return method(target=target, filters=filters, time_bucket=time_bucket, limit=limit)

    # ── Aggregation implementations ───────────────────────────────────────────

    def _agg_trend_over_time(self, target, filters, time_bucket, limit) -> ToolResult:
        table_map = {
            "articles": ("articles", "published_date"),
            "entities": ("entity_mentions", "created_at"),
            "storylines": ("storylines", "created_at"),
            "trade_signals": ("trade_signals", "created_at"),
        }
        table, date_col = table_map[target]

        conditions, params = self._build_conditions(table, filters)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        query = f"""
            SELECT DATE_TRUNC(%s, {date_col}) AS bucket, COUNT(*) AS count
            FROM {table}
            {where}
            GROUP BY bucket
            ORDER BY bucket
            LIMIT %s
        """
        params = [time_bucket] + params + [limit]
        return self._run_query(query, params, agg_type="trend_over_time")

    def _agg_top_n(self, target, filters, time_bucket, limit) -> ToolResult:
        if target == "entities":
            query = """
                SELECT e.name, e.entity_type, COUNT(em.id) AS mention_count
                FROM entities e
                JOIN entity_mentions em ON e.id = em.entity_id
                GROUP BY e.id, e.name, e.entity_type
                ORDER BY mention_count DESC
                LIMIT %s
            """
            return self._run_query(query, [limit], agg_type="top_n")

        if target == "storylines":
            query = """
                SELECT id, title, momentum_score, narrative_status, article_count
                FROM v_active_storylines
                ORDER BY momentum_score DESC NULLS LAST
                LIMIT %s
            """
            return self._run_query(query, [limit], agg_type="top_n")

        if target == "articles":
            conditions, params = self._build_conditions("articles", filters)
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            query = f"""
                SELECT id, title, source, published_date
                FROM articles
                {where}
                ORDER BY published_date DESC
                LIMIT %s
            """
            return self._run_query(query, params + [limit], agg_type="top_n")

        if target == "trade_signals":
            query = """
                SELECT ticker, signal, confidence, intelligence_score, timeframe, rationale
                FROM trade_signals
                ORDER BY intelligence_score DESC NULLS LAST, confidence DESC NULLS LAST
                LIMIT %s
            """
            return self._run_query(query, [limit], agg_type="top_n")

        return ToolResult(success=False, data=None, error=f"top_n not supported for {target}")

    def _agg_distribution(self, target, filters, time_bucket, limit) -> ToolResult:
        if target == "articles":
            conditions, params = self._build_conditions("articles", filters)
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            query = f"""
                SELECT category, COUNT(*) AS count,
                       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
                FROM articles
                {where}
                GROUP BY category
                ORDER BY count DESC
                LIMIT %s
            """
            return self._run_query(query, params + [limit], agg_type="distribution")

        if target == "entities":
            query = """
                SELECT entity_type, COUNT(*) AS count,
                       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
                FROM entities
                GROUP BY entity_type
                ORDER BY count DESC
                LIMIT %s
            """
            return self._run_query(query, [limit], agg_type="distribution")

        if target == "trade_signals":
            query = """
                SELECT signal, COUNT(*) AS count,
                       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
                FROM trade_signals
                GROUP BY signal
                ORDER BY count DESC
                LIMIT %s
            """
            return self._run_query(query, [limit], agg_type="distribution")

        return ToolResult(success=False, data=None, error=f"distribution not supported for {target}")

    def _agg_statistics(self, target, filters, time_bucket, limit) -> ToolResult:
        if target == "storylines":
            query = """
                SELECT
                    COUNT(*) AS total,
                    ROUND(AVG(momentum_score)::numeric, 3) AS avg_momentum,
                    ROUND(MIN(momentum_score)::numeric, 3) AS min_momentum,
                    ROUND(MAX(momentum_score)::numeric, 3) AS max_momentum,
                    ROUND(STDDEV(momentum_score)::numeric, 3) AS stddev_momentum,
                    COUNT(*) FILTER (WHERE narrative_status = 'ACTIVE') AS active_count
                FROM storylines
            """
            return self._run_query(query, [], agg_type="statistics")

        if target == "trade_signals":
            query = """
                SELECT
                    COUNT(*) AS total,
                    ROUND(AVG(confidence)::numeric, 3) AS avg_confidence,
                    ROUND(AVG(intelligence_score)::numeric, 3) AS avg_intel_score,
                    ROUND(MIN(intelligence_score)::numeric, 3) AS min_intel,
                    ROUND(MAX(intelligence_score)::numeric, 3) AS max_intel
                FROM trade_signals
            """
            return self._run_query(query, [], agg_type="statistics")

        if target == "articles":
            conditions, params = self._build_conditions("articles", filters)
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            query = f"""
                SELECT
                    COUNT(*) AS total,
                    MIN(published_date) AS earliest,
                    MAX(published_date) AS latest,
                    COUNT(DISTINCT source) AS source_count,
                    COUNT(DISTINCT category) AS category_count
                FROM articles
                {where}
            """
            return self._run_query(query, params, agg_type="statistics")

        return ToolResult(success=False, data=None, error=f"statistics not supported for {target}")

    # ── Utilities ─────────────────────────────────────────────────────────────

    def _build_conditions(self, table: str, filters: Dict):
        conditions = []
        params = []
        if filters.get("start_date"):
            conditions.append("published_date >= %s")
            params.append(filters["start_date"])
        if filters.get("end_date"):
            conditions.append("published_date <= %s")
            params.append(filters["end_date"])
        if filters.get("category") and table == "articles":
            conditions.append("category = %s")
            params.append(filters["category"])
        return conditions, params

    def _run_query(self, query: str, params: List, agg_type: str) -> ToolResult:
        try:
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    rows = cur.fetchall()
                    columns = [d[0] for d in cur.description] if cur.description else []
                    conn.rollback()

            data = {"results": [dict(zip(columns, row)) for row in rows], "columns": columns}
            return ToolResult(success=True, data=data, metadata={"agg_type": agg_type, "rows": len(rows)})
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))

    def _format_success(self, data: Any, metadata: Dict) -> str:
        rows = data.get("results", [])
        columns = data.get("columns", [])
        agg_type = metadata.get("agg_type", "")

        if not rows:
            return "[Aggregation: no results]"

        if agg_type == "trend_over_time":
            lines = [f"- {row.get('bucket', '')}: {row.get('count', 0)}" for row in rows]
            return "Trend:\n" + "\n".join(lines)

        if agg_type == "top_n":
            lines = []
            for i, row in enumerate(rows, 1):
                lines.append(f"{i}. " + " | ".join(f"{k}: {v}" for k, v in row.items()))
            return "Top results:\n" + "\n".join(lines)

        if agg_type == "distribution":
            lines = [f"- {row.get(columns[0], '')}: {row.get('count', 0)} ({row.get('pct', 0)}%)" for row in rows]
            return "Distribution:\n" + "\n".join(lines)

        # statistics or generic
        header = " | ".join(columns)
        values = " | ".join(str(rows[0].get(c, "")) for c in columns) if rows else ""
        return f"{header}\n{values}"
