"""
build_sc_signals_context.py
============================
Costruisce il contesto supply chain in modo DETERMINISTICO
prima della LLM call #1 (macro_analysis_prompt).

Flusso:
  1. Legge config/sc_sector_map.yaml (path fisso, non parametro)
  2. Skips indicatori confirmation-only (CASS) — trattati separatamente
  3. Per indicatori non-daily (mensili/settimanali): applica soglie
     di materialità più alte e li marca come "context" nel prompt
  4. Per ogni indicatore daily con delta sopra soglia, cerca i settori
  5. Calcola pre_confidence deterministico
  6. Aggrega per settore + corroboration boost
  7. Aggiunge blocco Cass come confirmation layer separato
  8. Produce prompt block XML per LLM call #1

L'LLM riceve:
  - Segnali SC pre-calcolati (daily indicators)
  - Contesto da indicatori mensili (bassa frequenza, alta cautela)
  - Cass come confirmation/contradiction layer
  Il suo compito è VALIDARE, ARRICCHIRE, assegnare confidence finale.

Adattamenti rispetto al design originale (planning/Archivio/build_sc_signals_context.py):
  - YAML path fisso: config/sc_sector_map.yaml (non più parametro)
  - build_sc_signals_context() non richiede sc_sector_map_path
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Frequenza degli indicatori
# ---------------------------------------------------------------------------
INDICATOR_FREQUENCY = {
    # Monthly FRED — delta calcolato MoM, non daily
    "CASS_FREIGHT_INDEX":   "monthly",
    "US_CPI":               "monthly",
    "US_UNEMPLOYMENT":      "monthly",
    "US_INDUSTRIAL_PROD":   "monthly",
    "NICKEL":               "monthly",   # FRED PNICKUSDM

    # Weekly FRED
    "FIN_STRESS_INDEX":     "weekly",

    # Daily futures yfinance — trattati come daily di default
    # ALUMINUM: ALI=F (daily)
    # WHEAT: ZW=F (daily)

    # Rimossi dal sistema
    # TED_SPREAD: rimosso
    # EPU_GLOBAL: rimosso
    # USD_RUB:    rimosso
}

# Indicatori che non generano segnali primari — solo conferma
CONFIRMATION_ONLY_INDICATORS = {"CASS_FREIGHT_INDEX"}

# ---------------------------------------------------------------------------
# Soglie materialità pre-confidence
# ---------------------------------------------------------------------------
PRE_CONFIDENCE_MATRIX = {
    ("significant", "high"):   "high",
    ("significant", "medium"): "medium",
    ("significant", "low"):    "low",
    ("notable",     "high"):   "medium",
    ("notable",     "medium"): "low",
    ("notable",     "low"):    None,
    ("noise",       "high"):   None,
    ("noise",       "medium"): None,
    ("noise",       "low"):    None,
}

# Per indicatori mensili: scala un livello verso il basso
PRE_CONFIDENCE_MATRIX_MONTHLY = {
    ("significant", "high"):   "medium",
    ("significant", "medium"): "low",
    ("significant", "low"):    None,
    ("notable",     "high"):   "low",
    ("notable",     "medium"): None,
    ("notable",     "low"):    None,
    ("noise",       "high"):   None,
    ("noise",       "medium"): None,
    ("noise",       "low"):    None,
}

MIN_PRE_CONFIDENCE = {"high", "medium"}

# ---------------------------------------------------------------------------
# YAML path fisso
# ---------------------------------------------------------------------------
_SC_MAP_PATH = Path(__file__).parent.parent.parent / "config" / "sc_sector_map.yaml"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class RawSCSignal:
    sector: str
    indicator_key: str
    delta_pct: float
    materiality: str
    direction_active: bool
    mechanism: str
    lag: str
    pre_confidence: str
    monitor_sources: list
    is_monthly: bool = False


@dataclass
class AggregatedSCSignal:
    sector: str
    pre_confidence: str
    lag: str
    contributing_indicators: List[str]
    mechanisms: List[str]
    monitor_sources: List[str]
    corroboration_count: int
    has_monthly_only: bool = False


@dataclass
class CassConfirmation:
    """
    Stato del Cass Freight Index come confirmation layer.
    Non genera segnali SC — valida o contraddice quelli esistenti.
    """
    value: Optional[float]
    delta_mom: Optional[float]
    is_fresh: bool
    direction: Optional[str]
    prompt_note: str


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def _get_pre_confidence(materiality: str, rel_confidence: str, is_monthly: bool) -> Optional[str]:
    matrix = PRE_CONFIDENCE_MATRIX_MONTHLY if is_monthly else PRE_CONFIDENCE_MATRIX
    return matrix.get((materiality, rel_confidence))


def _build_cass_confirmation(
    indicators_today: Dict[str, float],
    indicator_values: Dict[str, float],
    sc_map: dict,
) -> CassConfirmation:
    cass_data = sc_map.get("CASS_FREIGHT_INDEX", {})
    if not cass_data:
        return CassConfirmation(
            value=None, delta_mom=None, is_fresh=False,
            direction=None,
            prompt_note="CASS_FREIGHT_INDEX: not in sc_sector_map."
        )

    value = indicator_values.get("CASS_FREIGHT_INDEX") if indicator_values else None
    delta_mom = indicators_today.get("CASS_FREIGHT_INDEX")

    if delta_mom is None:
        return CassConfirmation(
            value=value, delta_mom=None, is_fresh=False,
            direction=None,
            prompt_note=(
                "CASS FREIGHT INDEX: no data available for this period. "
                "Cannot use as SC confirmation."
            )
        )

    if abs(delta_mom) < 1.5:
        direction = "neutral"
    elif delta_mom < 0:
        direction = "bearish"
    else:
        direction = "bullish"

    confirmation_template = cass_data.get("confirmation_logic", {}).get("use_in_prompt", "")
    note = confirmation_template.format(
        value=f"{value:.2f}" if value else "N/A",
        delta_mom=f"{delta_mom:+.1f}" if delta_mom is not None else "N/A",
    )

    if not note:
        note = (
            f"CASS FREIGHT INDEX: {value or 'N/A'} "
            f"(MoM: {delta_mom:+.1f}% — {direction.upper()}). "
            f"Monthly indicator — use as SC confirmation layer only."
        )

    return CassConfirmation(
        value=value,
        delta_mom=delta_mom,
        is_fresh=True,
        direction=direction,
        prompt_note=note,
    )


def _aggregate_by_sector(raw_signals: List[RawSCSignal]) -> List[AggregatedSCSignal]:
    by_sector: dict = {}
    for sig in raw_signals:
        by_sector.setdefault(sig.sector, []).append(sig)

    conf_rank = {"high": 2, "medium": 1, "low": 0}
    lag_rank  = {"immediate": 0, "short": 1, "medium": 2, "structural": 3}
    result = []

    for sector, signals in by_sector.items():
        best_conf = max(signals, key=lambda s: conf_rank[s.pre_confidence]).pre_confidence
        best_lag  = min(signals, key=lambda s: lag_rank[s.lag]).lag

        all_sources: List[str] = []
        seen: set = set()
        for s in signals:
            for src in s.monitor_sources:
                if src not in seen:
                    all_sources.append(src)
                    seen.add(src)

        # Corroboration boost: 2+ segnali medium → high
        # Solo se almeno uno è da indicatore daily (non mensile)
        has_daily = any(not s.is_monthly for s in signals)
        if len(signals) >= 2 and best_conf == "medium" and has_daily:
            best_conf = "high"
            logger.debug(f"SC '{sector}' promoted to high by corroboration "
                         f"({len(signals)} indicators, has_daily={has_daily})")

        has_monthly_only = all(s.is_monthly for s in signals)

        result.append(AggregatedSCSignal(
            sector=sector,
            pre_confidence=best_conf,
            lag=best_lag,
            contributing_indicators=[s.indicator_key for s in signals],
            mechanisms=[s.mechanism for s in signals],
            monitor_sources=all_sources,
            corroboration_count=len(signals),
            has_monthly_only=has_monthly_only,
        ))

    result.sort(
        key=lambda s: (conf_rank[s.pre_confidence], s.corroboration_count, not s.has_monthly_only),
        reverse=True
    )
    return result


def _build_prompt_block(
    signals: List[AggregatedSCSignal],
    cass: CassConfirmation,
) -> str:
    lines = ["<sc_pre_signals>"]
    lines.append("  <!-- Pre-computed from sc_sector_map.yaml. Validate and enrich. -->")
    lines.append("  <!-- Indicators marked is_monthly=true are stale (monthly data): -->")
    lines.append("  <!-- treat as structural context, not as fresh daily signals.   -->")

    if not signals:
        lines.append("  <no_signals>No SC signals above threshold today.</no_signals>")
    else:
        for sig in signals:
            monthly_flag = " is_monthly='true'" if sig.has_monthly_only else ""
            lines.append(
                f"  <signal sector='{sig.sector}'"
                f" pre_confidence='{sig.pre_confidence}'"
                f" lag='{sig.lag}'"
                f" corroboration='{sig.corroboration_count}'"
                f"{monthly_flag}>"
            )
            lines.append(f"    <indicators>{', '.join(sig.contributing_indicators)}</indicators>")
            for i, mech in enumerate(sig.mechanisms):
                ind = sig.contributing_indicators[i] if i < len(sig.contributing_indicators) else "?"
                mech_short = mech[:200] + "..." if len(mech) > 200 else mech
                lines.append(f"    <mechanism indicator='{ind}'>{mech_short}</mechanism>")
            lines.append(f"    <monitor_sources>{', '.join(sig.monitor_sources)}</monitor_sources>")
            lines.append("  </signal>")

    lines.append("")
    lines.append("  <!-- CASS FREIGHT INDEX: confirmation layer only, not a primary signal -->")
    if cass.direction:
        lines.append(f"  <cass_confirmation direction='{cass.direction}'>")
        lines.append(f"    {cass.prompt_note}")
        lines.append("  </cass_confirmation>")
    else:
        lines.append(f"  <cass_confirmation>{cass.prompt_note}</cass_confirmation>")

    lines.append("</sc_pre_signals>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_sc_signals_context(
    indicators_today: Dict[str, float],
    indicator_materiality: Dict[str, str],
    indicator_values: Optional[Dict[str, float]] = None,
) -> Tuple[List[AggregatedSCSignal], str]:
    """
    Costruisce il contesto supply chain deterministico.

    Parameters
    ----------
    indicators_today : dict
        {INDICATOR_KEY: delta_%} per tutti gli indicatori fetchati oggi.
    indicator_materiality : dict
        {INDICATOR_KEY: materiality_level} già calcolato da match_convergences.
        Valori: "noise" | "notable" | "significant"
    indicator_values : dict | None
        {INDICATOR_KEY: valore_assoluto} — serve per il Cass (valore corrente).

    Returns
    -------
    (List[AggregatedSCSignal], str)
        Segnali aggregati + prompt block XML.
    """
    try:
        with open(_SC_MAP_PATH, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except FileNotFoundError:
        logger.error(f"[build_sc_signals_context] sc_sector_map.yaml not found: {_SC_MAP_PATH}")
        return [], "<sc_pre_signals><error>sc_sector_map.yaml not found</error></sc_pre_signals>"
    except Exception as e:
        logger.error(f"[build_sc_signals_context] Failed to load sc_sector_map.yaml: {e}")
        return [], "<sc_pre_signals><error>YAML load failed</error></sc_pre_signals>"

    sc_map = raw.get("sc_sector_map", {})
    raw_signals: List[RawSCSignal] = []

    for indicator_key, delta in indicators_today.items():
        if indicator_key in CONFIRMATION_ONLY_INDICATORS:
            continue

        if indicator_key not in sc_map:
            continue

        entry = sc_map[indicator_key]

        if entry.get("role") == "confirmation_only":
            continue

        is_monthly = INDICATOR_FREQUENCY.get(indicator_key) == "monthly"
        materiality = indicator_materiality.get(indicator_key, "noise")

        if materiality == "noise":
            continue

        impacts = entry.get("impacts", [])

        for impact in impacts:
            expected_direction = impact.get("direction", +1)
            actual_direction = +1 if delta > 0 else -1

            if actual_direction != int(expected_direction):
                continue

            rel_confidence = impact.get("confidence", "low")
            pre_conf = _get_pre_confidence(materiality, rel_confidence, is_monthly)

            if pre_conf not in MIN_PRE_CONFIDENCE:
                continue

            raw_signals.append(RawSCSignal(
                sector=impact["sector"],
                indicator_key=indicator_key,
                delta_pct=delta,
                materiality=materiality,
                direction_active=True,
                mechanism=impact.get("mechanism", "").strip(),
                lag=impact.get("lag", "medium"),
                pre_confidence=pre_conf,
                monitor_sources=impact.get("monitor_sources", []),
                is_monthly=is_monthly,
            ))

    cass = _build_cass_confirmation(indicators_today, indicator_values or {}, sc_map)
    aggregated = _aggregate_by_sector(raw_signals)
    prompt_block = _build_prompt_block(aggregated, cass)

    logger.info(
        f"[build_sc_signals_context] {len(aggregated)} settori SC con segnali "
        f"({'|'.join(f'{s.sector}:{s.pre_confidence}' for s in aggregated[:5])})"
        if aggregated else "[build_sc_signals_context] Nessun segnale SC sopra soglia"
    )

    return aggregated, prompt_block


# ---------------------------------------------------------------------------
# Istruzione LLM per validazione SC signals
# ---------------------------------------------------------------------------
SC_VALIDATION_INSTRUCTION = """
=== SUPPLY CHAIN SIGNAL VALIDATION ===
The <sc_pre_signals> block contains pre-computed signals derived
deterministically from indicator movements and the sc_sector_map ontology.

Signals marked is_monthly='true' come from monthly indicators (stale data).
Treat them as structural background context, not as fresh daily triggers.
Do NOT assign confidence_final > 'low' to monthly-only signals
unless corroborated by a fresh daily indicator in the same direction.

Your task:
1. VALIDATE each signal against today's macro context.
2. SET confidence_final: accept, upgrade, or downgrade pre_confidence.
   Upgrade if: corroboration >= 2 AND at least one daily indicator confirms.
   Downgrade if: Cass direction contradicts the signal (bearish signal + Cass bullish).
   Discard if: implausible given today's full context.
3. ENRICH signal field with specific, quantified description.
4. USE Cass confirmation: if direction='bearish', it corroborates stress signals.
   If direction='bullish', it contradicts them — flag as divergence.
5. ADD new signals if you identify SC implications not covered above.
6. SET monitor_sources from available OSINT subcategories.
""".strip()
