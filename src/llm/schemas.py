"""
Pydantic schemas for structured LLM output validation

Sprint 2.1 MVP: Simplified schema for initial testing
Sprint 2.2: Full schema with nested models (trade signals, impact scores)
"""

from pydantic import BaseModel, Field, ValidationError
from typing import Literal, Optional, List, Dict, Any
from enum import Enum


class IntelligenceReportMVP(BaseModel):
    """
    Minimal schema for Sprint 2.1 MVP validation

    Focus: Validate JSON mode works reliably before expanding to complex nested models
    Success criteria: 95%+ validation success rate on real articles
    """

    title: str = Field(
        ...,
        description="Concise article title (5-15 words)",
        min_length=10,
        max_length=200
    )

    category: Literal["GEOPOLITICS", "DEFENSE", "ECONOMY", "CYBER", "ENERGY", "OTHER"] = Field(
        ...,
        description="Primary category for article classification"
    )

    executive_summary: str = Field(
        ...,
        description="BLUF-style summary: Bottom Line Up Front (100-300 words)",
        min_length=100,
        max_length=1500
    )

    sentiment_label: Literal["POSITIVE", "NEUTRAL", "NEGATIVE"] = Field(
        ...,
        description="Overall sentiment towards investment/security outlook"
    )

    confidence_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Analyst confidence in the assessment (0.0 = low, 1.0 = high)"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "title": "China Expands Military Presence in South China Sea",
                "category": "GEOPOLITICS",
                "executive_summary": "BLUF: China deployed 3 additional Type 055 destroyers to Hainan Naval Base...",
                "sentiment_label": "NEGATIVE",
                "confidence_score": 0.85
            }
        }


# ============================================================================
# SPRINT 2.2 - FULL SCHEMA (Implement AFTER MVP validates)
# ============================================================================
# Uncomment and test after MVP achieves 95%+ success rate

class ImpactScore(BaseModel):
    """Nested model for impact assessment"""
    score: int = Field(..., ge=0, le=10, description="Impact severity (0-10 scale)")
    reasoning: str = Field(..., description="Justification for impact score")


class SentimentAnalysis(BaseModel):
    """Enhanced sentiment with numeric score"""
    label: Literal["POSITIVE", "NEUTRAL", "NEGATIVE"]
    score: float = Field(..., ge=-1.0, le=1.0, description="Sentiment polarity (-1.0 to +1.0)")


class TradeSignal(BaseModel):
    """Trade recommendation with context"""
    ticker: str = Field(..., description="Stock ticker symbol (e.g., 'LMT', 'NVDA')")
    signal: Literal["BULLISH", "BEARISH", "NEUTRAL", "WATCHLIST"]
    timeframe: Literal["SHORT_TERM", "MEDIUM_TERM", "LONG_TERM"]
    rationale: str = Field(..., description="Specific catalyst driving the signal")


class IntelligenceReport(BaseModel):
    """
    Full schema with nested models (Sprint 2.2)

    WARNING: Complex schema - test AFTER MVP validation succeeds
    """
    title: str
    category: Literal["GEOPOLITICS", "DEFENSE", "ECONOMY", "CYBER", "ENERGY"]
    impact: ImpactScore
    sentiment: SentimentAnalysis
    key_entities: list[str] = Field(..., description="Top 5-10 entities mentioned")
    related_tickers: list[TradeSignal] = Field(..., description="Trade signals with tickers")
    executive_summary: str
    analysis_content: str = Field(..., description="Full markdown analysis")
    confidence_score: float = Field(ge=0.0, le=1.0)

    class Config:
        json_schema_extra = {
            "example": {
                "title": "Taiwan Semiconductor Reports Record Q4 Earnings",
                "category": "ECONOMY",
                "impact": {
                    "score": 8,
                    "reasoning": "TSMC is critical supplier for 90% of advanced chips globally"
                },
                "sentiment": {
                    "label": "POSITIVE",
                    "score": 0.75
                },
                "key_entities": ["Taiwan Semiconductor", "NVIDIA", "Apple", "China"],
                "related_tickers": [
                    {
                        "ticker": "TSM",
                        "signal": "BULLISH",
                        "timeframe": "MEDIUM_TERM",
                        "rationale": "Q4 revenue up 32% YoY driven by AI chip demand"
                    }
                ],
                "executive_summary": "BLUF: TSMC reported Q4 2024 revenue of $23.8B...",
                "analysis_content": "## Key Developments\n\n...",
                "confidence_score": 0.90
            }
        }


