"""TickerThemesTool — find storylines correlated to market tickers."""

from typing import Any, Dict, Optional

from .base import BaseTool, ToolResult
from ...services.ticker_service import get_themes_for_ticker
from ...utils.logger import get_logger

logger = get_logger(__name__)


class TickerThemesTool(BaseTool):
    """Find and analyze storylines correlated to a specific market ticker symbol."""

    name = "ticker_themes"
    description = (
        "Find storylines and narrative themes correlated to a market ticker symbol. "
        "Returns the top N storylines for a given ticker with momentum scores and article counts."
    )
    parameters = {
        "type": "object",
        "properties": {
            "rationale": {
                "type": "string",
                "description": "PATH TICKER: Explain why you're looking up this ticker and what narrative themes you expect to find correlated with it.",
            },
            "ticker": {
                "type": "string",
                "description": "Market ticker symbol (e.g., RTX, NVDA, TSLA)",
            },
            "top_n": {
                "type": "integer",
                "description": "Maximum number of storylines to return (1-20)",
            },
            "days": {
                "type": "integer",
                "description": "Look back this many days for articles (1-365)",
            },
        },
        "required": ["rationale", "ticker"],
    }

    def _execute(self, **kwargs) -> ToolResult:
        """Execute ticker theme search."""
        ticker: str = kwargs.get("ticker", "").upper()
        top_n: int = min(kwargs.get("top_n", 5), 20)
        days: int = min(kwargs.get("days", 30), 365)

        if not ticker:
            return ToolResult(
                success=False,
                data=None,
                error="Ticker symbol is required",
            )

        try:
            result = get_themes_for_ticker(self.db, ticker, days=days, top_n=top_n)
            logger.info(
                f"TickerThemesTool: Found {result.get('total_themes', 0)} themes for {ticker}"
            )
            return ToolResult(
                success=True,
                data=result,
                metadata={
                    "ticker": result.get("ticker"),
                    "themes_count": result.get("total_themes"),
                },
            )

        except ValueError as e:
            # Ticker not found
            logger.warning(f"TickerThemesTool: Ticker not found: {e}")
            return ToolResult(
                success=False,
                data=None,
                error=f"Ticker '{ticker}' not found in configuration",
            )
        except Exception as e:
            logger.error(f"TickerThemesTool: Execution error: {e}", exc_info=True)
            return ToolResult(
                success=False,
                data=None,
                error=f"Failed to find themes for ticker {ticker}: {str(e)}",
            )

    def _format_success(self, data: Dict[str, Any], metadata: Dict[str, Any]) -> str:
        """Format ticker themes result for LLM consumption."""
        if not data or not data.get("themes"):
            return f"No storylines found for ticker {data.get('ticker', 'UNKNOWN')} in the specified period."

        ticker = data.get("ticker", "")
        name = data.get("name", "")
        days = data.get("days", 30)
        themes = data.get("themes", [])

        lines = [
            f"TICKER: {ticker} ({name})",
            f"Found {len(themes)} storylines in the past {days} days:\n",
        ]

        for i, theme in enumerate(themes, 1):
            momentum = theme.get("momentum_score", 0.0)
            article_count = theme.get("article_count", 0)
            title = theme.get("title", "Unknown")
            lines.append(
                f"{i}. [{momentum:.2f} momentum] {title} — {article_count} articles"
            )

        return "\n".join(lines)
