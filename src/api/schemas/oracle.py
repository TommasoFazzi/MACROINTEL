"""Pydantic schemas for Oracle 2.0 API endpoints."""

from datetime import date
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class OracleChatRequest(BaseModel):
    query: str = Field(..., min_length=3, max_length=2000)
    session_id: str = Field(default="default", max_length=64)
    mode: Literal["both", "factual", "strategic"] = "both"
    search_type: Literal["vector", "keyword", "hybrid"] = "hybrid"
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    categories: Optional[List[Literal[
        "GEOPOLITICS", "DEFENSE", "ECONOMY", "CYBER", "ENERGY"
    ]]] = None
    gpe_filter: Optional[List[str]] = None
    # BREAKING CHANGE (2026-04-17): gemini_api_key field removed.
    # Oracle now uses server-side ANTHROPIC_API_KEY. Passing this field returns HTTP 422.


class OracleSource(BaseModel):
    type: Literal["REPORT", "ARTICOLO"]
    id: Optional[int] = None
    title: str
    date_str: Optional[str] = None
    similarity: float = 0.0
    status: Optional[str] = None
    preview: Optional[str] = None
    link: Optional[str] = None
    source: Optional[str] = None


class OracleChatResponse(BaseModel):
    answer: str
    sources: List[OracleSource] = []
    query_plan: Optional[Dict[str, Any]] = None
    mode: str = "both"
    metadata: Dict[str, Any] = {}