# ============================================================================
# MACRO-FIRST PIPELINE SCHEMAS
# ============================================================================
# Used by the serialized pipeline (--macro-first flag) where:
# 1. Macro report is generated first
# 2. Context is condensed for efficiency
# 3. Trade signals are extracted with macro alignment check


class MacroCondensedContext(BaseModel):
    """
    Token-efficient condensation of macro report (~500 tokens).

    Used as context for article-level signal extraction instead of
    passing the full report (5000+ tokens), reducing API costs by ~90%.
    """
    key_themes: list[str] = Field(
        ...,
        description="Top 5-7 strategic themes from the macro report",
        min_length=3,
        max_length=10
    )

    dominant_sentiment: Literal["RISK_ON", "RISK_OFF", "MIXED"] = Field(
        ...,
        description="Overall market sentiment from macro analysis"
    )

    priority_sectors: list[str] = Field(
        ...,
        description="Sectors most affected by current events (e.g., 'Defense', 'Semiconductors')",
        max_length=5
    )

    tickers_mentioned: list[str] = Field(
        ...,
        description="Tickers explicitly mentioned in the macro report",
        max_length=20
    )

    geopolitical_hotspots: list[str] = Field(
        ...,
        description="Active geopolitical regions (e.g., 'Taiwan Strait', 'Middle East')",
        max_length=5
    )

    time_horizon_focus: Literal["IMMEDIATE", "SHORT_TERM", "MEDIUM_TERM"] = Field(
        ...,
        description="Primary time horizon of macro concerns"
    )


class ReportLevelSignal(BaseModel):
    """
    Trade signal extracted at macro report level.

    These are HIGH-CONVICTION signals derived from the synthesis of
    multiple articles, not individual article events.

    Financial Intelligence v2 fields (intelligence_score, etc.) are
    populated by ValuationEngine after LLM extraction.
    """
    ticker: str = Field(..., description="Stock ticker symbol from whitelist")
    signal: Literal["BULLISH", "BEARISH", "NEUTRAL", "WATCHLIST"]
    timeframe: Literal["SHORT_TERM", "MEDIUM_TERM", "LONG_TERM"]
    rationale: str = Field(
        ...,
        description="Macro-level rationale spanning multiple articles/themes"
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence in this signal (0.7+ for macro signals)"
    )
    supporting_themes: list[str] = Field(
        ...,
        description="Which macro themes support this signal"
    )

    # Financial Intelligence v2 fields (populated post-LLM by ValuationEngine)
    intelligence_score: Optional[int] = Field(
        None,
        ge=0,
        le=100,
        description="Market-validated score (0-100) combining LLM confidence with technical/fundamental analysis"
    )
    sma_200_deviation: Optional[float] = Field(
        None,
        description="Price deviation from 200-day SMA (%)"
    )
    pe_rel_valuation: Optional[float] = Field(
        None,
        description="P/E ratio relative to sector median (>1 = expensive)"
    )
    valuation_rating: Optional[Literal["UNDERVALUED", "FAIR", "OVERVALUED", "BUBBLE", "LOSS_MAKING", "UNKNOWN"]] = Field(
        None,
        description="Valuation category based on P/E analysis"
    )
    data_quality: Optional[Literal["FULL", "PARTIAL", "INSUFFICIENT"]] = Field(
        None,
        description="Quality of market data available for scoring"
    )


class ArticleLevelSignal(BaseModel):
    """
    Trade signal extracted from individual article WITH macro alignment check.

    Includes alignment_score indicating how well the signal aligns with
    the broader macro narrative. Low alignment may indicate contrarian signal.
    """
    ticker: str = Field(..., description="Stock ticker symbol from whitelist")
    signal: Literal["BULLISH", "BEARISH", "NEUTRAL", "WATCHLIST"]
    timeframe: Literal["SHORT_TERM", "MEDIUM_TERM", "LONG_TERM"]
    rationale: str = Field(..., description="Article-specific catalyst")
    confidence: float = Field(..., ge=0.0, le=1.0)
    alignment_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Alignment with macro narrative (1.0 = perfect, 0.0 = contrarian)"
    )
    alignment_reasoning: str = Field(
        ...,
        description="Explanation of alignment or divergence from macro themes"
    )


class MacroSignalsResult(BaseModel):
    """
    Complete output from report-level signal extraction.
    """
    condensed_context: MacroCondensedContext
    report_signals: list[ReportLevelSignal]
    extraction_timestamp: str


