"""QueryRouter — intent classification, complexity heuristic, and QueryPlan generation."""

import hashlib
import json
import re
from typing import Any, Dict, List, Optional

import google.generativeai as genai
import google.api_core.exceptions
from cachetools import TTLCache
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .schemas import ExecutionStep, QueryComplexity, QueryIntent, QueryPlan
from ..utils.logger import get_logger

logger = get_logger(__name__)

# Few-shot examples per intent category
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

# Keywords that signal complexity
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


class QueryRouter:
    def __init__(self, llm):
        self.llm = llm
        self._intent_cache: TTLCache = TTLCache(maxsize=200, ttl=600)

    def route(self, query: str, context: Optional[str] = None) -> QueryPlan:
        """Classify query intent and build a QueryPlan."""
        # Check intent cache
        cache_key = hashlib.md5(query.encode()).hexdigest()
        if cache_key in self._intent_cache:
            cached = self._intent_cache[cache_key]
            logger.debug(f"QueryRouter: cache hit for intent={cached['intent']}")
            intent = QueryIntent(cached["intent"])
            key_entities = cached.get("key_entities", [])
        else:
            intent, key_entities = self._classify_intent(query)
            self._intent_cache[cache_key] = {"intent": intent.value, "key_entities": key_entities}

        complexity = self._assess_complexity(query, intent)
        tools, steps = self._select_tools(intent, complexity, query, key_entities=key_entities)

        estimated_time = {"simple": 5.0, "medium": 15.0, "complex": 30.0}[complexity.value]
        requires_decomp = complexity == QueryComplexity.COMPLEX and intent == QueryIntent.COMPARATIVE

        sub_queries = None
        if requires_decomp:
            parts = re.split(r"\bvs\b|\bversus\b|\bconfrontra\b|\brispetto a\b", query, flags=re.IGNORECASE)
            sub_queries = [p.strip() for p in parts if p.strip()]

        # Query expansion for complex analytical/narrative queries
        if complexity == QueryComplexity.COMPLEX and intent in (QueryIntent.ANALYTICAL, QueryIntent.NARRATIVE, QueryIntent.COMPARATIVE, QueryIntent.OVERVIEW):
            expanded = self._expand_query(query)
            if expanded:
                # Inject expanded queries into RAG steps
                for step in steps:
                    if step.tool_name == "rag_search":
                        step.parameters["multi_query"] = expanded

        return QueryPlan(
            intent=intent,
            complexity=complexity,
            tools=tools,
            execution_steps=steps,
            estimated_time=estimated_time,
            requires_decomposition=requires_decomp,
            sub_queries=sub_queries,
        )

    # ── Intent classification (LLM) ───────────────────────────────────────────

    def _extract_entities_spacy(self, query: str) -> List[str]:
        """Extract GPE/ORG/LOC entities from query using spaCy NER."""
        try:
            from ..utils.stopwords import _cleaner
            if _cleaner.nlp:
                doc = _cleaner.nlp(query)
                return [ent.text for ent in doc.ents if ent.label_ in {"GPE", "ORG", "LOC"}]
        except Exception:
            pass
        return []

    def _classify_intent(self, query: str):
        # Pre-LLM override: overview keywords are unambiguous
        q_lower = query.lower()
        if any(kw in q_lower for kw in OVERVIEW_KEYWORDS):
            entities = self._extract_entities_spacy(query)
            logger.info(f"QueryRouter: overview keyword detected, overriding to OVERVIEW, entities={entities}")
            return QueryIntent.OVERVIEW, entities

        examples_block = "\n".join(
            f"  {intent}: {', '.join(exs[:2])}"
            for intent, exs in INTENT_EXAMPLES.items()
        )
        prompt = f"""Classify the following intelligence query into one of these intents:
- factual: looking for specific facts, news, events, declarations
- analytical: counting, trends, distributions, statistics
- narrative: storyline evolution, graph relationships, narrative analysis
- market: trade signals, macro indicators, investment opportunities
- comparative: comparing two entities, time periods, or viewpoints
- ticker: market ticker analysis, company themes, storylines correlated to stock symbols
- overview: broad geopolitical overview, country/region profile, comprehensive landscape analysis, excursus — needs historical depth, not just recent news

Examples:
{examples_block}

Query: "{query}"

Respond ONLY with valid JSON:
{{"intent": "factual|analytical|narrative|market|comparative|ticker|overview", "confidence": 0.0-1.0, "key_entities": ["entity1", "entity2"]}}"""

        config = genai.types.GenerationConfig(temperature=0.1, max_output_tokens=1024)
        last_exc: Exception = ValueError("No attempts made")
        for attempt in range(2):
            try:
                result = self._llm_call_with_retry(prompt, config)
                raw = (result.text or "").strip()
                if not raw:
                    last_exc = ValueError("Empty LLM response")
                    logger.debug(f"QueryRouter: empty response on attempt {attempt + 1}, retrying")
                    continue
                # Try direct parse; fall back to regex JSON extraction
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

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(min=2, max=10),
        retry=retry_if_exception_type((
            google.api_core.exceptions.DeadlineExceeded,
            google.api_core.exceptions.ServiceUnavailable,
        )),
    )
    def _llm_call_with_retry(self, prompt: str, config):
        return self.llm.generate_content(
            prompt,
            generation_config=config,
            request_options={"timeout": 30},
        )

    # ── Complexity heuristic (rule-based) ─────────────────────────────────────

    def _assess_complexity(self, query: str, intent: QueryIntent) -> QueryComplexity:
        words = query.lower().split()
        word_count = len(words)
        q_lower = query.lower()

        # OVERVIEW is always COMPLEX — needs query expansion for breadth
        if intent == QueryIntent.OVERVIEW:
            return QueryComplexity.COMPLEX

        # COMPLEX signals
        if any(kw in q_lower for kw in COMPLEX_KEYWORDS):
            return QueryComplexity.COMPLEX
        if word_count > 20 and intent in (QueryIntent.ANALYTICAL, QueryIntent.COMPARATIVE):
            return QueryComplexity.COMPLEX

        # SIMPLE signals
        if word_count < 10 and intent == QueryIntent.FACTUAL:
            return QueryComplexity.SIMPLE

        # MEDIUM: has analytical keywords, time range, or multiple entities
        if any(kw in q_lower for kw in ANALYTICAL_KEYWORDS):
            return QueryComplexity.MEDIUM
        if intent in (QueryIntent.ANALYTICAL, QueryIntent.MARKET):
            return QueryComplexity.MEDIUM

        return QueryComplexity.SIMPLE

    # ── Tool selection ────────────────────────────────────────────────────────

    # Recency keywords — triggers recency_boost flag on RAG steps
    _RECENCY_KEYWORDS = re.compile(
        r"\b(ultimo|ultima|ultimi|ultime|latest|recent[ei]?|più recente|"
        r"report di oggi|report di ieri|today|yesterday|stamattina|"
        r"questa settimana|this week|last report)\b",
        re.IGNORECASE,
    )

    def _has_recency_intent(self, query: str) -> bool:
        return bool(self._RECENCY_KEYWORDS.search(query))

    def _select_tools(self, intent: QueryIntent, complexity: QueryComplexity, query: str, key_entities: Optional[List[str]] = None):
        tool_names: List[str] = []
        steps: List[ExecutionStep] = []
        recency = self._has_recency_intent(query)

        if intent == QueryIntent.FACTUAL:
            tool_names = ["rag_search"]
            rag_filters = {}
            if recency:
                rag_filters["recency_boost"] = True
            steps = [ExecutionStep(
                tool_name="rag_search",
                parameters={"query": query, "mode": "both", "top_k": 10, "filters": rag_filters} if rag_filters else {"query": query, "mode": "both", "top_k": 10},
                description="Hybrid RAG search for factual information",
            )]

        elif intent == QueryIntent.ANALYTICAL:
            if complexity == QueryComplexity.SIMPLE:
                tool_names = ["aggregation"]
                steps = [ExecutionStep(
                    tool_name="aggregation",
                    parameters={"aggregation_type": "statistics", "target": "articles"},
                    description="Statistical aggregation",
                )]
            else:
                sql_query = self._generate_sql(query)
                tool_names = ["aggregation", "sql_query"] if sql_query else ["aggregation"]
                steps = [
                    ExecutionStep(
                        tool_name="aggregation",
                        parameters={"aggregation_type": "trend_over_time", "target": "articles"},
                        description="Trend aggregation",
                    ),
                ]
                if sql_query:
                    steps.append(ExecutionStep(
                        tool_name="sql_query",
                        parameters={"query": sql_query},
                        description="Custom SQL analysis",
                    ))

        elif intent == QueryIntent.NARRATIVE:
            tool_names = ["rag_search", "graph_navigation"]
            rag_params = {"query": query, "mode": "both", "top_k": 8}
            if recency:
                rag_params["filters"] = {"recency_boost": True}
            steps = [
                ExecutionStep(
                    tool_name="rag_search",
                    parameters=rag_params,
                    description="RAG search for narrative context",
                ),
                ExecutionStep(
                    tool_name="graph_navigation",
                    parameters={"operation": "connected_storylines", "max_depth": 3},
                    description="Graph traversal for storyline connections",
                    is_critical=False,
                ),
            ]

        elif intent == QueryIntent.MARKET:
            sql_query = self._generate_sql(query)
            tool_names = ["market_analysis", "rag_search"]
            steps = [
                ExecutionStep(
                    tool_name="market_analysis",
                    parameters={"analysis_type": "signals_filter", "timeframe": "SHORT_TERM"},
                    description="Market signals analysis",
                ),
                ExecutionStep(
                    tool_name="rag_search",
                    parameters={"query": query, "mode": "strategic", "top_k": 5},
                    description="Strategic report search",
                    is_critical=False,
                ),
            ]

        elif intent == QueryIntent.COMPARATIVE:
            tool_names = ["rag_search", "aggregation"]
            steps = [
                ExecutionStep(
                    tool_name="rag_search",
                    parameters={"query": query, "mode": "both", "top_k": 10},
                    description="Full comparative search",
                ),
                ExecutionStep(
                    tool_name="aggregation",
                    parameters={"aggregation_type": "top_n", "target": "entities"},
                    description="Entity comparison",
                    is_critical=False,
                ),
            ]
            # If query mentions "report" and contains numbers, add report_compare tool
            if "report" in query.lower() and any(char.isdigit() for char in query):
                tool_names.insert(0, "report_compare")
                # Report comparison will extract IDs from query dynamically if needed

        elif intent == QueryIntent.OVERVIEW:
            tool_names = ["rag_search", "graph_navigation"]
            # Use vector-only search to avoid FTS AND-matching problem
            # (e.g. "myanmar geopolitical landscape" requires ALL 3 terms in same chunk,
            #  finding only 2 chunks out of 227 that mention Myanmar)
            # GPE filter NOT used: many articles lack GPE entities in JSONB
            steps = [
                ExecutionStep(
                    tool_name="rag_search",
                    parameters={"query": query, "mode": "both", "top_k": 15, "filters": {"search_type": "vector"}},
                    description="Deep vector search for comprehensive overview (no recency bias)",
                ),
                ExecutionStep(
                    tool_name="graph_navigation",
                    parameters={"operation": "connected_storylines", "max_depth": 3},
                    description="Graph traversal for related storylines",
                    is_critical=False,
                ),
            ]

        elif intent == QueryIntent.TICKER:
            tool_names = ["ticker_themes", "rag_search"]
            steps = [
                ExecutionStep(
                    tool_name="ticker_themes",
                    parameters={"query": query, "top_n": 5, "days": 30},
                    description="Find storylines correlated to ticker",
                ),
                ExecutionStep(
                    tool_name="rag_search",
                    parameters={"query": query, "mode": "strategic", "top_k": 5},
                    description="Strategic context search",
                    is_critical=False,
                ),
            ]

        elif intent == QueryIntent.REFERENCE:
            # Detect lookup type from query context
            query_lower = query.lower()
            sanctions_keywords = ("sanzion", "sanction", "blacklist", "embargo", "sanctioned")
            is_sanctions = any(k in query_lower for k in sanctions_keywords)

            if is_sanctions:
                lookup_type = "sanctions_search"
                # Extract entity name from query (heuristic: longest capitalized word sequence)
                ref_query = query  # Let ReferenceTool handle fuzzy matching
            else:
                lookup_type = "country_by_name"
                ref_query = query

            # Extract ISO3 if present (3 uppercase letters)
            import re
            iso3_match = re.search(r'\b([A-Z]{3})\b', query)
            if iso3_match and not is_sanctions:
                lookup_type = "country_profile"
                ref_query = iso3_match.group(1)

            tool_names = ["reference_lookup", "rag_search"]
            steps = [
                ExecutionStep(
                    tool_name="reference_lookup",
                    parameters={"lookup_type": lookup_type, "query": ref_query},
                    description=f"Reference data lookup ({lookup_type})",
                ),
                ExecutionStep(
                    tool_name="rag_search",
                    parameters={"query": query, "mode": "both", "top_k": 5},
                    description="Contextual RAG enrichment for reference data",
                    is_critical=False,
                ),
            ]

        elif intent == QueryIntent.SPATIAL:
            # Build SpatialQuerySpec from query analysis
            import re
            query_lower = query.lower()

            spec = {"include_infrastructure": True, "include_conflicts": True}

            # Extract ISO3 from query
            iso3_match = re.search(r'\b([A-Z]{3})\b', query)
            if iso3_match:
                spec["center_iso3"] = iso3_match.group(1)

            # Extract radius if mentioned
            radius_match = re.search(r'(\d+)\s*km', query_lower)
            if radius_match:
                spec["radius_km"] = min(int(radius_match.group(1)), 2000)

            # Detect infrastructure-only or conflict-only
            conflict_keywords = ("conflitt", "conflict", "guerra", "war", "violenz", "battle", "scontr")
            infra_keywords = ("cav", "cable", "aeroporto", "airport", "porto", "port", "pipeline",
                            "raffineria", "refinery", "central", "power", "base militar", "military")
            has_conflict = any(k in query_lower for k in conflict_keywords)
            has_infra = any(k in query_lower for k in infra_keywords)
            if has_conflict and not has_infra:
                spec["include_infrastructure"] = False
            if has_infra and not has_conflict:
                spec["include_conflicts"] = False

            tool_names = ["spatial_query", "rag_search"]
            steps = [
                ExecutionStep(
                    tool_name="spatial_query",
                    parameters={"spec": spec},
                    description="Composable spatial analysis",
                ),
                ExecutionStep(
                    tool_name="rag_search",
                    parameters={"query": query, "mode": "both", "top_k": 5},
                    description="RAG narrative enrichment for spatial context",
                    is_critical=False,
                ),
            ]

        return tool_names, steps

    # ── SQL generation (for ANALYTICAL/MARKET) with injection sanitization ────

    # Few-Shot SQL examples per table (Spider/BIRD benchmark evidence: +30% accuracy vs zero-shot)
    # Each entry shows the canonical query pattern. Use as reference when generating similar queries.
    _SQL_EXAMPLES: Dict[str, str] = {
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

    def _generate_sql(self, query: str) -> Optional[str]:
        """Layer 1: sanitize user query → LLM generates SQL → return for Layer 2 (SQLTool)."""
        from datetime import date as _date
        sanitized = self._sanitize_user_query(query)
        today = _date.today().isoformat()

        allowed_tables = (
            "articles, chunks, reports, storylines, entities, entity_mentions, "
            "trade_signals, macro_indicators, market_data, article_storylines, "
            "storyline_edges, v_active_storylines, v_storyline_graph, "
            "country_profiles, v_sanctions_public, conflict_events, country_boundaries, "
            "strategic_infrastructure, macro_forecasts, trade_flow_indicators"
        )

        schema_hints = (
            "Key columns (PostgreSQL):\n"
            "- articles: id, title, source, category, published_date, url, content\n"
            "- storylines: id, title, summary, momentum_score, narrative_status, community_id\n"
            "- trade_signals: id, ticker, signal (BULLISH/BEARISH/NEUTRAL/WATCHLIST), timeframe, rationale, confidence, signal_date\n"
            "- entities: id, name, entity_type, intelligence_score\n"
            "- v_active_storylines: id, title, momentum_score, narrative_status (view of active storylines)\n"
            "- reports: id, report_date, status, report_type, title\n"
            "- conflict_events: event_date DATE, event_type ('1'=state-based,'2'=non-state,'3'=one-sided), "
            "country TEXT, location TEXT, actor1 TEXT, actor2 TEXT, fatalities INT, geom GEOMETRY(Point,4326)\n"
            "- macro_forecasts: iso3 CHAR(3), indicator_code TEXT (e.g. NGDP_RPCH=GDP growth, PCPIPCH=inflation, "
            "LUR=unemployment, GGXWDG_NGDP=debt/GDP), year INT, value NUMERIC, unit TEXT, vintage TEXT\n"
            "- v_sanctions_public: id TEXT, caption TEXT, schema_type TEXT, aliases TEXT[], "
            "countries CHAR(2)[] (ISO2), datasets TEXT[], first_seen DATE, last_seen DATE\n"
            "- country_profiles: iso3 CHAR(3), iso2 CHAR(2), name TEXT, region TEXT, "
            "population BIGINT, gdp_usd NUMERIC, gdp_growth NUMERIC, inflation NUMERIC, "
            "debt_to_gdp NUMERIC, governance_score NUMERIC, data_year INT\n"
            "- trade_flow_indicators: reporter_iso3, partner_iso3 (NULL=total), indicator_code "
            "(EXPORT_VALUE/IMPORT_VALUE/TRADE_BALANCE), year INT, value NUMERIC, unit TEXT\n"
            "- country_boundaries: iso3, name, geom GEOMETRY(MultiPolygon,4326), continent, subregion"
        )

        # Build few-shot examples block — only include tables relevant to the query
        examples_block = ""
        query_lower = sanitized.lower()
        relevant_examples = []
        table_keywords = {
            "conflict_events": ["conflitt", "conflict", "guerra", "war", "attack", "attacco", "fatalities", "morti"],
            "macro_forecasts": ["imf", "previsioni", "forecast", "pil", "gdp", "inflazione", "inflation", "disoccup"],
            "v_sanctions_public": ["sanzioni", "sanction", "sanzionat", "blacklist"],
            "country_profiles": ["paese", "country", "pil", "gdp", "regione", "region", "popolazione", "population"],
            "trade_flow_indicators": ["export", "import", "commercio", "trade", "bilancia"],
            "country_boundaries": ["confine", "boundary", "vicino", "near", "within", "distanza"],
        }
        for table, keywords in table_keywords.items():
            if any(kw in query_lower for kw in keywords):
                relevant_examples.append(self._SQL_EXAMPLES[table])
        if relevant_examples:
            examples_block = "\n## Examples of correct SQL patterns:\n" + "\n\n".join(relevant_examples) + "\n"

        prompt = f"""Generate a safe read-only SQL SELECT query for this intelligence database query.
Database: PostgreSQL — use PostgreSQL syntax (e.g. NOW() - INTERVAL '7 days', not DATE_SUB/CURDATE).
TODAY = {today}  -- Use CURRENT_DATE or this date for temporal filters. Default to last 365 days for event tables.
Available tables: {allowed_tables}
{schema_hints}
{examples_block}
User request: {sanitized}

Rules:
- Use only SELECT statements
- Max 3 JOINs
- No subqueries returning more than 1000 rows
- Only reference the allowed tables above
- Add LIMIT 50 if not already present for non-aggregate (non-COUNT/SUM) queries
- For sanctions queries, always use v_sanctions_public (not sanctions_registry)
- For conflict queries, add event_date >= CURRENT_DATE - INTERVAL '365 days' unless the user asks for historical data
- For macro_forecasts, always filter to the latest vintage with a subquery

Output ONLY the SQL query, nothing else."""

        try:
            result = self._llm_call_with_retry(
                prompt,
                genai.types.GenerationConfig(temperature=0.1, max_output_tokens=2048),
            )
            sql = result.text.strip().strip("```sql").strip("```").strip()
            # Basic sanity check before returning — SQLTool does full validation
            if sql.upper().startswith("SELECT"):
                return sql
            logger.warning(f"QueryRouter: generated SQL doesn't start with SELECT, skipping")
            return None
        except Exception as e:
            logger.warning(f"QueryRouter: SQL generation failed ({e}), skipping sql_query tool")
            return None

    def _expand_query(self, query: str) -> Optional[List[str]]:
        """Use LLM to decompose a complex query into 2-3 focused sub-queries for better RAG retrieval."""
        prompt = f"""You are a geopolitical intelligence analyst. A user has asked a complex question.
Decompose it into 2-3 simpler, focused search queries that together cover all aspects of the original question.
Each sub-query should be a concise search phrase (5-15 words) optimized for semantic retrieval.

Original question: "{query}"

Respond ONLY with valid JSON: {{"queries": ["sub-query 1", "sub-query 2", "sub-query 3"]}}"""

        try:
            result = self._llm_call_with_retry(
                prompt,
                genai.types.GenerationConfig(
                    temperature=0.2,
                    max_output_tokens=1024,
                ),
            )
            raw = (result.text or "").strip()
            parsed = json.loads(raw)
            queries = parsed.get("queries", [])
            if queries and len(queries) <= 5:
                logger.info(f"Query expansion: {len(queries)} sub-queries generated")
                return queries
            return None
        except Exception as e:
            logger.warning(f"Query expansion failed ({e}), using original query")
            return None

    def _sanitize_user_query(self, query: str) -> str:
        """Remove dangerous SQL keywords from user query BEFORE passing to LLM (Layer 1)."""
        dangerous = [
            "DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "CREATE",
            "GRANT", "TRUNCATE", "EXEC", "EXECUTE", "COPY", "VACUUM",
        ]
        sanitized = query
        for kw in dangerous:
            sanitized = re.sub(rf"\b{kw}\b", "", sanitized, flags=re.IGNORECASE)
        return " ".join(sanitized.split())  # collapse extra whitespace
