"""
query_router — legacy config constants (Oracle 2.0 agentic migration).

The QueryRouter class and all LLM-based routing logic have been removed.
Routing is now handled by the Gemini Function Calling agentic loop in
oracle_orchestrator.py using SOPs encoded in the system prompt.

Constants below are kept for reference and backward-compat with eval scripts.
"""

# Few-shot examples per intent category (documentation / eval reference)
INTENT_EXAMPLES = {
    "factual": [
        "Cosa è successo a Taiwan negli ultimi 7 giorni?",
        "Ultime notizie sulla Cina e Taiwan",
        "Cosa ha dichiarato Biden sull'Ucraina?",
    ],
    "analytical": [
        "Quanti articoli sulla Cina negli ultimi 30 giorni?",
        "Qual è il trend delle notizie sull'energia nel 2024?",
        "Distribuzione degli articoli per categoria nell'ultimo mese",
    ],
    "narrative": [
        "Come si è evoluta la narrativa sulla guerra in Ucraina?",
        "Quali storyline sono collegate alla crisi energetica europea?",
        "Mostrami la rete di connessioni tra le storie sul Medio Oriente",
    ],
    "market": [
        "Quali sono i segnali di trading più forti ora?",
        "Mostrami le opportunità BUY con alto intelligence score",
        "Come si correlano gli indicatori macro con gli eventi in Russia?",
    ],
    "comparative": [
        "Confronta la copertura su Cina vs USA negli ultimi 3 mesi",
        "Come è cambiata la situazione in Iran rispetto a 6 mesi fa?",
        "Differenze tra la narrativa europea e americana sulla NATO",
    ],
    "ticker": [
        "Quali sono i temi principali per RTX?",
        "Mostrami le storyline correlate a NVDA",
        "Temi associati al ticker tecnologico",
    ],
    "overview": [
        "Give me a geopolitical overview of Myanmar",
        "Panorama geopolitico dell'Iran",
        "Quadro generale della situazione in Ucraina",
        "Excursus sulla crisi energetica europea",
        "Situazione geopolitica del Medio Oriente",
    ],
}

# Keywords used for keyword-based complexity/intent heuristics (kept for reference)
ANALYTICAL_KEYWORDS = {
    "quanti", "quante", "conteggio", "trend", "distribuzione",
    "statistiche", "analisi", "report", "percentuale", "media",
    "totale", "aggregazione",
}
COMPLEX_KEYWORDS = {
    "confronta", "vs", "versus", "come si è evoluto", "come è cambiato",
    "rispetto a", "paragona", "differenze", "simile a",
}
TICKER_KEYWORDS = {
    "ticker", "azione", "stock", "rtx", "nvda", "msft", "tsm", "temi",
    "storyline", "associato", "correlato", "correlate", "correlati",
}
OVERVIEW_KEYWORDS = {
    "panorama", "panoramica", "overview", "landscape", "excursus",
    "quadro generale", "situazione generale", "quadro geopolitico",
    "geopolitical overview", "geopolitical landscape", "country profile",
    "scenario complessivo", "storia di", "contesto storico",
    "analisi paese", "country analysis", "comprehensive analysis",
}

