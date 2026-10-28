"""
SpatialTool — composable PostGIS query builder for Oracle 2.0.

ARCHITECTURE:
    1. LLM populates SpatialQuerySpec (Pydantic model → JSON) — NO SQL generation
    2. Python validates via Pydantic (whitelists + bounds)
    3. Python assembles pre-approved SQL blocks using psycopg2 %()s parameters
    4. Executes with extended statement_timeout (30s)

SECURITY:
    - Zero SQL injection surface: all values are parameterized via psycopg2
    - Table/column names are hardcoded Python strings (not user input)
    - Enum values (infra_types, event_types) are checked against whitelists
    - Radius clamped to [1, 2000] km, LIMIT clamped to [1, 200]

Bypass SQLTool's EXPLAIN cost check: PostGIS queries routinely exceed MAX_COST=10000
on cold cache, but are fast when GIST indexes are warm.
"""

import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, field_validator

from .base import BaseTool, ToolResult
from ...utils.logger import get_logger

logger = get_logger(__name__)

# ── Whitelists ──────────────────────────────────────────────────────────────────

ALLOWED_INFRA_TYPES = {
    'SUBMARINE_CABLE', 'CABLE_LANDING_POINT', 'PORT', 'AIRPORT',
    'PIPELINE', 'REFINERY', 'LNG_TERMINAL', 'MINE',
    'DATA_CENTER', 'POWER_PLANT', 'MILITARY_BASE',
}
ALLOWED_EVENT_TYPES = {'1', '2', '3'}  # UCDP: state-based, non-state, one-sided


# ── Pydantic spec: what the LLM outputs ────────────────────────────────────────

class SpatialQuerySpec(BaseModel):
    """Structured spec populated by LLM (JSON dict). No SQL, only validated parameters."""

    # Center: resolve from ISO3 (centroid) or explicit coordinates
    center_iso3: Optional[str] = None
    center_lon: Optional[float] = None
    center_lat: Optional[float] = None
    radius_km: int = 200

    # Optional composable filters
    infra_types: Optional[List[str]] = None
    event_types: Optional[List[str]] = None
    country_codes: Optional[List[str]] = None
    date_from: Optional[str] = None   # ISO format YYYY-MM-DD
    date_to: Optional[str] = None

    # Output control
    include_infrastructure: bool = True
    include_conflicts: bool = True
    include_boundaries: bool = False
    limit: int = 50

    @field_validator('radius_km')
    @classmethod
    def clamp_radius(cls, v):
        return max(1, min(v, 2000))

    @field_validator('limit')
    @classmethod
    def clamp_limit(cls, v):
        return max(1, min(v, 200))

    @field_validator('infra_types')
    @classmethod
    def validate_infra(cls, v):
        if v:
            invalid = set(v) - ALLOWED_INFRA_TYPES
            if invalid:
                raise ValueError(f"Invalid infra_types: {invalid}")
        return v

    @field_validator('event_types')
    @classmethod
    def validate_events(cls, v):
        if v:
            invalid = set(v) - ALLOWED_EVENT_TYPES
            if invalid:
                raise ValueError(f"Invalid event_types: {invalid}")
        return v


# ── SpatialTool ─────────────────────────────────────────────────────────────────

