"""
ReferenceTool — structured reference data lookup for Oracle 2.0.

Unlike SQLTool (LLM-generated queries) or SpatialTool (composable PostGIS templates),
this tool uses hardcoded parameterized queries with type-safe inputs.

No LLM-generated SQL, no EXPLAIN cost check needed.

Supported lookups:
    - country_profile: by ISO3 code
    - country_by_name: by country name (ILIKE match)
    - country_by_region: by World Bank region
    - sanctions_search: by name/alias (queries v_sanctions_public — PII sanitized)
    - sanctions_by_country: by country ISO2 code (queries v_sanctions_public)
    - macro_forecast: IMF WEO forecasts for a country (latest vintage) by ISO3
    - macro_forecast_indicator: IMF WEO cross-country comparison for one indicator
    - trade_flow: trade flows (export/import/balance) for a country by ISO3
"""

import json
from decimal import Decimal
from typing import Any, Dict, List, Optional

from .base import BaseTool, ToolResult
from ...utils.logger import get_logger

logger = get_logger(__name__)

# IMF WEO indicator codes → human-readable names
IMF_INDICATOR_NAMES = {
    "NGDP_RPCH": "Crescita PIL reale (%)",
    "PCPIPCH": "Inflazione (CPI, %)",
    "LUR": "Disoccupazione (%)",
    "GGXWDG_NGDP": "Debito pubblico (% PIL)",
    "BCA_NGDPD": "Saldo conto corrente (% PIL)",
    "NGDPDPC": "PIL pro capite (USD)",
}


