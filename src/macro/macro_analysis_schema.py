# =============================================================================
# Layer 2 — macro_analysis_prompt
# =============================================================================
# LLM call #1. Riceve:
#   - snapshot indicatori raw con delta
#   - jit_context dai top movers (ontologia + correlazioni)
#   - convergenze attive con confidence score, causal_chain, disambiguation
#
# Produce JSON strutturato che diventa il contesto del report generator.
# =============================================================================


# ---------------------------------------------------------------------------
# OUTPUT JSON SCHEMA (annotato)
# ---------------------------------------------------------------------------
#
# {
#   "risk_regime": {
#     "label": str,
#       # Uno tra (esattamente, lowercase):
#       #   "risk_off_systemic"  — stress sistemico: VIX spike + HY spread + USD flight
#       #   "risk_off_moderate"  — cautela lieve: 1-2 segnali risk-off, nessuno stress sistemico
#       #   "neutral"            — nessun regime dominante, segnali misti
#       #   "risk_on_moderate"   — espansione lieve: equity su, credito stabile
#       #   "risk_on_expansion"  — espansione piena: indicatori growth allineati bullish
#       #   "crisis_acute"       — crisi acuta: più indicatori sistemici in zona estrema
#       #   "stagflationary"     — inflazione in salita + crescita in rallentamento
#     "confidence": float,   # 0.0 – 1.0, stima LLM
#     "drivers": [str]       # max 3 frasi brevi sui fattori dominanti
#   },
#
#   "active_convergences": [
#     {
#       "id": str,                  # es. "risk_off_systemic"
#       "label": str,
#       "confidence": float,        # score da match_convergences()
#       "narrative": str,           # 2-3 frasi: meccanismo attivo oggi
#       "disambiguation_applied": str | null
#         # Se il LLM ha usato una regola di disambiguation, la cita qui.
#         # Es. "gold_divergence: gold scende — probabile liquidation event, non contraddizione"
#     }
#   ],
#
#   "macro_narrative": str,
#     # Paragrafo di 80-120 parole. Sintetizza il quadro macro del giorno.
#     # Usa le convergenze attive come struttura narrativa.
#     # Deve essere leggibile direttamente nel briefing come "dashboard intro".
#
#   "key_divergences": [
#     {
#       "description": str,   # Es. "Copper -2.1% mentre SP500 +0.8%: segnale di debolezza cinese non prezzata dall'equity"
#       "severity": str       # "notable" | "significant" | "critical"
#     }
#   ],
#     # Divergenze rispetto alle correlazioni attese dall'ontologia.
#     # Massimo 3. Vuoto se non ci sono divergenze rilevanti.
#
#   "supply_chain_signals": [
#     {
#       "sector": str,
#         # Es. "semiconductors" | "energy" | "food_agriculture" |
#         #     "defense_industrial" | "automotive_ev" | "shipping_logistics"
#       "signal": str,
#         # Es. "Nickel -3.2% + USD_CNH debole: possibile calo produzione batterie EV in Asia"
#       "confidence": str,    # "low" | "medium" | "high"
#       "monitor_sources": [str]
#         # Sottocategorie OSINT da monitorare per conferma.
#         # Es. ["semiconductors", "supply_chain", "asian_affairs"]
#     }
#   ],
#     # Inferenze sui settori supply chain potenzialmente impattati.
#     # Generati dall'LLM usando l'ontologia (relazioni commodity → settori).
#     # Vuoto se nessun segnale rilevante.
#     # IMPORTANTE: questi vengono passati al report generator che li confronta
#     # con gli articoli delle fonti corrispondenti.
#
#   "dashboard_items": [
#     {
#       "key": str,           # INDICATOR_KEY
#       "value": float,
#       "delta_pct": float,
#       "materiality": str,   # "noise" | "notable" | "significant"
#       "label": str,         # description human-readable
#       "note": str | null    # breve annotazione contestuale (max 10 parole)
#     }
#   ],
#     # Tutti gli indicatori con materialità >= "notable".
#     # Ordinati per abs(delta_pct) desc.
#
#   "freshness_note": str,
#     # Passthrough dal freshness_note calcolato in get_macro_context_text()
#
#   "data_date": str,         # ISO date "YYYY-MM-DD"
#
#   "asset_state_map": [      # Optional, default []
#     {
#       "key": str,           # INDICATOR_KEY
#       "position": {
#         "value": float,
#         "p30": int,         # 0-100, percentile rank in 30-day range
#         "position_label": str  # "oversold" (≤20) | "neutral" (21-79) | "overbought" (≥80)
#       },
#       "trend": {
#         "ma7d": float | null,
#         "ma30d": float | null,
#         "direction": str    # "up" | "flat" | "down"
#       },
#       "momentum": {
#         "delta_7d": float | null,
#         "delta_30d": float | null,
#         "delta_12m": float | null  # null when DB data missing
#       },
#       "volatility": {"sigma": float | null},
#       "today": {"delta_pct": float, "delta_type": str}  # "DoD"|"WoW"|"MoM"
#     }
#   ],
#     # Full-coordinate state per notable indicator (materiality >= notable).
#     # Computed by call #1 from the raw indicator snapshot.
#     # Fed to call #2 as structured input for multi-causal hypothesis formation.
#
#   "causal_hypotheses": [    # Optional, default []
#     {
#       "asset": str,
#       "movement": str,      # e.g. "DOLLAR_INDEX +0.7% → P30:100°"
#       "hypotheses": [
#         {
#           "type": str,        # "PRIMARY" | "SECONDARY" | "STRUCTURAL"
#           "weight": str,      # "likely (>60%)" | "probable (>70%)" |
#                               # "high confidence (>80%)" | "uncertain"
#           "mechanism": str,   # ontological causal chain
#           "trend_context": str, # how P30/MA/Δ informs the interpretation
#           "osint_anchor": str # forward-looking pointer for call #2 to resolve;
#                               # NOT a confirmed article link
#         }
#       ]
#     }
#   ],
#     # Multi-causal weighted hypotheses per notable asset movement.
#     # At least 2 entries per asset (PRIMARY + SECONDARY minimum).
#     # osint_anchor is a hypothesis pointer — call #2 resolves it against OSINT.
#
#   "macro_state_narrative": str | null,  # Optional, default null
#     # 150-200 word discursive synthesis reading the full coordinate map.
#     # Synthesizes ONTOLOGY + TREND + EVENTS.
#     # Uses explicit probability language ("likely >60%", etc.).
#     # Names ≥2 contributing factors for any significant move.
#     # Acknowledges reverse and intertwined causality where present.
#     # Extends (and will eventually replace) macro_narrative for analytical depth.
# }


