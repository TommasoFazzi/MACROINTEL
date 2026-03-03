"""GraphTool — recursive CTE graph traversal on storyline_edges."""

from typing import Any, Dict, List, Optional

from .base import BaseTool, ToolResult
from ...utils.logger import get_logger

logger = get_logger(__name__)

VALID_OPERATIONS = {"connected_storylines", "entity_path", "storyline_cluster"}


class GraphTool(BaseTool):
    name = "graph_navigation"
    description = (
        "Navigate storyline relationship graph using recursive traversal. "
        "Find connected storylines, entity paths, and storyline clusters."
    )
    parameters = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": list(VALID_OPERATIONS),
            },
            "source": {"type": "integer", "description": "Source storyline ID"},
            "target": {"type": "integer", "description": "Target storyline ID (for entity_path)"},
            "max_depth": {"type": "integer", "default": 3},
            "weight_threshold": {"type": "number", "default": 0.3},
        },
        "required": ["operation"],
    }

    def _execute(self, **kwargs) -> ToolResult:
        operation: str = kwargs.get("operation", "")
        source: Optional[int] = kwargs.get("source")
        target: Optional[int] = kwargs.get("target")
        max_depth: int = min(kwargs.get("max_depth", 3), 5)
        weight_threshold: float = kwargs.get("weight_threshold", 0.3)

        if operation not in VALID_OPERATIONS:
            return ToolResult(success=False, data=None, error=f"Invalid operation: {operation}")

        method = getattr(self, f"_op_{operation}", None)
        if method is None:
            return ToolResult(success=False, data=None, error=f"No handler for {operation}")

        return method(source=source, target=target, max_depth=max_depth, weight_threshold=weight_threshold)

    # ── Operations ────────────────────────────────────────────────────────────

    def _op_connected_storylines(self, source, target, max_depth, weight_threshold) -> ToolResult:
        if source is None:
            return ToolResult(success=False, data=None, error="source storyline ID required")

        query = """
            WITH RECURSIVE storyline_graph AS (
                SELECT source_story_id, target_story_id, weight, relation_type, 1 AS depth
                FROM storyline_edges
                WHERE source_story_id = %s AND weight >= %s
                UNION ALL
                SELECT se.source_story_id, se.target_story_id, se.weight, se.relation_type, sg.depth + 1
                FROM storyline_edges se
                JOIN storyline_graph sg ON se.source_story_id = sg.target_story_id
                WHERE sg.depth < %s AND se.weight >= %s
            )
            SELECT DISTINCT
                s.id, s.title, s.narrative_status, s.momentum_score,
                sg.weight, sg.depth, sg.relation_type
            FROM storyline_graph sg
            JOIN storylines s ON s.id = sg.target_story_id
            ORDER BY sg.depth, sg.weight DESC
            LIMIT 50
        """
        params = [source, weight_threshold, max_depth, weight_threshold]
        return self._run_query(query, params, operation="connected_storylines")

    def _op_entity_path(self, source, target, max_depth, weight_threshold) -> ToolResult:
        if source is None or target is None:
            return ToolResult(success=False, data=None, error="Both source and target IDs required")

        query = """
            SELECT
                e.id AS entity_id, e.name, e.entity_type,
                COUNT(DISTINCT em1.article_id) AS shared_articles
            FROM entity_mentions em1
            JOIN entity_mentions em2 ON em1.article_id = em2.article_id
            JOIN entities e ON em1.entity_id = e.id
            JOIN article_storylines as1 ON as1.article_id = em1.article_id AND as1.storyline_id = %s
            JOIN article_storylines as2 ON as2.article_id = em2.article_id AND as2.storyline_id = %s
            WHERE em1.entity_id = em2.entity_id
            GROUP BY e.id, e.name, e.entity_type
            ORDER BY shared_articles DESC
            LIMIT 20
        """
        params = [source, target]
        return self._run_query(query, params, operation="entity_path")

    def _op_storyline_cluster(self, source, target, max_depth, weight_threshold) -> ToolResult:
        query = """
            SELECT
                s.id, s.title, s.narrative_status, s.momentum_score,
                COUNT(se.target_story_id) AS connection_count,
                ROUND(AVG(se.weight)::numeric, 3) AS avg_weight
            FROM storylines s
            JOIN storyline_edges se ON se.source_story_id = s.id
            WHERE se.weight >= %s
            GROUP BY s.id, s.title, s.narrative_status, s.momentum_score
            HAVING COUNT(se.target_story_id) >= 2
            ORDER BY connection_count DESC, avg_weight DESC
            LIMIT 20
        """
        params = [weight_threshold]
        return self._run_query(query, params, operation="storyline_cluster")

    # ── Utilities ─────────────────────────────────────────────────────────────

    def _run_query(self, query: str, params: List, operation: str) -> ToolResult:
        try:
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    rows = cur.fetchall()
                    columns = [d[0] for d in cur.description] if cur.description else []
                    conn.rollback()

            data = {"results": [dict(zip(columns, row)) for row in rows], "columns": columns}
            return ToolResult(success=True, data=data, metadata={"operation": operation, "rows": len(rows)})
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))

    def _format_success(self, data: Any, metadata: Dict) -> str:
        rows = data.get("results", [])
        operation = metadata.get("operation", "")

        if not rows:
            return "[Graph: no relationships found]"

        if operation == "connected_storylines":
            # Group by depth
            by_depth: Dict[int, List] = {}
            for row in rows:
                d = row.get("depth", 0)
                by_depth.setdefault(d, []).append(row)
            lines = []
            for depth in sorted(by_depth):
                lines.append(f"\nDepth {depth}:")
                for r in by_depth[depth]:
                    lines.append(
                        f"  - [{r.get('id')}] {r.get('title', '')} "
                        f"(weight={r.get('weight', 0):.2f}, status={r.get('narrative_status', '')})"
                    )
            return "Connected Storylines:" + "\n".join(lines)

        if operation == "entity_path":
            lines = [
                f"- {r.get('name')} ({r.get('entity_type')}) — {r.get('shared_articles')} shared articles"
                for r in rows
            ]
            return "Shared Entities:\n" + "\n".join(lines)

        if operation == "storyline_cluster":
            lines = [
                f"- [{r.get('id')}] {r.get('title', '')} "
                f"(connections={r.get('connection_count')}, avg_weight={r.get('avg_weight', 0)})"
                for r in rows
            ]
            return "Storyline Clusters:\n" + "\n".join(lines)

        return str(rows[:5])
