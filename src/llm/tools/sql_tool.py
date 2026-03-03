"""SQLTool — executes LLM-generated SQL with 5-layer safety validation."""

import re
from typing import Any, Dict, List, Optional, Set

import sqlparse
import sqlparse.tokens as T

from .base import BaseTool, ToolResult
from ...utils.logger import get_logger

logger = get_logger(__name__)

ALLOWED_TABLES: Set[str] = {
    "articles",
    "chunks",
    "reports",
    "storylines",
    "entities",
    "entity_mentions",
    "trade_signals",
    "macro_indicators",
    "market_data",
    "article_storylines",
    "storyline_edges",
    "v_active_storylines",
    "v_storyline_graph",
}

FORBIDDEN_KEYWORDS = {
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
    "TRUNCATE", "GRANT", "REVOKE", "EXECUTE", "EXEC",
    "COPY", "VACUUM", "ANALYZE", "CLUSTER", "REINDEX",
}

MAX_JOINS = 3
MAX_COST = 10000.0
STATEMENT_TIMEOUT_MS = 5000


class SQLTool(BaseTool):
    name = "sql_query"
    description = (
        "Execute a read-only SQL query against the intelligence database. "
        "Applies 5-layer safety validation before execution."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "SQL SELECT query to execute"},
        },
        "required": ["query"],
    }

    def _execute(self, **kwargs) -> ToolResult:
        raw_query: Optional[str] = kwargs.get("query")
        if not raw_query:
            return ToolResult(success=False, data=None, error="No SQL query provided")

        # ── Layer 1: sqlparse parsing ────────────────────────────────────────
        try:
            statements = sqlparse.parse(raw_query.strip())
        except Exception as e:
            return ToolResult(success=False, data=None, error=f"SQL parse error: {e}")

        if len(statements) != 1:
            return ToolResult(
                success=False, data=None,
                error="Only a single SQL statement is allowed"
            )
        parsed = statements[0]

        # ── Layer 2: Forbidden keyword check (token-level, not regex) ────────
        forbidden_found = self._check_forbidden_keywords(parsed)
        if forbidden_found:
            return ToolResult(
                success=False, data=None,
                error=f"Forbidden SQL keyword(s): {', '.join(forbidden_found)}"
            )

        # Ensure it's a SELECT statement
        stmt_type = parsed.get_type()
        if stmt_type != "SELECT":
            return ToolResult(
                success=False, data=None,
                error=f"Only SELECT statements are allowed (got: {stmt_type})"
            )

        # ── Layer 3: Max JOIN complexity ──────────────────────────────────────
        join_count = self._count_joins(parsed)
        if join_count > MAX_JOINS:
            return ToolResult(
                success=False, data=None,
                error=f"Too many JOINs: {join_count} (max {MAX_JOINS})"
            )

        # ── Layer 4: LIMIT enforcement ────────────────────────────────────────
        safe_query = self._enforce_limit(raw_query.strip(), parsed)

        # ── Layer 5: EXPLAIN cost pre-check + execution ───────────────────────
        return self._run_with_safety(safe_query)

    # ── Helper methods ────────────────────────────────────────────────────────

    def _check_forbidden_keywords(self, parsed) -> List[str]:
        found = []
        flat = list(parsed.flatten())
        for token in flat:
            if token.ttype in (T.Keyword, T.Keyword.DML, T.Keyword.DDL):
                upper = token.normalized.upper()
                if upper in FORBIDDEN_KEYWORDS:
                    found.append(upper)
        return found

    def _count_joins(self, parsed) -> int:
        count = 0
        for token in parsed.flatten():
            if token.ttype is T.Keyword and token.normalized.upper() == "JOIN":
                count += 1
        return count

    def _enforce_limit(self, query: str, parsed) -> str:
        has_limit = any(
            t.ttype is T.Keyword and t.normalized.upper() == "LIMIT"
            for t in parsed.flatten()
        )
        if not has_limit:
            # Strip trailing semicolon before appending LIMIT
            q = query.rstrip().rstrip(";")
            return f"{q} LIMIT 1000"
        return query

    def _run_with_safety(self, query: str) -> ToolResult:
        try:
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    # Set statement timeout (read-only enforcement)
                    cur.execute(f"SET statement_timeout = '{STATEMENT_TIMEOUT_MS}'")
                    conn.commit()

                    # EXPLAIN cost check
                    cur.execute(f"EXPLAIN {query}")
                    explain_rows = cur.fetchall()
                    explain_output = "\n".join(str(r[0]) for r in explain_rows)
                    cost_match = re.search(r"cost=\d+\.\d+\.\.(\d+\.\d+)", explain_output)
                    if cost_match and float(cost_match.group(1)) > MAX_COST:
                        conn.rollback()
                        return ToolResult(
                            success=False, data=None,
                            error=f"Query too complex (estimated cost {float(cost_match.group(1)):.0f} > {MAX_COST:.0f})"
                        )

                    # Execute
                    cur.execute(query)
                    rows = cur.fetchall()
                    columns = [desc[0] for desc in cur.description] if cur.description else []
                    conn.rollback()  # ensure read-only — no side-effects

            data = {"results": [dict(zip(columns, row)) for row in rows], "columns": columns}
            return ToolResult(
                success=True, data=data,
                metadata={"rows_returned": len(rows), "columns": columns}
            )

        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))

    def _format_success(self, data: Any, metadata: Dict) -> str:
        rows = data.get("results", [])
        columns = data.get("columns", [])
        if not rows:
            return "[SQL: query returned 0 rows]"

        header = " | ".join(columns)
        sep = "-" * len(header)
        lines = [header, sep]
        for row in rows[:20]:
            lines.append(" | ".join(str(row.get(c, "")) for c in columns))

        suffix = f"\n[...{len(rows) - 20} more rows]" if len(rows) > 20 else ""
        return "\n".join(lines) + suffix
