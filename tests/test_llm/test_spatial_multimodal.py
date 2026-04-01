"""
Integration test: SpatialTool + RAGTool multimodal query routing.

Verifies that:
1. SpatialQuerySpec validates and clamps parameters correctly
2. SpatialTool produces valid structured results
3. SpatialQuerySpec correctly represents common spatial query patterns
   (in agentic mode the LLM generates the spec via function calling)

Run: pytest tests/test_llm/test_spatial_multimodal.py -v
"""

import pytest
from unittest.mock import MagicMock, patch

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.llm.tools.spatial_tool import SpatialQuerySpec, SpatialTool, ALLOWED_INFRA_TYPES


# ── SpatialQuerySpec validation tests ──────────────────────────────────────────

class TestSpatialQuerySpec:
    """Test Pydantic validation and bounds clamping."""

    def test_default_values(self):
        spec = SpatialQuerySpec()
        assert spec.radius_km == 200
        assert spec.limit == 50
        assert spec.include_infrastructure is True
        assert spec.include_conflicts is True

    def test_radius_clamping_upper(self):
        spec = SpatialQuerySpec(radius_km=5000)
        assert spec.radius_km == 2000  # clamped

    def test_radius_clamping_lower(self):
        spec = SpatialQuerySpec(radius_km=-10)
        assert spec.radius_km == 1  # clamped

    def test_limit_clamping(self):
        spec = SpatialQuerySpec(limit=999)
        assert spec.limit == 200  # clamped

    def test_valid_infra_types(self):
        spec = SpatialQuerySpec(infra_types=["PORT", "AIRPORT"])
        assert spec.infra_types == ["PORT", "AIRPORT"]

    def test_invalid_infra_type_raises(self):
        with pytest.raises(ValueError, match="Invalid infra_types"):
            SpatialQuerySpec(infra_types=["INVALID_TYPE"])

    def test_valid_event_types(self):
        spec = SpatialQuerySpec(event_types=["1", "3"])
        assert spec.event_types == ["1", "3"]

    def test_center_iso3(self):
        spec = SpatialQuerySpec(center_iso3="UKR")
        assert spec.center_iso3 == "UKR"

    def test_center_coords(self):
        spec = SpatialQuerySpec(center_lon=36.8, center_lat=50.0)
        assert spec.center_lon == 36.8
        assert spec.center_lat == 50.0

    def test_full_spec(self):
        """Test a realistic full query spec."""
        spec = SpatialQuerySpec(
            center_iso3="UKR",
            radius_km=200,
            infra_types=["AIRPORT", "POWER_PLANT"],
            event_types=["1"],
            date_from="2024-01-01",
            include_infrastructure=True,
            include_conflicts=True,
            limit=50,
        )
        assert spec.radius_km == 200
        assert len(spec.infra_types) == 2
        assert spec.date_from == "2024-01-01"


# ── SpatialTool query builder tests ────────────────────────────────────────────

class TestSpatialToolQueryBuilder:
    """Test SQL block assembly without executing queries."""

    def setup_method(self):
        self.mock_db = MagicMock()
        self.tool = SpatialTool(db=self.mock_db)

    def test_resolve_center_iso3(self):
        spec = SpatialQuerySpec(center_iso3="UKR")
        center = self.tool._resolve_center(spec)
        assert "country_boundaries" in center
        assert "%(center_iso3)s" in center

    def test_resolve_center_coords(self):
        spec = SpatialQuerySpec(center_lon=36.8, center_lat=50.0)
        center = self.tool._resolve_center(spec)
        assert "ST_SetSRID" in center
        assert "%(center_lon)s" in center

    def test_resolve_center_missing_raises(self):
        spec = SpatialQuerySpec()
        with pytest.raises(ValueError, match="Must provide"):
            self.tool._resolve_center(spec)

    def test_build_infra_block(self):
        spec = SpatialQuerySpec(center_iso3="UKR", infra_types=["PORT"])
        center = self.tool._resolve_center(spec)
        sql = self.tool._build_infra_block(spec, center)
        assert sql is not None
        assert "strategic_infrastructure" in sql
        assert "%(infra_types)s" in sql
        assert "%(radius_m)s" in sql

    def test_build_infra_block_disabled(self):
        spec = SpatialQuerySpec(center_iso3="UKR", include_infrastructure=False)
        center = self.tool._resolve_center(spec)
        assert self.tool._build_infra_block(spec, center) is None

    def test_build_conflict_block(self):
        spec = SpatialQuerySpec(center_iso3="UKR", date_from="2024-01-01")
        center = self.tool._resolve_center(spec)
        sql = self.tool._build_conflict_block(spec, center)
        assert sql is not None
        assert "conflict_events" in sql
        assert "%(date_from)s" in sql

    def test_build_conflict_block_disabled(self):
        spec = SpatialQuerySpec(center_iso3="UKR", include_conflicts=False)
        center = self.tool._resolve_center(spec)
        assert self.tool._build_conflict_block(spec, center) is None


# ── SpatialQuerySpec construction tests (agentic architecture) ─────────────────
#
# In Oracle 2.0 agentic mode, the LLM generates the SpatialQuerySpec via the
# function calling interface. These tests verify that SpatialQuerySpec correctly
# accepts the parameters the LLM would produce for common spatial query patterns.

class TestSpatialRouting:
    """Test that SpatialQuerySpec correctly represents common spatial query patterns."""

    def test_select_tools_spatial(self):
        """Test spec for infrastructure-near-country query."""
        spec = SpatialQuerySpec(
            center_iso3="UKR",
            radius_km=300,
            include_infrastructure=True,
            include_conflicts=True,
        )
        assert spec.center_iso3 == "UKR"
        assert spec.radius_km == 300
        assert spec.include_infrastructure is True
        assert spec.include_conflicts is True

    def test_spatial_radius_extraction(self):
        """Test that explicit radius is stored correctly."""
        spec = SpatialQuerySpec(
            center_iso3="UKR",
            radius_km=500,
            include_infrastructure=False,
            include_conflicts=True,
        )
        assert spec.radius_km == 500

    def test_spatial_conflict_only(self):
        """Test conflict-only spec (infrastructure disabled)."""
        spec = SpatialQuerySpec(
            center_iso3="UKR",
            include_infrastructure=False,
            include_conflicts=True,
        )
        assert spec.include_infrastructure is False
        assert spec.include_conflicts is True