# Few-Shot SQL examples per table (migrated into SQLTool.description for agentic loop)
# Kept here for eval script backward-compat and documentation.
SQL_EXAMPLES = {
    "conflict_events": (
        "-- Conflicts in a region with fatalities, ordered most recent first:\n"
        "SELECT event_date, country, location, actor1, actor2, fatalities, event_type\n"
        "FROM conflict_events\n"
        "WHERE country ILIKE '%Sudan%'\n"
        "  AND event_date >= CURRENT_DATE - INTERVAL '365 days'\n"
        "ORDER BY event_date DESC LIMIT 50;"
    ),
    "macro_forecasts": (
        "-- Latest IMF GDP growth forecasts for a country (auto-select latest vintage):\n"
        "SELECT mf.year, mf.value, mf.unit, mf.vintage\n"
        "FROM macro_forecasts mf\n"
        "WHERE mf.iso3 = 'DEU' AND mf.indicator_code = 'NGDP_RPCH'\n"
        "  AND mf.vintage = (SELECT MAX(vintage) FROM macro_forecasts WHERE iso3 = 'DEU')\n"
        "ORDER BY mf.year LIMIT 10;"
    ),
    "v_sanctions_public": (
        "-- Sanctioned entities in a country (ISO2), most recent first:\n"
        "SELECT caption, schema_type, datasets, first_seen\n"
        "FROM v_sanctions_public\n"
        "WHERE 'RU' = ANY(countries)\n"
        "ORDER BY last_seen DESC NULLS LAST LIMIT 30;"
    ),
    "country_profiles": (
        "-- Compare GDP and debt for countries in a region:\n"
        "SELECT name, iso3, gdp_usd, gdp_growth, debt_to_gdp, inflation\n"
        "FROM country_profiles\n"
        "WHERE region = 'Middle East & North Africa'\n"
        "ORDER BY gdp_usd DESC NULLS LAST LIMIT 20;"
    ),
    "trade_flow_indicators": (
        "-- Export flows for a country, most recent year first:\n"
        "SELECT tf.year, tf.partner_iso3, cp.name AS partner, tf.value, tf.unit\n"
        "FROM trade_flow_indicators tf\n"
        "LEFT JOIN country_profiles cp ON tf.partner_iso3 = cp.iso3\n"
        "WHERE tf.reporter_iso3 = 'CHN' AND tf.indicator_code = 'EXPORT_VALUE'\n"
        "ORDER BY tf.year DESC, tf.value DESC NULLS LAST LIMIT 30;"
    ),
    "country_boundaries": (
        "-- Countries whose territory is within 500km of a point (PostGIS):\n"
        "SELECT cb.iso3, cb.name, cb.continent\n"
        "FROM country_boundaries cb\n"
        "WHERE ST_DWithin(cb.geom::geography, ST_Point(37.9, 21.5)::geography, 500000)\n"
        "LIMIT 20;"
    ),
}

# Backward-compat alias used in some eval scripts
_SQL_EXAMPLES = SQL_EXAMPLES


# ---------------------------------------------------------------------------
# QueryRouter — standalone utility class used by eval scripts.
# Production routing has moved to oracle_orchestrator.py (agentic loop).
# This class is kept here for eval/testing purposes only.
# ---------------------------------------------------------------------------

import json
import re
from typing import List, Optional, Tuple

import google.generativeai as genai
import google.api_core.exceptions
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .schemas import QueryIntent
from ..utils.logger import get_logger

logger = get_logger(__name__)


