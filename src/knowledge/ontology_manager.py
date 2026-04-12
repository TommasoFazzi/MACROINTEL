"""
OntologyManager — Singleton loader for the Asset Theory Library.

Loads `config/asset_theory_library.yaml` once at boot and provides:
1. get_ontology(key) → ontology text for a specific indicator
2. get_correlations(key) → correlation map {related_key: (direction, rationale)}
3. build_jit_context(top_movers) → formatted context block for LLM injection
4. screen_anomalies(indicators) → data-driven anomaly detection (Z-score + delta)

Usage:
    from src.knowledge.ontology_manager import OntologyManager

    mgr = OntologyManager()
    context = mgr.build_jit_context(['VIX', 'COPPER', 'US_10Y_YIELD'])

Integration:
    Called by `report_generator.py._generate_macro_analysis()` to inject
    Just-In-Time theoretical context for only the top anomalous indicators.
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from ..utils.logger import get_logger

logger = get_logger(__name__)

# Soglie di materialità per categoria — allineate a src/macro/match_convergences.py
# Usate in screen_anomalies() per normalizzare i delta e produrre un ranking
# indipendente dalla scala dell'indicatore (VIX vs %, bps, ecc.)
_MATERIALITY_SIGNIFICANT: Dict[str, float] = {
    "RATES":       0.10,
    "VOLATILITY":  3.0,
    "COMMODITIES": 2.0,
    "FX":          1.0,
    "INDICES":     1.5,
    "CREDIT_RISK": 0.15,
    "INFLATION":   0.08,
    "ECONOMY":     0.3,
    "SHIPPING":    1.5,
    "CRYPTO":      5.0,
}
_MATERIALITY_SIGNIFICANT_DEFAULT = 1.5

# Indicatori esclusi dai top movers (reliability='restricted' o segnale ambiguo)
_EXCLUDED_FROM_TOP_MOVERS = {"USD_CNH"}


class OntologyManager:
    """
    Singleton that loads the Asset Theory Library YAML once and provides
    JIT context injection methods.

    Thread-safe: YAML is loaded once at first instantiation and cached.
    """

    _instance: Optional['OntologyManager'] = None
    _loaded: bool = False
    _indicators: Dict[str, Dict[str, Any]] = {}
    _convergences: Dict[str, Any] = {}   # macro_convergences.yaml
    _sc_map: Dict[str, Any] = {}         # sc_sector_map.yaml

    def __new__(cls, config_path: Optional[str] = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, config_path: Optional[str] = None):
        if OntologyManager._loaded:
            return

        project_root = Path(__file__).parent.parent.parent
        if config_path is None:
            config_path = str(project_root / "config" / "asset_theory_library.yaml")

        self._load(config_path)
        self._load_convergences(str(project_root / "config" / "macro_convergences.yaml"))
        self._load_sc_map(str(project_root / "config" / "sc_sector_map.yaml"))
        OntologyManager._loaded = True

    def _load(self, config_path: str):
        """Load asset_theory_library.yaml into memory."""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)

            OntologyManager._indicators = data.get('indicators', {})
            logger.info(
                f"[OntologyManager] Loaded {len(OntologyManager._indicators)} indicator ontologies "
                f"from {os.path.basename(config_path)}"
            )
        except FileNotFoundError:
            logger.warning(f"[OntologyManager] Config not found: {config_path}")
            OntologyManager._indicators = {}
        except Exception as e:
            logger.error(f"[OntologyManager] Failed to load config: {e}")
            OntologyManager._indicators = {}

    def _load_convergences(self, config_path: str):
        """Load macro_convergences.yaml into memory."""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            OntologyManager._convergences = data.get('convergences', {})
            logger.info(
                f"[OntologyManager] Loaded {len(OntologyManager._convergences)} convergence patterns"
            )
        except FileNotFoundError:
            logger.warning(f"[OntologyManager] macro_convergences.yaml not found: {config_path}")
            OntologyManager._convergences = {}
        except Exception as e:
            logger.error(f"[OntologyManager] Failed to load macro_convergences.yaml: {e}")
            OntologyManager._convergences = {}

    def _load_sc_map(self, config_path: str):
        """Load sc_sector_map.yaml into memory."""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            OntologyManager._sc_map = data.get('sc_sector_map', {})
            logger.info(
                f"[OntologyManager] Loaded {len(OntologyManager._sc_map)} SC sector mappings"
            )
        except FileNotFoundError:
            logger.warning(f"[OntologyManager] sc_sector_map.yaml not found: {config_path}")
            OntologyManager._sc_map = {}
        except Exception as e:
            logger.error(f"[OntologyManager] Failed to load sc_sector_map.yaml: {e}")
            OntologyManager._sc_map = {}

    # ── Accessors ───────────────────────────────────────────────────────────

    def get_ontology(self, key: str) -> Optional[str]:
        """Get the ontology description for an indicator."""
        entry = OntologyManager._indicators.get(key)
        return entry.get('ontology', '').strip() if entry else None

    def get_correlations(self, key: str) -> Dict[str, Tuple[int, str]]:
        """Get the correlation map for an indicator.

        Returns: {related_key: (direction, rationale)}
        """
        entry = OntologyManager._indicators.get(key)
        if not entry or 'correlations' not in entry:
            return {}

        result = {}
        for related_key, val in entry['correlations'].items():
            if isinstance(val, list) and len(val) == 2:
                result[related_key] = (int(val[0]), str(val[1]))
        return result

    def get_spread_signal(self, key: str) -> Optional[Dict[str, str]]:
        """Get the spread signal config for composite indicators (e.g. USD_CNH)."""
        entry = OntologyManager._indicators.get(key)
        return entry.get('spread_signal') if entry else None

    def has_indicator(self, key: str) -> bool:
        """Check if an indicator exists in the ontology."""
        return key in OntologyManager._indicators

    @property
    def all_keys(self) -> List[str]:
        """List all indicator keys in the ontology."""
        return list(OntologyManager._indicators.keys())

    # ── Anomaly Screener ────────────────────────────────────────────────────

    def screen_anomalies(
        self,
        indicators: List[Dict[str, Any]],
        prev_indicators: Optional[List[Dict[str, Any]]] = None,
        top_n: int = 6,
        z_score_threshold: float = 1.5,
        metadata: Optional[Dict[str, Dict]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Data-driven anomaly screener: identifies the top movers from daily macro data.

        Phase 1 of the "Intelligence-Driven" funnel.
        Scores each indicator as: abs(delta_pct) / materiality_significant[category]
        so that a 3-point VIX move is not swamped by a 1% oil move in raw % terms.

        USD_CNH is excluded from top movers (reliability='restricted' — PBoC fixing
        distorts the signal; included in context text separately with a warning).

        Args:
            indicators: List of dicts from macro_indicators table
                        (must have 'indicator_key', 'value', 'previous_value', 'category')
            prev_indicators: Optional list of previous-day indicators for delta calculation.
                             If provided, used for delta; otherwise falls back to 'previous_value' field.
            top_n: Number of top movers to return (default: 6)
            z_score_threshold: Z-score threshold (reserved for future use)
            metadata: Optional {key: {staleness_days, expected_frequency, ...}}
                      from macro_indicator_metadata. Not used for filtering here
                      (filtering is done in match_convergences); included for
                      future downstream consumers that may need freshness info.

        Returns:
            List of dicts with keys: 'key', 'value', 'prev_value', 'delta_pct',
            'abs_delta', 'anomaly_score' sorted by anomaly_score descending.
        """
        prev_map: Dict[str, float] = {}
        if prev_indicators:
            prev_map = {
                i['indicator_key']: float(i['value'])
                for i in prev_indicators
                if i.get('value') is not None
            }

        scored = []
        for ind in indicators:
            key = ind.get('indicator_key', '')

            # Exclude unreliable indicators from top-mover ranking
            if key in _EXCLUDED_FROM_TOP_MOVERS:
                continue

            try:
                value = float(ind.get('value', 0))
            except (ValueError, TypeError):
                continue

            prev_value = prev_map.get(key) or ind.get('previous_value')
            if prev_value is None:
                continue

            try:
                prev_value = float(prev_value)
            except (ValueError, TypeError):
                continue

            if prev_value == 0:
                continue

            delta_pct = ((value - prev_value) / abs(prev_value)) * 100

            # Materiality-normalized anomaly score
            category = ind.get('category', 'COMMODITIES')
            mat_sig = _MATERIALITY_SIGNIFICANT.get(category, _MATERIALITY_SIGNIFICANT_DEFAULT)
            anomaly_score = abs(delta_pct) / mat_sig

            scored.append({
                'key': key,
                'value': value,
                'prev_value': prev_value,
                'delta_pct': round(delta_pct, 2),
                'abs_delta': abs(delta_pct),
                'anomaly_score': round(anomaly_score, 3),
                'category': category,
            })

        scored.sort(key=lambda x: x['anomaly_score'], reverse=True)
        top_movers = scored[:top_n]

        if top_movers:
            logger.info(
                f"[OntologyManager] Top {len(top_movers)} movers: "
                + ", ".join(
                    f"{m['key']} ({m['delta_pct']:+.1f}%, score={m['anomaly_score']:.2f})"
                    for m in top_movers
                )
            )

        return top_movers

    # ── JIT Context Builder ─────────────────────────────────────────────────

    def build_jit_context(
        self,
        top_mover_keys: List[str],
        include_cross_correlations: bool = True,
    ) -> str:
        """
        Build Just-In-Time theoretical context for the LLM.

        Phase 2 of the "Intelligence-Driven" funnel.
        Fetches ontology + correlations only for the top movers identified by the anomaly screener.

        Args:
            top_mover_keys: List of indicator keys (e.g. ['VIX', 'COPPER', 'BRENT_OIL'])
            include_cross_correlations: If True, include mutual correlations between the top movers

        Returns:
            Formatted text block for injection into the LLM prompt.
        """
        if not top_mover_keys:
            return ""

        lines = [
            "=== THEORETICAL CONTEXT: TOP MOVERS ===",
            "(Use this deep analysis to interpret the anomalous movements above)",
            "",
        ]

        top_mover_set = set(top_mover_keys)

        for key in top_mover_keys:
            ontology = self.get_ontology(key)
            correlations = self.get_correlations(key)
            spread_signal = self.get_spread_signal(key)

            if not ontology:
                continue

            lines.append(f"### {key}")
            lines.append(f"**Cosa è e da cosa è guidato:**")
            lines.append(ontology)

            # Spread signal (for composite indicators like USD_CNH)
            if spread_signal:
                lines.append(f"\n**Spread Signal** ({spread_signal.get('formula', '')}):")
                for state in ['positive_wide', 'negative_wide', 'near_zero']:
                    if state in spread_signal:
                        label = state.replace('_', ' ').title()
                        lines.append(f"  - {label}: {spread_signal[state].strip()}")

            # Correlations — filter to show only relevant ones
            if correlations:
                # Show correlations with OTHER top movers first (cross-links)
                cross_corrs = {k: v for k, v in correlations.items() if k in top_mover_set and k != key}
                other_corrs = {k: v for k, v in correlations.items() if k not in top_mover_set}

                if cross_corrs and include_cross_correlations:
                    lines.append(f"\n**Correlazioni con gli altri top movers di oggi:**")
                    for related, (direction, rationale) in cross_corrs.items():
                        dir_str = "↑↑" if direction > 0 else "↑↓"
                        lines.append(f"  {dir_str} {related}: {rationale}")

                # Show top 3 most relevant other correlations
                if other_corrs:
                    lines.append(f"\n**Altre correlazioni chiave:**")
                    for i, (related, (direction, rationale)) in enumerate(other_corrs.items()):
                        if i >= 3:
                            break
                        dir_str = "↑↑" if direction > 0 else "↑↓"
                        lines.append(f"  {dir_str} {related}: {rationale}")

            lines.append("")

        return "\n".join(lines)

    def build_full_context_for_keys(self, keys: List[str]) -> str:
        """Build full ontology context for a list of indicator keys (for Oracle queries)."""
        if not keys:
            return ""

        lines = []
        for key in keys:
            ontology = self.get_ontology(key)
            if ontology:
                lines.append(f"**{key}**: {ontology}")

        return "\n\n".join(lines)

    # ── Convergence + SC Map Accessors ──────────────────────────────────────

    @property
    def convergences(self) -> Dict[str, Any]:
        """Raw convergence dict loaded from macro_convergences.yaml."""
        return OntologyManager._convergences

    @property
    def sc_map(self) -> Dict[str, Any]:
        """Raw SC sector map loaded from sc_sector_map.yaml."""
        return OntologyManager._sc_map
