"""MarketTool — trade signals and macro indicators analysis."""

from typing import Any, Dict, List, Optional

from .base import BaseTool, ToolResult
from ...utils.logger import get_logger

logger = get_logger(__name__)

VALID_ANALYSIS_TYPES = {"signals_filter", "macro_correlation", "valuation_screen"}
VALID_TIMEFRAMES = {"SHORT_TERM", "MEDIUM_TERM", "LONG_TERM"}


class MarketTool(BaseTool):
    name = "market_analysis"
    description = (
        "Analyze trade signals and macroeconomic indicators. "
        "Filter signals by confidence, screen valuations, correlate macro with geopolitical events."
    )
    parameters = {
        "type": "object",
        "properties": {
            "analysis_type": {
                "type": "string",
                "enum": list(VALID_ANALYSIS_TYPES),
            },
            "filters": {
                "type": "object",
                "description": "Optional: signal ('BUY'|'SELL'|'HOLD'), min_confidence, min_intel_score, ticker",
            },
            "timeframe": {
                "type": "string",
                "enum": list(VALID_TIMEFRAMES),
                "default": "SHORT_TERM",
            },
        },
        "required": ["analysis_type"],
    }

    def _execute(self, **kwargs) -> ToolResult:
        analysis_type: str = kwargs.get("analysis_type", "")
        filters: Dict = kwargs.get("filters") or {}
        timeframe: str = kwargs.get("timeframe", "SHORT_TERM")

        if analysis_type not in VALID_ANALYSIS_TYPES:
            return ToolResult(success=False, data=None, error=f"Invalid analysis_type: {analysis_type}")
        if timeframe not in VALID_TIMEFRAMES:
            timeframe = "SHORT_TERM"

        method = getattr(self, f"_analysis_{analysis_type}", None)
        if method is None:
            return ToolResult(success=False, data=None, error=f"No handler for {analysis_type}")

        return method(filters=filters, timeframe=timeframe)

    # ── Analysis methods ──────────────────────────────────────────────────────

    def _analysis_signals_filter(self, filters: Dict, timeframe: str) -> ToolResult:
        conditions = ["timeframe = %s"]
        params: List = [timeframe]

        if filters.get("signal"):
            conditions.append("signal = %s")
            params.append(filters["signal"].upper())
        if filters.get("min_confidence") is not None:
            conditions.append("confidence >= %s")
            params.append(float(filters["min_confidence"]))
        if filters.get("min_intel_score") is not None:
            conditions.append("intelligence_score >= %s")
            params.append(float(filters["min_intel_score"]))
        if filters.get("ticker"):
            conditions.append("ticker ILIKE %s")
            params.append(f"%{filters['ticker']}%")

        where = "WHERE " + " AND ".join(conditions)
        query = f"""
            SELECT
                ticker, signal, confidence, intelligence_score,
                timeframe, rationale, created_at
            FROM trade_signals
            {where}
            ORDER BY intelligence_score DESC NULLS LAST, confidence DESC NULLS LAST
            LIMIT 20
        """
        return self._run_query(query, params, analysis_type="signals_filter")

    def _analysis_macro_correlation(self, filters: Dict, timeframe: str) -> ToolResult:
        query = """
            SELECT
                mi.indicator_name, mi.value, mi.previous_value,
                mi.change_pct, mi.interpretation, mi.label, mi.emoji,
                mi.created_at
            FROM macro_indicators mi
            ORDER BY mi.created_at DESC
            LIMIT 20
        """
        return self._run_query(query, [], analysis_type="macro_correlation")

    def _analysis_valuation_screen(self, filters: Dict, timeframe: str) -> ToolResult:
        min_intel = filters.get("min_intel_score", 0.5)
        query = """
            SELECT
                ticker, signal, confidence, intelligence_score,
                timeframe, rationale
            FROM trade_signals
            WHERE signal = 'BUY'
              AND intelligence_score >= %s
            ORDER BY intelligence_score DESC NULLS LAST, confidence DESC NULLS LAST
            LIMIT 15
        """
        return self._run_query(query, [min_intel], analysis_type="valuation_screen")

    # ── Utilities ─────────────────────────────────────────────────────────────

    def _run_query(self, query: str, params: List, analysis_type: str) -> ToolResult:
        try:
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    rows = cur.fetchall()
                    columns = [d[0] for d in cur.description] if cur.description else []
                    conn.rollback()

            data = {"results": [dict(zip(columns, row)) for row in rows], "columns": columns}
            return ToolResult(
                success=True, data=data,
                metadata={"analysis_type": analysis_type, "rows": len(rows)}
            )
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))

    def _format_success(self, data: Any, metadata: Dict) -> str:
        rows = data.get("results", [])
        analysis_type = metadata.get("analysis_type", "")

        if not rows:
            return f"[Market {analysis_type}: no results]"

        if analysis_type == "signals_filter":
            lines = []
            for r in rows:
                conf_pct = f"{float(r.get('confidence', 0)) * 100:.0f}%" if r.get("confidence") else "N/A"
                intel = f"{float(r.get('intelligence_score', 0)):.2f}" if r.get("intelligence_score") else "N/A"
                lines.append(
                    f"- {r.get('ticker')} [{r.get('signal')}] conf={conf_pct} intel={intel} | {str(r.get('rationale', ''))[:80]}"
                )
            return f"Trade Signals ({metadata.get('rows')} results):\n" + "\n".join(lines)

        if analysis_type == "macro_correlation":
            lines = []
            for r in rows:
                emoji = r.get("emoji", "")
                lines.append(
                    f"{emoji} {r.get('indicator_name', '')}: {r.get('value', '')} "
                    f"({r.get('change_pct', '')}%) — {r.get('label', '')}"
                )
            return "Macro Indicators:\n" + "\n".join(lines)

        if analysis_type == "valuation_screen":
            lines = []
            for i, r in enumerate(rows, 1):
                conf_pct = f"{float(r.get('confidence', 0)) * 100:.0f}%" if r.get("confidence") else "N/A"
                lines.append(
                    f"{i}. {r.get('ticker')} — intel={r.get('intelligence_score', 'N/A')} conf={conf_pct} | {str(r.get('rationale', ''))[:80]}"
                )
            return "Valuation Screen (BUY opportunities):\n" + "\n".join(lines)

        return str(rows[:5])