class ArticleSignalsResult(BaseModel):
    """
    Output from article-level signal extraction with macro context.
    """
    article_id: int
    article_title: str
    signals: list[ArticleLevelSignal]
    macro_alignment_summary: str = Field(
        ...,
        description="Brief summary of how this article fits the macro narrative"
    )


# ============================================================================
# QUERY ANALYZER SCHEMA (Query pre-processing for Oracle)
# ============================================================================

class ExtractedFilters(BaseModel):
    """
    Structured filters extracted from natural language query.

    Used by QueryAnalyzer to enable temporal/categorical filtering in RAG.
    Solves the problem of vector search not understanding date constraints.

    Example:
        Input: "Cosa è successo a Taiwan negli ultimi 7 giorni?"
        Output: ExtractedFilters(
            gpe_filter=['Taiwan'],
            start_date='2024-12-26',
            end_date='2025-01-02',
            semantic_query='Taiwan events developments'
        )
    """

    start_date: Optional[str] = Field(
        None,
        description="ISO date (YYYY-MM-DD) for range start. "
                    "Extract from 'ultimi X giorni', 'da settembre', 'il 15 dicembre'."
    )
    end_date: Optional[str] = Field(
        None,
        description="ISO date (YYYY-MM-DD) for range end. Usually today unless specified."
    )
    categories: Optional[list[Literal["GEOPOLITICS", "DEFENSE", "ECONOMY", "CYBER", "ENERGY"]]] = Field(
        None,
        description="Inferred categories. 'cyber attack' -> CYBER, 'difesa' -> DEFENSE"
    )
    gpe_filter: Optional[list[str]] = Field(
        None,
        description="Geographic entities normalized to English: 'Cina' -> 'China'"
    )
    sources: Optional[list[str]] = Field(
        None,
        description="News sources if explicitly mentioned: 'Reuters', 'Bloomberg'"
    )
    semantic_query: str = Field(
        ...,
        description="Query optimized for semantic search - temporal expressions removed"
    )
    extraction_confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence in extraction accuracy (0.0-1.0)"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "start_date": "2024-12-26",
                "end_date": "2025-01-02",
                "categories": ["CYBER"],
                "gpe_filter": ["Russia", "Ukraine"],
                "sources": None,
                "semantic_query": "cyber attacks critical infrastructure",
                "extraction_confidence": 0.85
            }
        }


# ============================================================================
# MACRO DASHBOARD SCHEMAS
# ============================================================================
# Used by the two-step macro reasoning pipeline where:
# 1. LLM interprets raw macro data (temperature 0.45)
# 2. Dashboard is generated with interpretive labels
# 3. Report includes macro context with reasoning

class MacroDashboardItem(BaseModel):
    """
    Single indicator for the macro dashboard display.

    Example: {"indicator": "VIX", "value": "14.2", "change": "+0.5%", "label": "Calm", "emoji": "🟢"}
    """
    indicator: str = Field(
        ...,
        description="Short indicator name (e.g., 'OIL', 'VIX', '10Y', 'DXY')",
        max_length=30  # Increased for longer names like 'INFLATION EXPECTATION 5Y'
    )
    value: str = Field(
        ...,
        description="Formatted value (e.g., '$78.50', '14.2', '4.1%')"
    )
    change: str = Field(
        ...,
        description="Change from previous day (e.g., '-1.2%', '+5bp', 'flat')"
    )
    label: str = Field(
        ...,
        description="Interpretive label (e.g., 'Calm', 'Elevated Fear', 'Supply Concern')",
        max_length=30
    )
    emoji: str = Field(
        default="",
        description="Status emoji (e.g., '🟢', '🔴', '⚠️', '📈')"
    )


