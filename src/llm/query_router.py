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
        tools, steps = self._select_tools(intent, complexity, query)

        estimated_time = {"simple": 5.0, "medium": 15.0, "complex": 30.0}[complexity.value]
        requires_decomp = complexity == QueryComplexity.COMPLEX and intent == QueryIntent.COMPARATIVE

        sub_queries = None
        if requires_decomp:
            parts = re.split(r"\bvs\b|\bversus\b|\bconfrontra\b|\brispetto a\b", query, flags=re.IGNORECASE)
            sub_queries = [p.strip() for p in parts if p.strip()]

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

    def _classify_intent(self, query: str):
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

Examples:
{examples_block}

Query: "{query}"

Respond ONLY with valid JSON:
{{"intent": "factual|analytical|narrative|market|comparative", "confidence": 0.0-1.0, "key_entities": ["entity1", "entity2"]}}"""

        try:
            result = self._llm_call_with_retry(
                prompt,
                genai.types.GenerationConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                    max_output_tokens=100,
                ),
            )
            parsed = json.loads(result.text)
            intent_str = parsed.get("intent", "factual").lower()
            # Validate enum
            intent = QueryIntent(intent_str) if intent_str in QueryIntent._value2member_map_ else QueryIntent.FACTUAL
            key_entities = parsed.get("key_entities", [])
            logger.info(f"QueryRouter: intent={intent.value} confidence={parsed.get('confidence', 0):.0%}")
            return intent, key_entities
        except Exception as e:
            logger.warning(f"QueryRouter: intent classification failed ({e}), defaulting to FACTUAL")
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

    def _select_tools(self, intent: QueryIntent, complexity: QueryComplexity, query: str):
        tool_names: List[str] = []
        steps: List[ExecutionStep] = []

        if intent == QueryIntent.FACTUAL:
            tool_names = ["rag_search"]
            steps = [ExecutionStep(
                tool_name="rag_search",
                parameters={"query": query, "mode": "both", "top_k": 10},
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
            steps = [
                ExecutionStep(
                    tool_name="rag_search",
                    parameters={"query": query, "mode": "both", "top_k": 8},
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
            tool_names = ["rag_search", "rag_search"]
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
            if "aggregation" not in tool_names:
                tool_names.append("aggregation")

        return tool_names, steps

    # ── SQL generation (for ANALYTICAL/MARKET) with injection sanitization ────

    def _generate_sql(self, query: str) -> Optional[str]:
        """Layer 1: sanitize user query → LLM generates SQL → return for Layer 2 (SQLTool)."""
        sanitized = self._sanitize_user_query(query)

        allowed_tables = (
            "articles, chunks, reports, storylines, entities, entity_mentions, "
            "trade_signals, macro_indicators, market_data, article_storylines, "
            "storyline_edges, v_active_storylines, v_storyline_graph"
        )

        prompt = f"""Generate a safe read-only SQL SELECT query for this intelligence database query.
Available tables: {allowed_tables}
User request: {sanitized}

Rules:
- Use only SELECT statements
- Max 3 JOINs
- No subqueries returning more than 1000 rows
- Only reference the allowed tables above
- Add LIMIT 50 if not already present

Output ONLY the SQL query, nothing else."""

        try:
            result = self._llm_call_with_retry(
                prompt,
                genai.types.GenerationConfig(temperature=0.1, max_output_tokens=300),
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
