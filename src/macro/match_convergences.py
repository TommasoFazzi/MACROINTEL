"""
match_convergences.py
=====================
Confronta TUTTI gli indicatori disponibili contro le convergenze
definite in config/macro_convergences.yaml.

NON usa i top movers — lavora sull'intero snapshot giornaliero.
Un pattern può attivarsi anche con movimenti moderati ma coordinati.

Output: lista di ConvergenceMatch ordinata per confidence score (desc).

Adattamenti rispetto al design originale (planning/Archivio/match_convergences.py):
  - _get_category() usa KEY_CATEGORY hardcoded (la YAML non ha il campo category)
  - match_convergences() accetta metadata: Dict[str, Dict] pre-loaded (non DB call)
  - Staleness weight logic: indicatori troppo stale vengono ignorati o penalizzati
  - YAML path caricato internamente (config/macro_convergences.yaml)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Soglie di materialità per categoria (% delta assoluto)
# Allineate alle SIGNAL MATERIALITY nel prompt macro
# ---------------------------------------------------------------------------
MATERIALITY = {
    "RATES":       {"notable": 0.05, "significant": 0.10},   # punti % (5bp / 10bp)
    "VOLATILITY":  {"notable": 1.0,  "significant": 3.0},    # punti VIX
    "COMMODITIES": {"notable": 1.0,  "significant": 2.0},    # %
    "FX":          {"notable": 0.5,  "significant": 1.0},    # %
    "INDICES":     {"notable": 0.5,  "significant": 1.5},    # %
    "CREDIT_RISK": {"notable": 0.05, "significant": 0.15},   # punti %
    "INFLATION":   {"notable": 0.03, "significant": 0.08},   # punti %
    "ECONOMY":     {"notable": 0.1,  "significant": 0.3},    # %
    "SHIPPING":    {"notable": 0.5,  "significant": 1.5},    # %
    "CRYPTO":      {"notable": 2.0,  "significant": 5.0},    # %
}

MATERIALITY_DEFAULT = {"notable": 0.5, "significant": 1.5}

# Mappatura hardcoded indicatore → categoria (il YAML non ha il campo category)
# Allineata con i valori in macro_indicators.category nel DB
KEY_CATEGORY: Dict[str, str] = {
    # RATES
    "US_10Y_YIELD": "RATES",
    "US_2Y_YIELD": "RATES",
    "YIELD_CURVE_10Y_2Y": "RATES",
    "REAL_RATE_10Y": "RATES",
    "BREAKEVEN_10Y": "RATES",
    "INFLATION_EXPECTATION_5Y": "INFLATION",
    # VOLATILITY
    "VIX": "VOLATILITY",
    # COMMODITIES
    "BRENT_OIL": "COMMODITIES",
    "WTI_OIL": "COMMODITIES",
    "NATURAL_GAS": "COMMODITIES",
    "GOLD": "COMMODITIES",
    "SILVER": "COMMODITIES",
    "COPPER": "COMMODITIES",
    "WHEAT": "COMMODITIES",
    "NICKEL": "COMMODITIES",
    "ALUMINUM": "COMMODITIES",
    "URANIUM": "COMMODITIES",
    # FX
    "EUR_USD": "FX",
    "USD_JPY": "FX",
    "DOLLAR_INDEX": "FX",
    "USD_CNY": "FX",
    "USD_CNH": "FX",
    "USD_GBP": "FX",
    # INDICES
    "SP500": "INDICES",
    "NASDAQ": "INDICES",
    # CREDIT_RISK
    "US_HY_SPREAD": "CREDIT_RISK",
    "FIN_STRESS_INDEX": "CREDIT_RISK",
    # ECONOMY / INFLATION
    "US_CPI": "INFLATION",
    "US_UNEMPLOYMENT": "ECONOMY",
    "US_INDUSTRIAL_PROD": "ECONOMY",
    "CASS_FREIGHT_INDEX": "SHIPPING",
    # CRYPTO
    "BITCOIN": "CRYPTO",
}

# Staleness limits per frequenza (giorni)
_STALE_MAX = {"daily": 2, "weekly": 10, "monthly": 45}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TriggerResult:
    key: str
    direction_expected: int       # +1 o -1 dalla convergenza YAML
    direction_actual: int         # +1 o -1 dal delta osservato
    delta_pct: float              # delta % osservato
    materiality: str              # "noise" | "notable" | "significant"
    aligned: bool                 # direzione corretta E sopra soglia noise
    staleness_note: Optional[str] = None   # se penalizzato per staleness


@dataclass
class ConvergenceMatch:
    convergence_id: str           # es. "risk_off_systemic"
    label: str
    narrative_horizon: str
    active: bool                  # True se confidence >= MIN_CONFIDENCE
    confidence: float             # 0.0 – 1.0
    triggers_total: int
    triggers_aligned: int
    triggers_significant: int
    trigger_details: List[TriggerResult]
    causal_chain: str
    llm_disambiguation: dict
    primary_trigger_note: Optional[str] = None
    spread_signal: Optional[dict] = None


MIN_CONFIDENCE = 0.55


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def _get_category(key: str) -> str:
    """Recupera la categoria di un indicatore dalla mappa hardcoded."""
    return KEY_CATEGORY.get(key, "COMMODITIES")


def _materiality_level(delta_abs: float, category: str) -> str:
    thresholds = MATERIALITY.get(category, MATERIALITY_DEFAULT)
    if delta_abs < thresholds["notable"]:
        return "noise"
    if delta_abs < thresholds["significant"]:
        return "notable"
    return "significant"


def _staleness_weight(key: str, metadata_dict: Dict[str, Dict]) -> tuple[float, Optional[str]]:
    """
    Restituisce (weight_multiplier, staleness_note).

    Logic:
      - staleness <= max_stale           → weight 1.0 (fresco)
      - max_stale < staleness <= 3x      → weight 0.5 (stale ma contestuale)
      - staleness > 3x max_stale         → weight 0.0 (ignora completamente)

    Senza questo, NICKEL a 67gg contribuisce a china_stress_global_slowdown
    con peso pieno — esattamente ciò che il metadata layer vuole prevenire.
    """
    entry = metadata_dict.get(key, {})
    staleness = entry.get("staleness_days", 0) or 0
    freq = entry.get("expected_frequency", "daily")
    max_stale = _STALE_MAX.get(freq, 2)

    if staleness > max_stale * 3:
        return 0.0, f"[staleness={staleness}d >> 3x max={max_stale * 3}d: ignorato]"
    elif staleness > max_stale:
        return 0.5, f"[staleness={staleness}d > max={max_stale}d: peso 0.5]"
    return 1.0, None


def _score_convergence(
    convergence: dict,
    indicators_today: Dict[str, float],
    metadata_dict: Dict[str, Dict],
) -> ConvergenceMatch:
    """
    Calcola il ConvergenceMatch per una singola convergenza.

    Scoring:
      - Ogni trigger allineato contribuisce in modo proporzionale alla materialità:
          notable    → peso base 1.0
          significant → peso base 1.5
      - Staleness weight multiplier applicato prima di accumulare il peso:
          fresco     → 1.0x
          stale      → 0.5x
          troppo stale → 0.0x (trigger ignorato, conta come non-allineato)
      - Score finale = somma_pesi_allineati / somma_pesi_massimi_possibili
      - Trigger in `context` non contribuiscono allo score.
    """
    triggers_raw = convergence.get("trigger", {})
    trigger_details: List[TriggerResult] = []

    total_weight = 0.0
    aligned_weight = 0.0
    triggers_aligned = 0
    triggers_significant = 0

    for key, expected_dir in triggers_raw.items():
        stale_mult, stale_note = _staleness_weight(key, metadata_dict)

        # Dato mancante o completamente stale → conta come non allineato
        delta = indicators_today.get(key)
        if delta is None or stale_mult == 0.0:
            base_weight = 1.0 * stale_mult if stale_mult > 0.0 else 1.0
            # Se stale_mult == 0.0, contribuisce con peso pieno al denominatore
            # (trigger non disponibile: non dovrebbe aumentare il numeratore)
            total_weight += 1.0
            trigger_details.append(TriggerResult(
                key=key,
                direction_expected=int(expected_dir),
                direction_actual=0,
                delta_pct=0.0,
                materiality="noise",
                aligned=False,
                staleness_note=stale_note,
            ))
            continue

        category = _get_category(key)
        delta_abs = abs(delta)
        mat = _materiality_level(delta_abs, category)

        base_weight = 1.5 if mat == "significant" else 1.0
        effective_weight = base_weight * stale_mult
        total_weight += effective_weight if effective_weight > 0 else base_weight

        actual_dir = 1 if delta > 0 else -1
        aligned = (actual_dir == int(expected_dir)) and (mat != "noise")

        if aligned:
            aligned_weight += effective_weight
            triggers_aligned += 1
            if mat == "significant":
                triggers_significant += 1

        trigger_details.append(TriggerResult(
            key=key,
            direction_expected=int(expected_dir),
            direction_actual=actual_dir,
            delta_pct=delta,
            materiality=mat,
            aligned=aligned,
            staleness_note=stale_note,
        ))

    confidence = (aligned_weight / total_weight) if total_weight > 0 else 0.0
    active = confidence >= MIN_CONFIDENCE

    # Nota sul trigger primario (carry_trade_unwind)
    primary_trigger_note = None
    if "trigger_disambiguation" in convergence:
        td = convergence["trigger_disambiguation"]
        jpy_detail = next((t for t in trigger_details if t.key == "USD_JPY"), None)
        vix_detail = next((t for t in trigger_details if t.key == "VIX"), None)
        if jpy_detail and vix_detail:
            if jpy_detail.materiality == "significant" and vix_detail.materiality != "significant":
                primary_trigger_note = td.get("primary_trigger_jpy", "")
            elif vix_detail.materiality == "significant":
                primary_trigger_note = td.get("primary_trigger_risk_off", "")

    return ConvergenceMatch(
        convergence_id=convergence["_id"],
        label=convergence.get("label", convergence["_id"]),
        narrative_horizon=convergence.get("narrative_horizon", ""),
        active=active,
        confidence=round(confidence, 3),
        triggers_total=len(triggers_raw),
        triggers_aligned=triggers_aligned,
        triggers_significant=triggers_significant,
        trigger_details=trigger_details,
        causal_chain=convergence.get("causal_chain", ""),
        llm_disambiguation=convergence.get("llm_disambiguation", {}),
        primary_trigger_note=primary_trigger_note,
        spread_signal=convergence.get("spread_signal"),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_YAML_PATH = Path(__file__).parent.parent.parent / "config" / "macro_convergences.yaml"


def match_convergences(
    indicators_today: Dict[str, float],
    metadata: Dict[str, Dict],
    ontology_mgr=None,  # reserved for future use; category uses KEY_CATEGORY
) -> List[ConvergenceMatch]:
    """
    Entry point principale.

    Parameters
    ----------
    indicators_today : dict
        {INDICATOR_KEY: delta_pct} per TUTTI gli indicatori fetchati oggi.
        Esempio: {"VIX": +4.2, "US_HY_SPREAD": +0.08, "GOLD": -0.3, ...}
    metadata : dict
        {INDICATOR_KEY: {staleness_days, expected_frequency, ...}}
        Caricato da macro_indicator_metadata nel DB.
    ontology_mgr : OntologyManager, optional
        Riservato per usi futuri. La categoria viene risolta da KEY_CATEGORY.

    Returns
    -------
    List[ConvergenceMatch]
        Tutte le convergenze valutate, ordinate per confidence desc.
        Il chiamante filtra con `[m for m in results if m.active]`.
    """
    try:
        with open(_YAML_PATH, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except FileNotFoundError:
        logger.error(f"[match_convergences] YAML not found: {_YAML_PATH}")
        return []
    except Exception as e:
        logger.error(f"[match_convergences] Failed to load YAML: {e}")
        return []

    convergences = raw.get("convergences", {})
    results: List[ConvergenceMatch] = []

    for conv_id, conv_data in convergences.items():
        conv_data["_id"] = conv_id
        match = _score_convergence(conv_data, indicators_today, metadata)
        results.append(match)

    results.sort(key=lambda m: (m.confidence, m.triggers_significant), reverse=True)

    active = [m for m in results if m.active]
    logger.info(
        f"[match_convergences] {len(active)}/{len(results)} convergenze attive "
        + (f": {', '.join(f'{m.convergence_id}({m.confidence:.2f})' for m in active)}"
           if active else "(nessuna)")
    )
    return results