class QueryRouter:
    """Standalone intent classifier and SQL generator for eval scripts."""

    def __init__(self, llm):
        self.llm = llm

    # ── Intent classification ──────────────────────────────────────────────

    def _classify_intent(self, query: str) -> Tuple[QueryIntent, List[str]]:
        """Classify query intent. Returns (intent_str, key_entities)."""
        q_lower = query.lower()
        if any(kw in q_lower for kw in OVERVIEW_KEYWORDS):
            logger.info("QueryRouter: overview keyword detected, overriding to OVERVIEW")
            return QueryIntent.OVERVIEW, []

        examples_block = "\n".join(
            f"  {intent}: {', '.join(exs[:2])}"
            for intent, exs in INTENT_EXAMPLES.items()
        )
        prompt = f"""Classify the following intelligence query into one of these intents:
- factual: looking for specific facts, news, events, declarations
- analytical: counting, trends, distributions, statistics from the database
- narrative: storyline evolution, graph relationships, narrative analysis
- market: trade signals, macro indicators, investment opportunities
- comparative: comparing two entities, time periods, or viewpoints
- ticker: market ticker analysis, company themes, storylines correlated to stock symbols
- overview: broad geopolitical overview, country/region profile, comprehensive landscape analysis

Examples:
{examples_block}

Query: "{query}"

Respond ONLY with valid JSON:
{{"intent": "factual|analytical|narrative|market|comparative|ticker|overview", "confidence": 0.0-1.0, "key_entities": ["entity1"]}}"""

        config = genai.types.GenerationConfig(temperature=0.1, max_output_tokens=1024)
        last_exc: Exception = ValueError("No attempts made")
        for attempt in range(2):
            try:
                result = self._llm_call(prompt, config)
                raw = (result.text or "").strip()
                if not raw:
                    last_exc = ValueError("Empty LLM response")
                    logger.debug(f"QueryRouter: empty response attempt {attempt + 1}, retrying")
                    continue
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    m = re.search(r'\{[^{}]*"intent"[^{}]*\}', raw, re.DOTALL)
                    if m:
                        parsed = json.loads(m.group())
                    else:
                        raise
                intent_str = parsed.get("intent", "factual").lower()
                intent = QueryIntent(intent_str) if intent_str in QueryIntent._value2member_map_ else QueryIntent.FACTUAL
                key_entities = parsed.get("key_entities", [])
                logger.info(f"QueryRouter: intent={intent.value} confidence={parsed.get('confidence', 0):.0%}")
                return intent, key_entities
            except Exception as e:
                last_exc = e
                break
        logger.warning(f"QueryRouter: intent classification failed ({last_exc}), defaulting to FACTUAL")
        return QueryIntent.FACTUAL, []

    # ── SQL generation ─────────────────────────────────────────────────────

    def _generate_sql(self, query: str) -> Optional[str]:
        """Generate a safe read-only SQL query for the given natural language request."""
        sanitized = self._sanitize_user_query(query)

        allowed_tables = (
            "articles, chunks, reports, storylines, entities, entity_mentions, "
            "trade_signals, macro_indicators, market_data, article_storylines, "
            "storyline_edges, v_active_storylines, v_storyline_graph, "
            "conflict_events, macro_forecasts, country_profiles, "
            "trade_flow_indicators, country_boundaries, strategic_infrastructure, "
            "v_sanctions_public"
        )
        schema_hints = (
            "Key columns (PostgreSQL):\n"
            "- articles: id, title, source, category, published_date, url, content\n"
            "- storylines: id, title, summary, momentum_score, narrative_status, community_id\n"
            "- trade_signals: id, ticker, signal (BULLISH/BEARISH/NEUTRAL/WATCHLIST), timeframe, rationale, confidence, signal_date\n"
            "- entities: id, name, entity_type, intelligence_score\n"
            "- v_active_storylines: id, title, momentum_score, narrative_status (view of active storylines)\n"
            "- reports: id, report_date, status, report_type, title\n"
            "- conflict_events: event_date, country, location, actor1, actor2, fatalities, event_type\n"
            "- macro_forecasts: iso3, indicator_code, year, value, unit, vintage (use MAX(vintage) subquery for latest)\n"
            "- country_profiles: iso3, name, region, gdp_usd, gdp_growth, debt_to_gdp, inflation\n"
            "- trade_flow_indicators: reporter_iso3, partner_iso3, indicator_code, year, value, unit\n"
            "- v_sanctions_public: caption, schema_type, datasets, countries (text[]), first_seen, last_seen\n"
            "  IMPORTANT: always use v_sanctions_public, never sanctions_registry (raw table is restricted)"
        )

        prompt = f"""Generate a safe read-only SQL SELECT query for this intelligence database query.
Database: PostgreSQL — use PostgreSQL syntax (e.g. NOW() - INTERVAL '7 days', not DATE_SUB/CURDATE).
Available tables: {allowed_tables}
{schema_hints}
User request: {sanitized}

Rules:
- Use only SELECT statements
- Max 3 JOINs
- Only reference the allowed tables above
- Always add LIMIT 50 unless the query is a pure COUNT(*) with no GROUP BY

Output ONLY the SQL query, nothing else."""

        try:
            result = self._llm_call(
                prompt,
                genai.types.GenerationConfig(temperature=0.1, max_output_tokens=2048),
            )
            sql = result.text.strip().strip("```sql").strip("```").strip()
            if sql.upper().startswith("SELECT"):
                return sql
            return None
        except Exception as e:
            logger.warning(f"QueryRouter: SQL generation failed ({e})")
            return None

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(min=2, max=10),
        retry=retry_if_exception_type((
            google.api_core.exceptions.DeadlineExceeded,
            google.api_core.exceptions.ServiceUnavailable,
        )),
    )
    def _llm_call(self, prompt: str, config):
        return self.llm.generate_content(
            prompt,
            generation_config=config,
            request_options={"timeout": 30},
        )

    def _sanitize_user_query(self, query: str) -> str:
        dangerous = [
            "DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "CREATE",
            "GRANT", "TRUNCATE", "EXEC", "EXECUTE", "COPY", "VACUUM",
        ]
        sanitized = query
        for kw in dangerous:
            sanitized = re.sub(rf"\b{kw}\b", "", sanitized, flags=re.IGNORECASE)
        return " ".join(sanitized.split())