class MacroAnalysisResult(BaseModel):
    """
    Complete output from Step 1 macro interpretation.

    LLM analyzes all 16 macro indicators from OpenBB and generates:
    - Dashboard items with interpretive labels
    - Risk regime classification
    - Narrative explaining the macro environment
    """
    dashboard_items: list[MacroDashboardItem] = Field(
        ...,
        description="6-8 key indicators selected for dashboard display",
        min_length=3,
        max_length=10
    )

    risk_regime: Literal[
        "RISK_ON", "RISK_OFF", "MIXED", "TRANSITION",
        "CAUTIOUS", "CAUTIOUS_RISK_ON", "CAUTIOUS_RISK_OFF",
        "NEUTRAL", "DEFENSIVE"
    ] = Field(
        ...,
        description="Overall market risk regime based on indicator synthesis"
    )

    macro_narrative: str = Field(
        ...,
        description="3-4 sentence interpretation of the macro environment",
        min_length=100,
        max_length=1500  # Increased for more detailed narratives
    )

    key_divergences: Optional[list[str]] = Field(
        default=None,
        description="Notable divergences or anomalies to highlight (e.g., 'VIX low despite geopolitical tension')"
    )

    watch_items: Optional[list[str]] = Field(
        default=None,
        description="Indicators to monitor for regime change signals"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "dashboard_items": [
                    {"indicator": "OIL", "value": "$78.50", "change": "-1.2%", "label": "Supply Easing", "emoji": "📉"},
                    {"indicator": "VIX", "value": "14.2", "change": "+0.5", "label": "Calm", "emoji": "🟢"},
                    {"indicator": "10Y YIELD", "value": "4.1%", "change": "+5bp", "label": "Hawkish Hold", "emoji": "📊"},
                    {"indicator": "DXY", "value": "104.2", "change": "-0.3%", "label": "Mild Weakness", "emoji": "💵"},
                    {"indicator": "HY SPREAD", "value": "3.2%", "change": "flat", "label": "No Stress", "emoji": "✅"},
                    {"indicator": "COPPER", "value": "$4.15", "change": "+0.8%", "label": "Growth Signal", "emoji": "🏭"}
                ],
                "risk_regime": "RISK_ON",
                "macro_narrative": "Markets are in a low-volatility, risk-on regime. VIX at 14.2 indicates investor complacency despite Middle East headlines. Oil weakness (-1.2%) suggests demand concerns outweigh supply risks. Copper strength points to intact global growth expectations. Monitor HY spreads for early stress signals.",
                "key_divergences": ["VIX complacent despite elevated geopolitical risk"],
                "watch_items": ["HY spreads", "Yield curve flattening"]
            }
        }


# ── MacroAnalysisResultV2 — Phase 4 output schema ─────────────────────────────

class RiskRegimeV2(BaseModel):
    label: Literal[
        "risk_off_systemic", "risk_off_moderate", "neutral",
        "risk_on_moderate", "risk_on_expansion", "crisis_acute", "stagflationary"
    ]
    confidence: float = Field(..., ge=0.0, le=1.0)
    drivers: List[str] = Field(default_factory=list)


class ActiveConvergenceItemV2(BaseModel):
    id: str
    label: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    narrative: str
    disambiguation_applied: Optional[str] = None


class KeyDivergenceItemV2(BaseModel):
    description: str
    severity: Literal["notable", "significant", "critical"]


class SCSignalItemV2(BaseModel):
    sector: str
    signal: str
    confidence: Literal["low", "medium", "high"]
    monitor_sources: List[str] = Field(default_factory=list)


class DashboardItemV2(BaseModel):
    key: str
    value: float
    delta_pct: float
    materiality: Literal["noise", "notable", "significant"]
    label: str
    note: Optional[str] = None


class MacroAnalysisResultV2(BaseModel):
    """
    Output schema for LLM call #1 (macro_analysis_prompt).
    Phase 4: validated via Pydantic in shadow mode before cutover.
    7 regime labels (Literal-constrained) prevent LLM label drift.
    """
    risk_regime: RiskRegimeV2
    active_convergences: List[ActiveConvergenceItemV2] = Field(default_factory=list)
    macro_narrative: str = Field(..., min_length=50, max_length=600)
    key_divergences: List[KeyDivergenceItemV2] = Field(default_factory=list)
    supply_chain_signals: List[SCSignalItemV2] = Field(default_factory=list)
    dashboard_items: List[DashboardItemV2] = Field(default_factory=list)
    freshness_note: Optional[str] = None
    data_date: str  # ISO YYYY-MM-DD


# ─── Oracle 2.0 Schemas ───────────────────────────────────────────────────────

class QueryIntent(str, Enum):
    FACTUAL = "factual"
    ANALYTICAL = "analytical"
    NARRATIVE = "narrative"
    MARKET = "market"
    COMPARATIVE = "comparative"
    TICKER = "ticker"
    OVERVIEW = "overview"
    REFERENCE = "reference"
    SPATIAL = "spatial"


class QueryComplexity(str, Enum):
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"


class ExecutionStep(BaseModel):
    tool_name: str
    parameters: Dict[str, Any]
    is_critical: bool = True
    description: str = ""


class QueryPlan(BaseModel):
    intent: QueryIntent
    complexity: QueryComplexity
    tools: List[str]
    execution_steps: List[ExecutionStep]
    estimated_time: float
    requires_decomposition: bool = False
    sub_queries: Optional[List[str]] = None
