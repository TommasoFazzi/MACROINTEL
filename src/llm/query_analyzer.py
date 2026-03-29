"""
Query Analyzer - Pre-search filter extraction using Gemini LLM

Extracts structured filters (dates, categories, GPE, sources) from natural language
queries BEFORE vector search, solving the temporal constraint problem.

Problem: Vector search finds semantically similar content but can't filter by metadata
like publication dates. "What happened on December 15th?" fails because embeddings
don't encode temporal constraints.

Solution: Use Gemini to parse the query and extract structured filters that can be
passed to the database query.

Example:
    Input: "Cosa e successo a Taiwan negli ultimi 7 giorni?"
    Output: ExtractedFilters(
        gpe_filter=['Taiwan'],
        start_date='2024-12-26',
        end_date='2025-01-02',
        semantic_query='Taiwan events developments',
        extraction_confidence=0.9
    )
"""

import os
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from pathlib import Path

# Load .env file
from dotenv import load_dotenv
env_path = Path(__file__).parent.parent.parent / '.env'
load_dotenv(env_path)

import google.generativeai as genai
from pydantic import ValidationError

from .schemas import ExtractedFilters
from ..utils.logger import get_logger

logger = get_logger(__name__)


class QueryAnalyzer:
    """
    Extracts structured filters from natural language queries using Gemini LLM.

    Designed for low latency (<500ms) with Gemini Flash.
    Includes fallback to return original query if extraction fails.

    Usage:
        analyzer = QueryAnalyzer()
        result = analyzer.analyze("Cosa e successo a Taiwan negli ultimi 7 giorni?")

        if result['success']:
            filters = result['filters']
            # filters['start_date'] -> datetime object
            # filters['gpe_filter'] -> ['Taiwan']
    """

    def __init__(
        self,
        gemini_api_key: Optional[str] = None,
        model_name: str = "gemini-2.5-flash"
    ):
        """
        Initialize Query Analyzer.

        Args:
            gemini_api_key: Gemini API key (reads from env if None)
            model_name: Gemini model to use (default: gemini-2.5-flash for speed)
        """
        api_key = (gemini_api_key or os.getenv('GEMINI_API_KEY', '')).strip()
        if not api_key:
            raise ValueError("GEMINI_API_KEY not found in environment or parameters")

        genai.configure(api_key=api_key, transport='rest')
        self.model = genai.GenerativeModel(model_name)
        self.model_name = model_name

        logger.info(f"QueryAnalyzer initialized with {model_name}")

    def _build_prompt(self, query: str, current_date: str) -> str:
        """Build the extraction prompt with rules for date/category/GPE parsing."""

        return f"""You are a query analyzer for an intelligence database search system.
Your task: Extract structured filters from the user's natural language query.

CURRENT DATE: {current_date}

USER QUERY:
"{query}"

EXTRACTION RULES:

1. **DATES** (output in ISO format YYYY-MM-DD):
   - "ultimi X giorni/settimane/mesi" -> calculate start_date relative to today
   - "da [mese/data]" -> set start_date, end_date = today
   - "il 15 dicembre" or "15 dicembre" -> start_date = end_date = that specific date
   - "tra X e Y" -> explicit date range
   - "ieri" -> yesterday's date for both start and end
   - "questa settimana" -> from Monday of current week to today
   - If just "ultimi" or "recenti" without a number, set dates to null
   - IMPORTANT: Always calculate dates relative to CURRENT DATE above

2. **CATEGORIES** (infer from keywords - can be multiple):
   - GEOPOLITICS: diplomacy, sanctions, treaties, elections, political leaders, tensions
   - DEFENSE: military, weapons, troops, NATO, wars, defense contractors, army, navy
   - ECONOMY: markets, trade, GDP, inflation, central banks, tariffs, stocks, finance
   - CYBER: hacking, malware, data breach, ransomware, cyber attack, cybersecurity
   - ENERGY: oil, gas, OPEC, renewables, pipelines, energy security, petroleum

3. **GPE (Geographic Entities)** - normalize to English:
   - "Cina" -> "China"
   - "Stati Uniti" / "America" -> "USA"
   - "Corea del Nord" -> "North Korea"
   - "Medio Oriente" -> "Middle East"
   - "russo/russe" -> "Russia"
   - "taiwanese" -> "Taiwan"
   - Include geopolitical regions: "South China Sea", "Europe", "Asia Pacific"

4. **SOURCES** (only if explicitly mentioned):
   - "report Reuters" / "secondo Reuters" -> ["Reuters"]
   - "articoli Bloomberg" -> ["Bloomberg"]
   - Common sources: Reuters, Bloomberg, ANSA, BBC, Financial Times, WSJ

5. **SEMANTIC_QUERY** (optimized for embedding search):
   - REMOVE temporal expressions ("negli ultimi 7 giorni", "da settembre", "ieri")
   - REMOVE filter words ("report", "notizie", "articoli", "cosa e successo")
   - KEEP the core information need, entities, and topics
   - Should be 3-10 words maximum

6. **CONFIDENCE** (0.0 to 1.0):
   - 0.9+: Clear query with explicit filters
   - 0.7-0.8: Some inference needed but reasonable
   - 0.5-0.6: Ambiguous, filters may be inaccurate
   - Use null for fields where data is not clearly present

OUTPUT FORMAT: Valid JSON matching the schema. Use null for empty optional fields.

{{
  "start_date": "YYYY-MM-DD" or null,
  "end_date": "YYYY-MM-DD" or null,
  "categories": ["CATEGORY1", "CATEGORY2"] or null,
  "gpe_filter": ["Country1", "Region2"] or null,
  "sources": ["Source1"] or null,
  "semantic_query": "optimized search terms",
  "extraction_confidence": 0.0-1.0
}}

JSON OUTPUT:"""

    def analyze(self, query: str, reference_date: Optional[str] = None) -> Dict[str, Any]:
        """
        Analyze query and extract structured filters.

        Args:
            query: User's natural language query
            reference_date: Optional ISO date string (YYYY-MM-DD) to use as "today".
                            If None, uses the actual current date. Inject a fixed date
                            in tests to make temporal assertions deterministic.

        Returns:
            Dictionary with:
            - success: bool
            - filters: dict with extracted filters (datetime objects for dates)
            - original_query: str
            - error: str (if failed)
        """
        current_date = reference_date if reference_date else datetime.now().strftime("%Y-%m-%d")

        try:
            prompt = self._build_prompt(query, current_date)

            # Use JSON mode for structured output
            response = self.model.generate_content(
                contents=[prompt],
                generation_config={
                    "response_mime_type": "application/json",
                    "temperature": 0.1,
                    "max_output_tokens": 2048,
                },
                request_options={"timeout": 30}
            )

            raw_output = response.text
            logger.debug(f"Raw LLM output: {raw_output}")

            # Validate with Pydantic
            validated = ExtractedFilters.model_validate_json(raw_output)

            # Convert to dict and post-process dates to datetime objects
            filters_dict = validated.model_dump()
            filters_dict = self._post_process_dates(filters_dict)

            logger.info(
                f"Query analyzed: GPE={filters_dict.get('gpe_filter')}, "
                f"dates={filters_dict.get('start_date')}->{filters_dict.get('end_date')}, "
                f"categories={filters_dict.get('categories')}, "
                f"confidence={filters_dict.get('extraction_confidence'):.0%}"
            )

            return {
                'success': True,
                'filters': filters_dict,
                'original_query': query,
                'raw_llm_output': raw_output
            }

        except ValidationError as e:
            logger.warning(f"Filter extraction validation failed: {e}")
            return self._fallback_response(query, f"Validation error: {e}")

        except Exception as e:
            logger.error(f"Query analysis failed: {e}")
            return self._fallback_response(query, str(e))

    def _post_process_dates(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        """Convert ISO date strings to datetime objects."""

        if filters.get('start_date'):
            try:
                filters['start_date'] = datetime.fromisoformat(filters['start_date'])
            except (ValueError, TypeError):
                logger.warning(f"Could not parse start_date: {filters['start_date']}")
                filters['start_date'] = None

        if filters.get('end_date'):
            try:
                filters['end_date'] = datetime.fromisoformat(filters['end_date'])
            except (ValueError, TypeError):
                logger.warning(f"Could not parse end_date: {filters['end_date']}")
                filters['end_date'] = None

        return filters

    def _fallback_response(self, query: str, error: str) -> Dict[str, Any]:
        """Return fallback when extraction fails - proceed with unfiltered search."""

        return {
            'success': False,
            'filters': {
                'start_date': None,
                'end_date': None,
                'categories': None,
                'gpe_filter': None,
                'sources': None,
                'semantic_query': query,  # Use original query
                'extraction_confidence': 0.0
            },
            'original_query': query,
            'error': error
        }


# =============================================================================
# FILTER MERGER - Combine extracted + UI filters
# =============================================================================

def merge_filters(
    extracted: Dict[str, Any],
    ui_start_date: Optional[datetime] = None,
    ui_end_date: Optional[datetime] = None,
    ui_categories: Optional[List[str]] = None,
    ui_gpe_filter: Optional[List[str]] = None,
    ui_sources: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Merge extracted filters with UI-provided filters.

    UI filters take precedence over extracted filters (user's explicit choice).

    Args:
        extracted: Output from QueryAnalyzer.analyze()['filters']
        ui_*: Explicit filters from UI widgets

    Returns:
        Merged filter dictionary ready for database query
    """

    merged = {
        'start_date': ui_start_date if ui_start_date is not None else extracted.get('start_date'),
        'end_date': ui_end_date if ui_end_date is not None else extracted.get('end_date'),
        'categories': ui_categories if ui_categories else extracted.get('categories'),
        'gpe_filter': ui_gpe_filter if ui_gpe_filter else extracted.get('gpe_filter'),
        'sources': ui_sources if ui_sources else extracted.get('sources'),
    }

    # Use semantic_query for embedding if available, otherwise original
    merged['query_for_embedding'] = extracted.get('semantic_query') or ''

    return merged


# =============================================================================
# SINGLETON FACTORY
# =============================================================================

_analyzer_instance: Optional[QueryAnalyzer] = None


def get_query_analyzer() -> QueryAnalyzer:
    """Get or create singleton QueryAnalyzer instance."""
    global _analyzer_instance

    if _analyzer_instance is None:
        _analyzer_instance = QueryAnalyzer()

    return _analyzer_instance
