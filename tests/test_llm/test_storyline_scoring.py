"""Unit tests for src/llm/storyline_scoring.py — Romania vertical scoring."""
import pytest
from unittest.mock import patch

from src.llm.storyline_scoring import (
    _direct_signal,
    _regional_signal,
    _trade_route_signal,
    _source_signal,
    _thematic_signal,
    _jaccard,
    compute_romania_relevance_score,
    filter_storylines_for_report,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_ROMANIA_STORYLINE = {
    "title": "BNR menține dobânda de politică monetară la 7%",
    "summary": "Banca Națională a României a decis menținerea dobânzii.",
    "entities": {"by_type": {"GPE": ["Romania", "Bucharest"], "ORG": ["BNR"]}},
    "article_sources": ["Ziarul Financiar", "Profit.ro"],
}

_CASPIAN_STORYLINE = {
    "title": "SOCAR expands Southern Gas Corridor capacity",
    "summary": "Azerbaijan's SOCAR announces expansion of TANAP pipeline.",
    "entities": {"by_type": {"GPE": ["Azerbaijan", "Turkey"], "ORG": ["SOCAR", "TANAP"]}},
    "article_sources": ["OilPrice"],
}

_UNRELATED_STORYLINE = {
    "title": "Semiconductor manufacturing boom in Taiwan",
    "summary": "TSMC expands capacity for advanced node production.",
    "entities": {"by_type": {"GPE": ["Taiwan", "USA"], "ORG": ["TSMC", "Intel"]}},
    "article_sources": ["Semiconductor Engineering"],
}

_RO_SOURCE_MAP = {
    "Ziarul Financiar": "romania",
    "Profit.ro": "romania",
    "NewStrategyCenter": "cee",
    "OilPrice": "global",
    "Semiconductor Engineering": "global",
}


# ---------------------------------------------------------------------------
# _jaccard
# ---------------------------------------------------------------------------

class TestJaccard:
    def test_identical_sets(self):
        assert _jaccard({"a", "b"}, {"a", "b"}) == 1.0

    def test_disjoint_sets(self):
        assert _jaccard({"a"}, {"b"}) == 0.0

    def test_partial_overlap(self):
        result = _jaccard({"a", "b"}, {"b", "c"})
        assert abs(result - 1/3) < 1e-9

    def test_empty_sets(self):
        assert _jaccard(set(), set()) == 0.0

    def test_one_empty(self):
        assert _jaccard({"a"}, set()) == 0.0


# ---------------------------------------------------------------------------
# direct_signal
# ---------------------------------------------------------------------------

class TestDirectSignal:
    def test_romania_in_entities(self):
        assert _direct_signal(_ROMANIA_STORYLINE) == 1.0

    def test_no_romania(self):
        assert _direct_signal(_UNRELATED_STORYLINE) == 0.0

    def test_empty_entities(self):
        assert _direct_signal({"entities": {}}) == 0.0


# ---------------------------------------------------------------------------
# regional_signal
# ---------------------------------------------------------------------------

class TestRegionalSignal:
    def test_has_neighbors(self):
        sl = {
            "entities": {"by_type": {"GPE": ["Moldova", "Ukraine", "France"]}},
        }
        score = _regional_signal(sl)
        assert score > 0.0
        assert score <= 1.0

    def test_no_neighbors(self):
        assert _regional_signal(_UNRELATED_STORYLINE) == 0.0

    def test_romania_story_with_neighbor_moldova(self):
        sl = dict(_ROMANIA_STORYLINE)
        sl["entities"] = {"by_type": {"GPE": ["Romania", "Moldova"]}}
        assert _regional_signal(sl) > 0.0


# ---------------------------------------------------------------------------
# trade_route_signal
# ---------------------------------------------------------------------------

class TestTradeRouteSignal:
    def test_caspian_storyline(self):
        score = _trade_route_signal(_CASPIAN_STORYLINE)
        assert score > 0.3, f"Expected > 0.3 for Caspian storyline, got {score}"

    def test_bri_kazakhstan(self):
        sl = {
            "title": "China BRI expansion through Kazakhstan rail",
            "entities": {"by_type": {"GPE": ["China", "Kazakhstan"], "ORG": ["BRI"]}},
        }
        score = _trade_route_signal(sl)
        assert score > 0.2

    def test_unrelated(self):
        assert _trade_route_signal(_UNRELATED_STORYLINE) == 0.0


# ---------------------------------------------------------------------------
# source_signal
# ---------------------------------------------------------------------------

class TestSourceSignal:
    def test_all_ro_sources(self):
        sl = {"article_sources": ["Ziarul Financiar", "Profit.ro"]}
        assert _source_signal(sl, _RO_SOURCE_MAP) == 1.0

    def test_mixed_sources(self):
        sl = {"article_sources": ["Ziarul Financiar", "OilPrice", "Semiconductor Engineering"]}
        assert abs(_source_signal(sl, _RO_SOURCE_MAP) - 1/3) < 1e-9

    def test_no_ro_sources(self):
        sl = {"article_sources": ["OilPrice"]}
        assert _source_signal(sl, _RO_SOURCE_MAP) == 0.0

    def test_empty_sources(self):
        assert _source_signal({"article_sources": []}, _RO_SOURCE_MAP) == 0.0

    def test_cee_source_counts(self):
        sl = {"article_sources": ["NewStrategyCenter"]}
        assert _source_signal(sl, _RO_SOURCE_MAP) == 1.0


# ---------------------------------------------------------------------------
# thematic_signal
# ---------------------------------------------------------------------------

class TestThematicSignal:
    def test_black_sea_grain(self):
        sl = {"title": "Black Sea grain corridor disruption", "summary": ""}
        assert _thematic_signal(sl) == 1.0

    def test_confindustria(self):
        sl = {"title": "Confindustria Romania meeting", "summary": ""}
        assert _thematic_signal(sl) == 1.0

    def test_unrelated(self):
        sl = {"title": "Semiconductor manufacturing", "summary": "TSMC fab expansion"}
        assert _thematic_signal(sl) == 0.0

    def test_keyword_in_summary(self):
        sl = {"title": "CEE update", "summary": "Italian companies Romania face new regulation"}
        assert _thematic_signal(sl) == 1.0


# ---------------------------------------------------------------------------
# compute_romania_relevance_score — aggregate
# ---------------------------------------------------------------------------

class TestComputeRomaniaRelevanceScore:
    def test_returns_required_keys(self):
        result = compute_romania_relevance_score(_ROMANIA_STORYLINE, _RO_SOURCE_MAP)
        assert set(result.keys()) >= {"score", "direct", "regional", "trade_route", "source", "thematic"}

    def test_score_bounds(self):
        for sl in [_ROMANIA_STORYLINE, _CASPIAN_STORYLINE, _UNRELATED_STORYLINE]:
            result = compute_romania_relevance_score(sl, _RO_SOURCE_MAP)
            assert 0.0 <= result["score"] <= 1.0

    def test_romania_story_high_score(self):
        result = compute_romania_relevance_score(_ROMANIA_STORYLINE, _RO_SOURCE_MAP)
        assert result["score"] >= 0.35, f"Expected ≥ 0.35 for Romania story, got {result}"
        assert result["direct"] == 1.0

    def test_caspian_has_trade_route(self):
        result = compute_romania_relevance_score(_CASPIAN_STORYLINE, {})
        assert result["trade_route"] > 0.0

    def test_unrelated_low_score(self):
        result = compute_romania_relevance_score(_UNRELATED_STORYLINE, _RO_SOURCE_MAP)
        assert result["score"] < 0.2, f"Expected < 0.2 for unrelated, got {result}"

    def test_no_source_map_defaults_zero(self):
        result = compute_romania_relevance_score(_ROMANIA_STORYLINE)
        assert result["source"] == 0.0

    def test_weights_sum_to_one(self):
        from src.llm.storyline_scoring import _get_weights
        w = _get_weights()
        total = sum(w.values())
        assert abs(total - 1.0) < 1e-9, f"Weights sum to {total}, expected 1.0"


# ---------------------------------------------------------------------------
# filter_storylines_for_report
# ---------------------------------------------------------------------------

class TestFilterStorylinesForReport:
    def _make_storylines(self, n: int):
        storylines = []
        for i in range(n):
            storylines.append({
                "id": i,
                "title": f"Storyline {i}",
                "summary": "",
                "entities": {},
                "article_sources": [],
            })
        return storylines

    def test_returns_scored_storylines(self):
        sls = [_ROMANIA_STORYLINE, _CASPIAN_STORYLINE, _UNRELATED_STORYLINE]
        result = filter_storylines_for_report(sls, "romania-weekly", _RO_SOURCE_MAP)
        assert all("romania_score" in s for s in result)

    def test_sorted_descending(self):
        sls = [_UNRELATED_STORYLINE, _ROMANIA_STORYLINE, _CASPIAN_STORYLINE]
        result = filter_storylines_for_report(sls, "romania-weekly", _RO_SOURCE_MAP)
        scores = [s["romania_score"]["score"] for s in result]
        assert scores == sorted(scores, reverse=True)

    def test_daily_max_3(self):
        sls = [_ROMANIA_STORYLINE] * 10
        result = filter_storylines_for_report(sls, "romania-daily", _RO_SOURCE_MAP)
        assert len(result) <= 3

    def test_fallback_when_no_threshold_met(self):
        sls = [_UNRELATED_STORYLINE] * 5
        result = filter_storylines_for_report(sls, "romania-daily", {})
        assert len(result) == 3
        assert all(s["romania_score"].get("_fallback") for s in result)