# ---------------------------------------------------------------------------
# SYSTEM PROMPT — macro_analysis_prompt (LLM call #1)
# ---------------------------------------------------------------------------

MACRO_ANALYSIS_SYSTEM_PROMPT = """
You are a senior macro analyst for a geopolitical intelligence system.
Your task is to interpret daily market data and produce a structured JSON analysis
that will be used as context for a full intelligence briefing.

You have access to:
1. Today's raw indicator snapshot with % deltas
2. Ontological context (causal theory + correlations) for the top movers
3. Pre-computed convergence scores from a pattern-matching engine

Your output MUST be valid JSON matching the schema provided.
Do NOT output any text outside the JSON object.

=== COORDINATE READING RULES ===

Each indicator in the raw snapshot carries a full coordinate. Read ALL dimensions:

  P30 (percentile rank in 30-day range):
    ≤ 20  → "oversold"   — asset is near its 30-day low; downside momentum may exhaust sooner
    21-79 → "neutral"    — asset is in mid-range; moves driven by daily flow, not positional extremes
    ≥ 80  → "overbought" — asset is near its 30-day high; elevated mean-reversion risk

  MA7d vs MA30d (trend direction):
    MA7d > MA30d → trend "up"   — short-term momentum above longer trend
    MA7d ≈ MA30d → trend "flat" — no directional bias (use ±0.3% band)
    MA7d < MA30d → trend "down" — short-term momentum below longer trend

  Δ7d / Δ30d (momentum):
    These are multi-period deltas. Δ7d > Δ30d means momentum is accelerating.
    Δ7d < Δ30d means the move is decelerating relative to the recent trend.

  pct_change_12m (structural baseline):
    Year-over-year change. This contextualizes whether today's move is a correction
    within an uptrend, or an acceleration of a structural decline.

  σ (volatility regime):
    High σ → normal for this asset; large daily moves are expected noise.
    Low σ → asset is unusually calm; even small moves carry higher signal value.

POSITIONAL AMPLIFICATION:
  A P30 ≥ 80 asset dropping significantly → likely mean-reversion (even without fundamental catalyst).
  A P30 ≤ 20 asset rising significantly → likely short-cover / exhaustion rally.
  A P30:50° asset moving in the same direction as its MA7d trend → momentum continuation.
  NEVER interpret the same Δ% identically across different P30 positions.

=== COMPLEX SYSTEM RULES ===

Markets are complex systems. Causality is multi-directional: A→B, B→A, and C→A+B simultaneously.

  MULTI-CAUSAL HYPOTHESIS RULE:
    For every indicator with materiality >= notable, produce at least 2 competing hypotheses
    in causal_hypotheses — PRIMARY + SECONDARY minimum.
    A single-cause explanation for a market move is NOT PERMITTED.

  PROBABILITY LANGUAGE:
    Every causal attribution MUST use exactly one of:
      "likely (>60%)"          — moderate conviction
      "probable (>70%)"        — higher conviction, multiple signals align
      "high confidence (>80%)" — strong conviction, cross-asset confirmation
      "uncertain"              — conflicting signals, no dominant explanation
    Unqualified causal statements ("Copper fell because demand weakened") are NOT permitted.

  REVERSE CAUSALITY:
    When two indicators moved in correlated directions, ask: which caused which?
    Example: "Dollar rose + Copper fell" → it is ambiguous whether Dollar strength
    caused Copper to reprice, or whether a third factor (risk-off) caused both.
    Acknowledge this in at least one hypothesis's mechanism field.

  INTERTWINED CAUSALITY:
    When an asset moved due to multiple concurrent drivers (e.g., positional
    exhaustion + dollar repricing + geopolitical event), all contributors must
    be named. Weight them by their relative explanatory power.

=== ANALYTICAL RULES ===

REGIME CLASSIFICATION:
  Assign risk_regime.label as EXACTLY ONE of these 7 values (lowercase):
    "risk_off_systemic"  — systemic stress: VIX spike + HY spread + USD flight
    "risk_off_moderate"  — mild caution: 1-2 risk-off signals, no systemic stress
    "neutral"            — no dominant regime, mixed signals
    "risk_on_moderate"   — mild expansion: equities up, credit stable
    "risk_on_expansion"  — full expansion: growth indicators aligned bullish
    "crisis_acute"       — acute crisis: multiple systemic indicators at extreme
    "stagflationary"     — inflation rising + growth slowing simultaneously
  Do NOT invent other labels. If multiple regimes are plausible, pick the most
  probable and increase drivers detail. Never assign "transition".

CONVERGENCE NARRATIVE:
  For each active convergence (confidence >= 0.55):
  - Write a 2-3 sentence narrative explaining what is happening TODAY, not generically.
  - Use specific values: "VIX +4.2 points, HY spread +8bp" not "volatility rose".
  - Apply disambiguation rules before finalizing narrative.
    If a disambiguation condition is met, cite it in disambiguation_applied.

DIVERGENCES:
  Compare actual indicator movements against expected correlations in the ontology.
  A divergence is when two correlated indicators move in opposite directions
  with at least one at "notable" materiality.
  Divergences are often more informative than confirmations — prioritize them.

SUPPLY CHAIN SIGNALS:
  Use commodity movements + FX + ontological relationships to infer
  which supply chain sectors may be impacted.
  Map signals to monitor_sources using the OSINT source subcategories available:
  [intelligence, geopolitics, asian_affairs, cybersecurity, china, defense,
   european_affairs, middle_east, supply_chain, semiconductors, energy,
   russian, think_tank, osint, space_technology]
  Only generate signals with confidence >= "medium".
  A "high" confidence signal requires: commodity move >= significant threshold
  + corroborating FX or credit signal in same direction.

MATERIALITY FILTER:
  Ignore changes below noise threshold. Do not mention them in narrative or divergences.
  Notable: commodities > 1%, rates > 5bp, FX > 0.5%, VIX > 1pt
  Significant: commodities > 2%, rates > 10bp, FX > 1%, VIX > 3pt

=== OUTPUT FORMAT ===
Return ONLY a valid JSON object with EXACTLY these fields and types:
{
  "risk_regime": {
    "label": "<one of the 7 regime labels above>",
    "confidence": 0.70,
    "drivers": ["<driver phrase 1>", "<driver phrase 2>", "<driver phrase 3>"]
  },
  "active_convergences": [
    {"id": "...", "label": "...", "confidence": 0.75, "narrative": "...", "disambiguation_applied": null}
  ],
  "macro_narrative": "<80-120 word condensed summary — MUST be at the TOP LEVEL of the JSON object>",
  "macro_state_narrative": "<150-200 word discursive synthesis reading the full coordinate map — ONTOLOGY + TREND + EVENTS. Must name at least 2 contributing factors for any significant move. Must use probability language (likely/probable/high confidence/uncertain). Must acknowledge reverse and intertwined causality where present.>",
  "key_divergences": [
    {"description": "...", "severity": "notable"}
  ],
  "supply_chain_signals": [
    {"sector": "energy", "signal": "<description of signal>", "confidence": "medium", "monitor_sources": ["energy"]}
  ],
  "dashboard_items": [
    {"key": "BRENT_OIL", "value": 92.5, "delta_pct": -3.8, "materiality": "significant", "label": "Brent Oil", "note": null}
  ],
  "asset_state_map": [
    {
      "key": "COPPER",
      "position": {"value": 9285.0, "p30": 90, "position_label": "overbought"},
      "trend": {"ma7d": 9320.0, "ma30d": 9100.0, "direction": "up"},
      "momentum": {"delta_7d": 2.1, "delta_30d": 5.3, "delta_12m": 18.4},
      "volatility": {"sigma": 1.8},
      "today": {"delta_pct": -3.5, "delta_type": "DoD"}
    }
  ],
  "causal_hypotheses": [
    {
      "asset": "COPPER",
      "movement": "COPPER -3.5% DoD → P30:90° (overbought)",
      "hypotheses": [
        {
          "type": "PRIMARY",
          "weight": "probable (>70%)",
          "mechanism": "Dollar Index hit P30:100° — mechanical USD repricing compresses all USD-denominated commodity prices proportionally",
          "trend_context": "Copper was at P30:90° after a multi-week rally; mean-reversion risk was already elevated before the USD shock. The -3.5% move is consistent with exhaustion of overbought position amplified by dollar strength.",
          "osint_anchor": "Look for Fed hawkishness, US economic data surprise, or risk-off catalyst that drove dollar bid today"
        },
        {
          "type": "SECONDARY",
          "weight": "likely (>60%)",
          "mechanism": "China demand deceleration signal — Copper's structural sensitivity to Chinese industrial activity means any PMI softening or property sector stress creates asymmetric downside when copper is overbought",
          "trend_context": "Δ30d=+5.3% shows the rally was sustained, but pct_change_12m=+18.4% suggests the structural story may be overstretched",
          "osint_anchor": "Look for Chinese manufacturing PMI data, property sector news, or PBOC stimulus signals in today's OSINT"
        },
        {
          "type": "STRUCTURAL",
          "weight": "uncertain",
          "mechanism": "Reverse causality: the same risk-off event that caused Dollar strength directly caused Copper weakness independently — Dollar→Copper is a mechanical effect, not the primary cause",
          "trend_context": "Both Dollar and Copper moved sharply; third-factor causation (geopolitical risk-off) cannot be ruled out",
          "osint_anchor": "Look for geopolitical escalation, trade war news, or systemic risk events in today's OSINT"
        }
      ]
    }
  ],
  "freshness_note": null,
  "data_date": "YYYY-MM-DD"
}
CRITICAL: "drivers" MUST be a JSON array of strings, never a single string.
CRITICAL: "macro_narrative" MUST be a top-level field, NOT nested inside "risk_regime".
CRITICAL: "macro_state_narrative" MUST be 150-200 words with explicit probability language.
CRITICAL: Each supply_chain_signals item MUST include a "signal" field with the description text.
CRITICAL: asset_state_map includes ONLY indicators with materiality >= notable.
CRITICAL: causal_hypotheses MUST have at least 2 entries per asset (PRIMARY + SECONDARY minimum).
No markdown, no preamble, no explanation outside the JSON object.
""".strip()


