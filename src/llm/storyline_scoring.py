"""
storyline_scoring.py — Romania vertical: tiered relevance scoring for storylines.

Exposes compute_romania_relevance_score() used by report_generator.py when
report_type starts with 'romania-'. Weights and sets are loaded from
config/romania_geo_scope.yaml so they can be tuned without code changes.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Set

from ..utils.logger import get_logger

logger = get_logger(__name__)

_CONFIG_PATH = Path("config/romania_geo_scope.yaml")


@lru_cache(maxsize=1)
def _load_config() -> dict:
    """Load and cache romania_geo_scope.yaml. Re-import process to refresh."""
    try:
        import yaml
        with open(_CONFIG_PATH) as f:
            cfg = yaml.safe_load(f) or {}
        logger.debug("romania_geo_scope.yaml loaded")
        return cfg
    except FileNotFoundError:
        logger.warning(f"romania_geo_scope.yaml not found at {_CONFIG_PATH} — using defaults")
        return {}
    except Exception as e:
        logger.error(f"Failed to load romania_geo_scope.yaml: {e} — using defaults")
        return {}


def _get_weights() -> dict:
    cfg = _load_config()
    defaults = {"direct": 0.35, "regional": 0.25, "trade_route": 0.15, "source": 0.15, "thematic": 0.10}
    return {**defaults, **cfg.get("weights", {})}


def _get_neighbors() -> Set[str]:
    cfg = _load_config()
    return {s.lower() for s in cfg.get("neighbors", [
        "Moldova", "Ukraine", "Bulgaria", "Hungary", "Serbia", "Black Sea", "Turkey"
    ])}


def _get_trade_routes() -> Set[str]:
    cfg = _load_config()
    return {s.lower() for s in cfg.get("trade_routes", [
        "Kazakhstan", "Uzbekistan", "Turkmenistan", "Azerbaijan", "Georgia", "Armenia",
        "Middle Corridor", "Trans-Caspian", "TITR", "Caspian Sea", "Constanta",
        "Silk Road", "BRI", "Belt and Road", "Southern Gas Corridor", "SGC",
        "TANAP", "TAP", "SOCAR", "BP Caspian", "CNPC Caspian",
    ])}


def _get_thematic_keywords() -> List[str]:
    cfg = _load_config()
    return cfg.get("thematic_keywords", [
        "Black Sea grain", "EU cohesion", "Eastern flank", "CEE banking",
        "Italian companies Romania", "Made in Italy", "Confindustria",
    ])


def _get_thresholds() -> dict:
    cfg = _load_config()
    return {**{"romania-daily": 0.20, "romania-weekly": 0.15}, **cfg.get("thresholds", {})}


def _jaccard(set_a: Set[str], set_b: Set[str]) -> float:
    """Jaccard similarity between two sets. Returns 0.0 if both empty."""
    if not set_a and not set_b:
        return 0.0
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def _entity_set(storyline: dict) -> Set[str]:
    """Extract lowercase entity strings from storyline dict."""
    entities = storyline.get("entities", {})
    if isinstance(entities, dict):
        all_ents: List[str] = []
        for v in entities.values():
            if isinstance(v, list):
                all_ents.extend(v)
        return {e.lower() for e in all_ents if isinstance(e, str)}
    return set()


def _direct_signal(storyline: dict) -> float:
    """1.0 if 'romania' in storyline entities, else 0.0."""
    entities = _entity_set(storyline)
    return 1.0 if "romania" in entities else 0.0


def _regional_signal(storyline: dict) -> float:
    """Jaccard similarity between storyline entities and NEIGHBORS_SET."""
    entities = _entity_set(storyline)
    return _jaccard(entities, _get_neighbors())


def _trade_route_signal(storyline: dict) -> float:
    """Jaccard similarity between storyline entities and TRADE_ROUTES_SET."""
    entities = _entity_set(storyline)
    return _jaccard(entities, _get_trade_routes())


def _source_signal(storyline: dict, source_geo_regions: Dict[str, str]) -> float:
    """
    Fraction of storyline articles whose source has geo_region in {romania, cee}.
    source_geo_regions: {source_name: geo_region} loaded from intelligence_sources cache.
    """
    article_sources: List[str] = storyline.get("article_sources", [])
    if not article_sources:
        return 0.0
    ro_count = sum(
        1 for s in article_sources
        if source_geo_regions.get(s, "global") in {"romania", "cee"}
    )
    return ro_count / len(article_sources)


def _thematic_signal(storyline: dict) -> float:
    """1.0 if storyline title or summary matches any thematic keyword, else 0.0."""
    text = " ".join(filter(None, [
        storyline.get("title", ""),
        storyline.get("summary", ""),
    ])).lower()
    keywords = _get_thematic_keywords()
    return 1.0 if any(kw.lower() in text for kw in keywords) else 0.0


def compute_romania_relevance_score(
    storyline: dict,
    source_geo_regions: Optional[Dict[str, str]] = None,
) -> dict:
    """
    Compute Romania relevance score for a storyline.

    Args:
        storyline: Dict with keys: entities (dict), title (str), summary (str),
                   article_sources (list[str]).
        source_geo_regions: {source_name: geo_region} for source_signal calculation.
                            If None, source_signal is 0.0.

    Returns:
        {
          "score": float [0, 1],
          "direct": float,
          "regional": float,
          "trade_route": float,
          "source": float,
          "thematic": float,
        }
    """
    if source_geo_regions is None:
        source_geo_regions = {}

    weights = _get_weights()

    direct = _direct_signal(storyline)
    regional = _regional_signal(storyline)
    trade_route = _trade_route_signal(storyline)
    source = _source_signal(storyline, source_geo_regions)
    thematic = _thematic_signal(storyline)

    score = (
        weights["direct"] * direct
        + weights["regional"] * regional
        + weights["trade_route"] * trade_route
        + weights["source"] * source
        + weights["thematic"] * thematic
    )
    score = min(1.0, max(0.0, score))

    return {
        "score": round(score, 4),
        "direct": round(direct, 4),
        "regional": round(regional, 4),
        "trade_route": round(trade_route, 4),
        "source": round(source, 4),
        "thematic": round(thematic, 4),
    }


def filter_storylines_for_report(
    storylines: List[dict],
    report_type: str,
    source_geo_regions: Optional[Dict[str, str]] = None,
) -> List[dict]:
    """
    Score and filter storylines for a Romania report type.

    Returns storylines sorted by score descending, above threshold for report_type,
    with 'romania_score' key added. Falls back to top-3 global if none pass threshold.

    Args:
        storylines: List of active storyline dicts.
        report_type: 'romania-daily' or 'romania-weekly'.
        source_geo_regions: {source_name: geo_region} mapping.

    Returns:
        List of storyline dicts with 'romania_score' key (breakdown dict).
    """
    thresholds = _get_thresholds()
    threshold = thresholds.get(report_type, 0.20)
    max_storylines = 3 if report_type == "romania-daily" else 7

    scored = []
    for sl in storylines:
        score_dict = compute_romania_relevance_score(sl, source_geo_regions)
        sl_copy = dict(sl)
        sl_copy["romania_score"] = score_dict
        scored.append(sl_copy)

    scored.sort(key=lambda x: x["romania_score"]["score"], reverse=True)

    qualified = [s for s in scored if s["romania_score"]["score"] >= threshold]

    if qualified:
        result = qualified[:max_storylines]
        logger.info(
            f"[Romania scoring] {len(qualified)} storylines ≥ {threshold} "
            f"(threshold={report_type}), returning top {len(result)}"
        )
        return result

    # Fallback: no storylines above threshold → return top-3 global
    fallback = scored[:3]
    for s in fallback:
        s["romania_score"]["_fallback"] = True
    logger.warning(
        f"[Romania scoring] No storylines ≥ {threshold} for {report_type} "
        f"— fallback to top-3 global"
    )
    return fallback
