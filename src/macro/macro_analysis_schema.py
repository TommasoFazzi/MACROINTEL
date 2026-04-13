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
#   "data_date": str          # ISO date "YYYY-MM-DD"
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
Return ONLY a JSON object. No markdown, no preamble, no explanation outside JSON.
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