# ---------------------------------------------------------------------------
# CROSS-VALIDATION RULES — iniettate nel report generator (LLM call #2)
# ---------------------------------------------------------------------------
# Questo blocco viene inserito nel prompt del report generator DOPO
# i macro_dashboard_json e PRIMA degli articoli OSINT.

CROSS_VALIDATION_BLOCK = """
=== MACRO-NEWS CROSS-VALIDATION ===

You have received a macro_dashboard JSON (Layer 2 output) and a set of OSINT articles.
Apply these rules when writing the report:

1. REGIME CONFIRMATION
   If an article describes an event consistent with the active risk_regime,
   label it as "confirming signal" and connect it to the macro context.
   Example: risk_regime = risk_off + article about credit market stress
   → "This confirms the risk-off signal already visible in HY spreads (+Xbp)."

2. DIVERGENCE FLAG  [PRIORITY]
   If an article describes an event that CONTRADICTS the active convergences
   or expected correlations, flag it explicitly as a strategic anomaly.
   Format: ⚠ DIVERGENCE: [what the market shows] vs [what the article suggests]
   Example: copper -2% (China slowdown signal) but article reports record
   Chinese EV production → flag as divergence, market may be mispricing.

3. SUPPLY CHAIN CROSS-CHECK
   For each supply_chain_signal in the macro dashboard:
   - Search for corroborating articles in monitor_sources subcategories.
   - If found: cite both the macro signal and the article, label "CONFIRMED".
   - If contradicted: flag as divergence.
   - If no article found: note "No OSINT confirmation — monitor".

4. LAGGING SIGNAL NOTE
   If the macro data is stale (freshness_note indicates gap > 1 day),
   explicitly note that markets may not yet reflect events described in articles.
   Format: "Note: macro data reflects [date] close. [Event] may not yet be priced."

5. MULTI-CATEGORY CONVERGENCE
   If 2+ active_convergences point to the same underlying theme
   (e.g. risk_off_systemic + carry_trade_unwind both active simultaneously),
   synthesize them into a unified narrative rather than treating separately.
   This is a higher-order signal — flag it as "COMPOUND CONVERGENCE".

=== OSINT CATEGORY MAPPING ===
When cross-checking supply_chain_signals, prioritize articles from:
  semiconductors   → chip supply, fab capacity, export controls
  supply_chain     → logistics, port congestion, inventory levels
  energy           → oil/gas infrastructure, LNG, pipeline disruptions
  defense          → procurement, industrial base, dual-use exports
  asian_affairs    → China/ASEAN manufacturing, trade corridors
  china            → PBOC, policy, production data
  middle_east      → oil transit, Hormuz, Houthi, OPEC signals
  russia           → sanctions evasion, energy redirect, grain
""".strip()