class SpatialTool(BaseTool):
    """
    Composable PostGIS query builder — assembles pre-approved SQL blocks from
    a Pydantic-validated JSON spec.
    """

    name = "spatial_query"
    description = (
        "PostGIS spatial analysis: find strategic infrastructure and conflict events near a geographic center. "
        "Use for PATH SPATIAL: queries about what is located near a country/region, "
        "critical infrastructure at risk, conflict zones near pipelines, etc. "
        "Center can be specified as ISO3 country code (e.g. 'IRN') or explicit coordinates. "
        "Returns infrastructure layers (ports, airports, pipelines, cables, refineries) and conflict events "
        "with distance in km from center."
    )
    parameters = {
        "type": "object",
        "properties": {
            "rationale": {
                "type": "string",
                "description": (
                    "PATH SPATIAL: Explain the geographic center you're analyzing, "
                    "which layers (infrastructure/conflicts) are relevant to the query, "
                    "and what radius makes sense for this geographic context."
                ),
            },
            "spec": {
                "type": "object",
                "description": "Spatial query specification",
                "properties": {
                    "center_iso3": {
                        "type": "string",
                        "description": "ISO3 country code as geographic center, e.g. 'IRN', 'UKR', 'TWN'",
                    },
                    "center_lon": {
                        "type": "number",
                        "description": "Longitude if center_iso3 not available",
                    },
                    "center_lat": {
                        "type": "number",
                        "description": "Latitude if center_iso3 not available",
                    },
                    "radius_km": {
                        "type": "integer",
                        "description": "Search radius in km (1-2000, default 200)",
                    },
                    "include_infrastructure": {
                        "type": "boolean",
                        "description": "Include strategic infrastructure (default true)",
                    },
                    "include_conflicts": {
                        "type": "boolean",
                        "description": "Include conflict events from UCDP data (default true)",
                    },
                    "include_boundaries": {
                        "type": "boolean",
                        "description": "Include neighboring country boundaries (default false)",
                    },
                    "date_from": {
                        "type": "string",
                        "description": "ISO YYYY-MM-DD filter for conflict events start date",
                    },
                    "date_to": {
                        "type": "string",
                        "description": "ISO YYYY-MM-DD filter for conflict events end date",
                    },
                    "infra_types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Filter infrastructure types: SUBMARINE_CABLE, CABLE_LANDING_POINT, "
                            "PORT, AIRPORT, PIPELINE, REFINERY, LNG_TERMINAL, MINE, "
                            "DATA_CENTER, POWER_PLANT, MILITARY_BASE"
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results per layer (1-200, default 50)",
                    },
                },
            },
        },
        "required": ["rationale", "spec"],
    }

    STATEMENT_TIMEOUT_MS = 30000  # 30s for spatial queries

    def _resolve_center(self, spec: SpatialQuerySpec) -> str:
        """Build center geometry clause from ISO3 or explicit coords."""
        if spec.center_iso3:
            return "(SELECT ST_Centroid(geom) FROM country_boundaries WHERE iso3 = %(center_iso3)s)"
        elif spec.center_lon is not None and spec.center_lat is not None:
            return "ST_SetSRID(ST_Point(%(center_lon)s, %(center_lat)s), 4326)"
        else:
            raise ValueError("Must provide center_iso3 or center_lon+center_lat")

    def _build_infra_block(self, spec: SpatialQuerySpec, center: str) -> Optional[str]:
        """Compose infrastructure sub-query with optional type/country filter."""
        if not spec.include_infrastructure:
            return None
        where_parts = [f"ST_DWithin(si.geom::geography, ({center})::geography, %(radius_m)s)"]
        if spec.infra_types:
            where_parts.append("si.infra_type = ANY(%(infra_types)s)")
        if spec.country_codes:
            where_parts.append("si.country_code = ANY(%(country_codes)s)")
        where = " AND ".join(where_parts)
        return f"""
            SELECT 'infrastructure' AS layer, si.name, si.infra_type AS type,
                   si.status, si.operator,
                   ST_AsGeoJSON(si.geom)::json AS geojson,
                   ROUND((ST_Distance(si.geom::geography, ({center})::geography) / 1000)::numeric, 1) AS distance_km
            FROM strategic_infrastructure si
            WHERE {where}
            ORDER BY distance_km LIMIT %(limit)s
        """

    def _build_conflict_block(self, spec: SpatialQuerySpec, center: str) -> Optional[str]:
        """Compose conflict events sub-query with optional date/type/country filter."""
        if not spec.include_conflicts:
            return None
        where_parts = [f"ST_DWithin(ce.geom::geography, ({center})::geography, %(radius_m)s)"]
        if spec.event_types:
            where_parts.append("ce.event_type = ANY(%(event_types)s)")
        if spec.date_from:
            where_parts.append("ce.event_date >= %(date_from)s")
        if spec.date_to:
            where_parts.append("ce.event_date <= %(date_to)s")
        if spec.country_codes:
            where_parts.append("ce.country = ANY(%(country_codes)s)")
        where = " AND ".join(where_parts)
        return f"""
            SELECT 'conflict' AS layer, ce.location AS name, ce.event_type AS type,
                   ce.actor1 AS status, ce.actor2 AS operator,
                   ST_AsGeoJSON(ce.geom)::json AS geojson,
                   ROUND((ST_Distance(ce.geom::geography, ({center})::geography) / 1000)::numeric, 1) AS distance_km,
                   ce.fatalities, ce.event_date::text AS event_date
            FROM conflict_events ce
            WHERE {where}
            ORDER BY ce.event_date DESC LIMIT %(limit)s
        """

    def _build_boundary_block(self, spec: SpatialQuerySpec, center: str) -> Optional[str]:
        """Compose country boundary lookup (neighboring countries within radius)."""
        if not spec.include_boundaries:
            return None
        return f"""
            SELECT 'boundary' AS layer, cb.name, 'COUNTRY' AS type,
                   cb.continent AS status, cb.iso3 AS operator,
                   NULL::json AS geojson,
                   ROUND((ST_Distance(cb.geom::geography, ({center})::geography) / 1000)::numeric, 1) AS distance_km
            FROM country_boundaries cb
            WHERE ST_DWithin(cb.geom::geography, ({center})::geography, %(radius_m)s)
            ORDER BY distance_km LIMIT %(limit)s
        """

    def _execute(self, spec: dict = None, **kwargs) -> ToolResult:
        """Validate spec, assemble SQL, execute."""
        if not spec:
            return ToolResult(success=False, data=None, error="No spatial query spec provided")

        try:
            validated = SpatialQuerySpec(**spec)
        except Exception as e:
            return ToolResult(success=False, data=None, error=f"Invalid spec: {e}")

        try:
            center = self._resolve_center(validated)
        except ValueError as e:
            return ToolResult(success=False, data=None, error=str(e))

        # Assemble UNION of requested layers
        blocks = [
            self._build_infra_block(validated, center),
            self._build_conflict_block(validated, center),
            self._build_boundary_block(validated, center),
        ]
        blocks = [b for b in blocks if b is not None]
        if not blocks:
            return ToolResult(success=False, data=None, error="No layers requested")

        final_sql = " UNION ALL ".join(blocks)

        # Convert Protobuf types to Python native types
        infra_types = validated.infra_types
        if infra_types and not isinstance(infra_types, list):
            infra_types = list(infra_types)

        event_types = validated.event_types
        if event_types and not isinstance(event_types, list):
            event_types = list(event_types)

        country_codes = validated.country_codes
        if country_codes and not isinstance(country_codes, list):
            country_codes = list(country_codes)

        params = {
            "center_iso3": validated.center_iso3.upper() if validated.center_iso3 else None,
            "center_lon": validated.center_lon,
            "center_lat": validated.center_lat,
            "radius_m": validated.radius_km * 1000,  # ST_DWithin uses meters
            "infra_types": infra_types,
            "event_types": event_types,
            "country_codes": [c.upper() for c in country_codes] if country_codes else None,
            "date_from": validated.date_from,
            "date_to": validated.date_to,
            "limit": validated.limit,
        }

        try:
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(f"SET statement_timeout = '{self.STATEMENT_TIMEOUT_MS}'")
                    cur.execute(final_sql, params)
                    columns = [desc[0] for desc in cur.description]
                    rows = cur.fetchall()
                    conn.rollback()  # Read-only

            results = [dict(zip(columns, row)) for row in rows]

            # Group by layer
            grouped = {}
            for r in results:
                layer = r.get('layer', 'unknown')
                grouped.setdefault(layer, []).append(r)

            return ToolResult(
                success=True,
                data={"results": results, "by_layer": grouped, "total": len(results)},
                metadata={
                    "center": validated.center_iso3 or f"{validated.center_lat},{validated.center_lon}",
                    "radius_km": validated.radius_km,
                    "layers": list(grouped.keys()),
                },
            )
        except Exception as e:
            logger.error(f"SpatialTool query error: {e}")
            return ToolResult(success=False, data=None, error=str(e))

    def _format_success(self, data: Any, metadata: Dict) -> str:
        """Format spatial results for LLM synthesis."""
        if not data or not data.get("results"):
            return "[SPATIAL: No results]"

        center = metadata.get("center", "?")
        radius = metadata.get("radius_km", "?")

        lines = [f"## Analisi Spaziale — Centro: {center}, Raggio: {radius}km"]
        lines.append(f"Total results: {data['total']}")

        for layer, items in data.get("by_layer", {}).items():
            lines.append(f"\n### {layer.upper()} ({len(items)} elementi)")
            for item in items[:20]:
                name = item.get('name', '?')
                type_ = item.get('type', '')
                dist = item.get('distance_km', '?')
                line = f"- **{name}** ({type_}) — {dist}km"
                if item.get('fatalities'):
                    line += f" — {item['fatalities']} vittime"
                if item.get('event_date'):
                    line += f" [{item['event_date']}]"
                if item.get('status'):
                    line += f" — {item['status']}"
                lines.append(line)

        return "\n".join(lines)