class ReferenceTool(BaseTool):
    """
    Lookup structured reference data (country profiles, sanctions, macro forecasts).

    SECURITY: All queries use psycopg2 parameterized queries (%(key)s).
    No user input is interpolated into SQL strings.
    Sanctions queries use v_sanctions_public (PII-sanitized view), not the base table.
    """

    name = "reference_lookup"
    description = (
        "Look up structured reference data: country profiles (World Bank), "
        "sanctions registries (OpenSanctions), IMF WEO macro forecasts, trade flows"
    )
    parameters = {
        "lookup_type": (
            "string (country_profile|country_by_name|country_by_region|"
            "sanctions_search|sanctions_by_country|"
            "macro_forecast|macro_forecast_indicator|trade_flow)"
        ),
        "query": "string (ISO3 code, country name, indicator code, or search term)",
        "start_year": "int (optional, for macro_forecast_indicator — default: current year - 1)",
        "end_year": "int (optional, for macro_forecast_indicator — default: current year + 5)",
    }

    # Pre-approved parameterized queries — no LLM SQL generation.
    # sanctions_* queries target v_sanctions_public (migration 034), not the raw table.
    SAFE_QUERIES = {
        "country_profile": {
            "sql": """
                SELECT iso3, iso2, name, capital, region, income_group,
                       population, gdp_usd, gdp_per_capita, gdp_growth,
                       inflation, unemployment, debt_to_gdp,
                       current_account_pct, governance_score, data_year
                FROM country_profiles WHERE iso3 = %(query)s
            """,
            "description": "Country profile by ISO3 code",
        },
        "country_by_name": {
            "sql": """
                SELECT iso3, iso2, name, capital, region, income_group,
                       population, gdp_usd, gdp_per_capita, gdp_growth,
                       inflation, unemployment, debt_to_gdp,
                       current_account_pct, governance_score, data_year
                FROM country_profiles
                WHERE name ILIKE %(query)s OR name ILIKE %(query_fuzzy)s
                ORDER BY name LIMIT 10
            """,
            "description": "Country profile by name search",
        },
        "country_by_region": {
            "sql": """
                SELECT iso3, name, population, gdp_usd, gdp_per_capita,
                       gdp_growth, inflation, income_group
                FROM country_profiles
                WHERE region ILIKE %(query)s
                ORDER BY gdp_usd DESC NULLS LAST LIMIT 30
            """,
            "description": "Countries by region",
        },
        # Sanctions queries use v_sanctions_public (PII-sanitized, migration 034)
        "sanctions_search": {
            "sql": """
                SELECT id, caption, schema_type, aliases, datasets, first_seen, last_seen
                FROM v_sanctions_public
                WHERE caption ILIKE %(query_fuzzy)s OR %(query)s = ANY(aliases)
                ORDER BY last_seen DESC NULLS LAST LIMIT 20
            """,
            "description": "Sanctions entity search by name",
        },
        "sanctions_by_country": {
            "sql": """
                SELECT id, caption, schema_type, datasets, first_seen, last_seen
                FROM v_sanctions_public
                WHERE %(query)s = ANY(countries)
                ORDER BY last_seen DESC NULLS LAST LIMIT 50
            """,
            "description": "Sanctions by country ISO2",
        },
        # IMF WEO macro forecasts (latest vintage auto-selected)
        "macro_forecast": {
            "sql": """
                SELECT mf.iso3, cp.name, mf.indicator_code, mf.indicator_name,
                       mf.year, mf.value, mf.unit, mf.vintage
                FROM macro_forecasts mf
                LEFT JOIN country_profiles cp ON mf.iso3 = cp.iso3
                WHERE mf.iso3 = %(query)s
                  AND mf.vintage = (
                      SELECT MAX(vintage) FROM macro_forecasts
                      WHERE iso3 = %(query)s
                  )
                ORDER BY mf.indicator_code, mf.year
                LIMIT 80
            """,
            "description": "IMF WEO forecasts for a country (all indicators, latest vintage)",
        },
        # Cross-country comparison for a single IMF indicator
        "macro_forecast_indicator": {
            "sql": """
                SELECT mf.iso3, cp.name, mf.year, mf.value, mf.unit, mf.vintage
                FROM macro_forecasts mf
                LEFT JOIN country_profiles cp ON mf.iso3 = cp.iso3
                WHERE mf.indicator_code = %(query)s
                  AND mf.year BETWEEN %(start_year)s AND %(end_year)s
                  AND mf.vintage = (
                      SELECT MAX(vintage) FROM macro_forecasts
                      WHERE indicator_code = %(query)s
                  )
                ORDER BY mf.year, mf.value DESC NULLS LAST
                LIMIT 500
            """,
            "description": "IMF WEO cross-country comparison for one indicator",
        },
        # Trade flows for a country (UNCTAD/World Bank data)
        "trade_flow": {
            "sql": """
                SELECT tf.reporter_iso3, cp_r.name AS reporter_name,
                       tf.partner_iso3, cp_p.name AS partner_name,
                       tf.indicator_code, tf.year, tf.value, tf.unit
                FROM trade_flow_indicators tf
                LEFT JOIN country_profiles cp_r ON tf.reporter_iso3 = cp_r.iso3
                LEFT JOIN country_profiles cp_p ON tf.partner_iso3 = cp_p.iso3
                WHERE tf.reporter_iso3 = %(query)s
                ORDER BY tf.year DESC, tf.indicator_code
                LIMIT 50
            """,
            "description": "Trade flows (exports/imports/balance) for a country by ISO3",
        },
    }

    # Lookup types that require ISO3 input (uppercased automatically)
    _ISO3_LOOKUPS = {"country_profile", "macro_forecast", "trade_flow"}
    # Lookup types that require ISO2 input (uppercased automatically)
    _ISO2_LOOKUPS = {"sanctions_by_country"}
    # Lookup types that require indicator code input (uppercased)
    _INDICATOR_LOOKUPS = {"macro_forecast_indicator"}

    def _execute(
        self,
        lookup_type: str = "country_profile",
        query: str = "",
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
        **kwargs,
    ) -> ToolResult:
        """Execute a reference lookup."""
        if lookup_type not in self.SAFE_QUERIES:
            return ToolResult(
                success=False, data=None,
                error=f"Unknown lookup_type: {lookup_type}. Valid: {list(self.SAFE_QUERIES.keys())}"
            )

        if not query or not query.strip():
            return ToolResult(success=False, data=None, error="Empty query")

        # Normalize query casing based on lookup type
        if lookup_type in self._ISO3_LOOKUPS or lookup_type in self._ISO2_LOOKUPS or lookup_type in self._INDICATOR_LOOKUPS:
            query_clean = query.strip().upper()
        else:
            query_clean = query.strip()

        query_config = self.SAFE_QUERIES[lookup_type]

        # Default year range for cross-country indicator queries
        from datetime import date
        current_year = date.today().year
        params = {
            "query": query_clean,
            "query_fuzzy": f"%{query_clean}%",
            "start_year": start_year if start_year is not None else current_year - 1,
            "end_year": end_year if end_year is not None else current_year + 5,
        }

        try:
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SET statement_timeout = '10s'")
                    cur.execute(query_config["sql"], params)
                    columns = [desc[0] for desc in cur.description]
                    rows = cur.fetchall()
                    conn.rollback()  # Read-only — release locks

            results = [dict(zip(columns, row)) for row in rows]

            return ToolResult(
                success=True,
                data={"results": results, "count": len(results)},
                metadata={
                    "lookup_type": lookup_type,
                    "query": query_clean,
                    "description": query_config["description"],
                },
            )
        except Exception as e:
            logger.error(f"ReferenceTool error ({lookup_type}): {e}")
            return ToolResult(success=False, data=None, error=str(e))

    def _format_success(self, data: Any, metadata: Dict) -> str:
        """Format reference lookup results for LLM injection."""
        if not data or not data.get("results"):
            return f"[REFERENCE: No results for '{metadata.get('query', '')}']"

        results = data["results"]
        lookup_type = metadata.get("lookup_type", "")

        if lookup_type in ("country_profile", "country_by_name", "country_by_region"):
            return self._format_country_profiles(results)
        elif lookup_type in ("sanctions_search", "sanctions_by_country"):
            return self._format_sanctions(results)
        elif lookup_type == "macro_forecast":
            return self._format_macro_forecast_country(results, metadata.get("query", ""))
        elif lookup_type == "macro_forecast_indicator":
            return self._format_macro_forecast_indicator(results)
        elif lookup_type == "trade_flow":
            return self._format_trade_flow(results)
        else:
            return json.dumps(results, indent=2, default=str)

    def _format_country_profiles(self, results: List[Dict]) -> str:
        """Format country profiles as readable text."""
        lines = []
        for r in results:
            parts = [f"## {r.get('name', '?')} ({r.get('iso3', '?')})"]
            if r.get('capital'):
                parts.append(f"Capitale: {r['capital']}")
            if r.get('region'):
                parts.append(f"Regione: {r['region']}")
            if r.get('income_group'):
                parts.append(f"Income: {r['income_group']}")
            if r.get('population'):
                parts.append(f"Popolazione: {r['population']:,}")
            if r.get('gdp_usd'):
                parts.append(f"GDP: ${r['gdp_usd']:,.0f}")
            if r.get('gdp_per_capita'):
                parts.append(f"GDP/capita: ${r['gdp_per_capita']:,.0f}")
            if r.get('gdp_growth') is not None:
                parts.append(f"Crescita GDP: {_fmt_decimal(r['gdp_growth'], '.1f')}%")
            if r.get('inflation') is not None:
                parts.append(f"Inflazione: {_fmt_decimal(r['inflation'], '.1f')}%")
            if r.get('unemployment') is not None:
                parts.append(f"Disoccupazione: {_fmt_decimal(r['unemployment'], '.1f')}%")
            if r.get('debt_to_gdp') is not None:
                parts.append(f"Debito/GDP: {_fmt_decimal(r['debt_to_gdp'], '.1f')}%")
            if r.get('governance_score') is not None:
                parts.append(f"Governance: {_fmt_decimal(r['governance_score'], '.2f')}")
            lines.append(" | ".join(parts))
        return "\n".join(lines)

    def _format_sanctions(self, results: List[Dict]) -> str:
        """Format sanctions results (from v_sanctions_public)."""
        lines = [f"Trovate {len(results)} entità sanzionate:"]
        for r in results:
            datasets = ", ".join(r.get('datasets', [])[:3]) if r.get('datasets') else "N/A"
            line = f"- **{r.get('caption', '?')}** ({r.get('schema_type', '?')}) — Datasets: {datasets}"
            if r.get('first_seen'):
                line += f" — Dal {r['first_seen']}"
            lines.append(line)
        return "\n".join(lines)

    def _format_macro_forecast_country(self, results: List[Dict], iso3: str) -> str:
        """Format IMF WEO forecasts for a country grouped by indicator."""
        if not results:
            return f"[MACRO FORECASTS: Nessuna previsione IMF per {iso3}]"

        country_name = results[0].get("name") or iso3
        vintage = results[0].get("vintage", "N/A")
        lines = [f"## Previsioni IMF WEO — {country_name} ({iso3}) | Vintage: {vintage}"]

        # Group by indicator_code
        by_indicator: Dict[str, List[Dict]] = {}
        for r in results:
            code = r.get("indicator_code", "?")
            by_indicator.setdefault(code, []).append(r)

        for code, rows in sorted(by_indicator.items()):
            label = IMF_INDICATOR_NAMES.get(code, code)
            unit = rows[0].get("unit", "")
            values = " | ".join(
                f"{r['year']}: {_fmt_decimal(r['value'], '.2f')}"
                for r in sorted(rows, key=lambda x: x.get("year", 0))
                if r.get("value") is not None
            )
            lines.append(f"**{label}** ({unit}): {values}")

        return "\n".join(lines)

    def _format_macro_forecast_indicator(self, results: List[Dict]) -> str:
        """Format cross-country IMF WEO comparison for one indicator."""
        if not results:
            return "[MACRO FORECASTS: Nessun dato trovato]"

        indicator_code = results[0].get("indicator_code") if results else "?"
        label = IMF_INDICATOR_NAMES.get(indicator_code, indicator_code)
        unit = results[0].get("unit", "") if results else ""
        vintage = results[0].get("vintage", "N/A") if results else "N/A"
        lines = [f"## IMF WEO — {label} ({unit}) | Vintage: {vintage}"]

        # Group by year for cross-country table
        by_year: Dict[int, List[Dict]] = {}
        for r in results:
            yr = r.get("year", 0)
            by_year.setdefault(yr, []).append(r)

        for yr in sorted(by_year.keys()):
            year_rows = sorted(by_year[yr], key=lambda x: -(float(x.get("value") or 0)))[:15]
            top = ", ".join(
                f"{r.get('name') or r.get('iso3', '?')}: {_fmt_decimal(r['value'], '.2f')}"
                for r in year_rows if r.get("value") is not None
            )
            lines.append(f"**{yr}** (top paesi): {top}")

        return "\n".join(lines)

    def _format_trade_flow(self, results: List[Dict]) -> str:
        """Format trade flow indicators."""
        if not results:
            return "[TRADE FLOWS: Nessun dato trovato]"

        reporter = results[0].get("reporter_name") or results[0].get("reporter_iso3", "?")
        lines = [f"## Flussi commerciali — {reporter}"]

        # Group by year + indicator
        by_key: Dict[str, List[Dict]] = {}
        for r in results:
            key = f"{r.get('year', '?')}_{r.get('indicator_code', '?')}"
            by_key.setdefault(key, []).append(r)

        for key in sorted(by_key.keys(), reverse=True)[:20]:
            rows = by_key[key]
            r0 = rows[0]
            year = r0.get("year", "?")
            indicator = r0.get("indicator_code", "?")
            unit = r0.get("unit", "USD")
            # Aggregate total (partner_iso3 IS NULL rows) or show top partners
            totals = [r for r in rows if not r.get("partner_iso3")]
            if totals:
                val = _fmt_decimal(totals[0].get("value"), ".2f")
                lines.append(f"**{year} {indicator}**: {val} {unit} (totale)")
            else:
                partners = ", ".join(
                    f"{r.get('partner_name') or r.get('partner_iso3', '?')}: {_fmt_decimal(r.get('value'), '.2f')}"
                    for r in rows[:5]
                )
                lines.append(f"**{year} {indicator}**: {partners}")

        return "\n".join(lines)


def _fmt_decimal(value: Any, fmt: str) -> str:
    """Format a value that may be Decimal, float, int, or None."""
    if value is None:
        return "N/A"
    try:
        return format(float(value), fmt)
    except (TypeError, ValueError):
        return str(value)
