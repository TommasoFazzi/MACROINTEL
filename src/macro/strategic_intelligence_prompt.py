# =============================================================================
# STRATEGIC INTELLIGENCE LAYER — Prompt Design (LLM call #2)
# =============================================================================
#
# Sostituisce il report generator attuale.
# Input: macro_analysis JSON + regime history + storylines + OSINT + rules
# Output: report strutturato in 7 sezioni (Markdown)
# Modello: Gemini 2.5 Flash
# Stima token input: ~7.800 | output: ~3.000
# =============================================================================


# =============================================================================
# SYSTEM PROMPT
# =============================================================================

STRATEGIC_INTELLIGENCE_SYSTEM_PROMPT = """
You are a senior strategic intelligence analyst for a proprietary geopolitical
and macroeconomic intelligence system. Your audience ranges from the system owner
(internal use) to professional clients seeking strategic insight.

Your role is NOT to summarize news. Your role is to INTERPRET, CONNECT, and
ANTICIPATE — identifying what is changing before it becomes mainstream,
where structural vulnerabilities lie, and what scenarios are plausible
given the current configuration of macro forces and geopolitical dynamics.

You think in three time horizons simultaneously:
  SHORT  (1-4 weeks):  What is changing NOW that markets haven't priced yet?
  MEDIUM (1-6 months): Where are the structural exposures and opportunities?
  LONG   (3-12 months): What scenarios are plausible, and what confirms or denies them?

You have access to:
  1. A macro analysis JSON with today's market regime, active convergences,
     key divergences, and supply chain signals — pre-computed from 33 indicators.
  2. A 60-day macro regime history showing how the regime has evolved.
  3. The top active storylines from a narrative clustering engine with momentum scores.
  4. Today's OSINT articles from 40+ curated sources across geopolitics,
     defense, cybersecurity, energy, supply chain, think tanks, and regional coverage.

ANALYTICAL STANDARDS:
  - Precision over breadth: 3 sharp insights beat 10 vague ones.
  - Every claim needs a basis: macro data, OSINT article, or historical pattern.
  - Distinguish CONFIRMED signals (macro + OSINT agree) from
    HYPOTHESES (single source, no cross-validation).
  - Flag divergences explicitly — they are often more valuable than confirmations.
  - Avoid hedging that conveys no information ("may", "could possibly").
    Use instead: "likely" (>60%), "probable" (>70%), "high confidence" (>80%)
    with explicit basis.
  - If today is genuinely low-signal: say so. Do not pad with generic analysis.
""".strip()


# =============================================================================
# CROSS-VALIDATION BLOCK
# Iniettato nel prompt prima delle output instructions.
# =============================================================================

CROSS_VALIDATION_BLOCK = """
=== MACRO-NEWS CROSS-VALIDATION RULES ===
Apply these rules before writing any section.

1. REGIME CONFIRMATION
   If an article confirms the active risk_regime or active convergences:
   Connect it explicitly to the macro data with [Article N] citation.
   Example: "risk_off_systemic active + HY spreads +8bp confirmed by [Article N]"

2. DIVERGENCE FLAG [CHECK THIS FIRST — highest priority]
   If an article CONTRADICTS active convergences or expected correlations:
   Flag as: "DIVERGENCE: [market signal] vs [OSINT evidence from Article N]"
   Divergences are often more strategically valuable than confirmations.

3. SUPPLY CHAIN CROSS-CHECK
   For each supply_chain_signal:
     - Article found in relevant subcategory: cite [Article N], label CONFIRMED
     - Article contradicts: flag as divergence
     - No article found: label "No OSINT confirmation — monitor [subcategories]"
   Never assert SC signal confirmed without a supporting article.

4. LAGGING SIGNAL NOTE
   If macro data is stale (gap > 1 day in freshness_note):
   Add inline: "Note: [INDICATOR] data from [date] — event may not be priced yet."

5. COMPOUND CONVERGENCE
   If 2+ active convergences point to the same theme simultaneously:
   Synthesize into unified narrative. Label: "COMPOUND CONVERGENCE: [theme]"
   Treat with elevated weight — this is a higher-order signal.

6. DATA QUALITY CAVEAT (inline, not grouped)
   If a signal is from a stale or restricted indicator:
   Add caveat where the signal appears, not at the end.
   Example: "NICKEL signals EV battery cost pressure [STRUCTURAL CONTEXT:
   data from Feb 2026 — verify with current LME pricing]"
""".strip()


# =============================================================================
# OUTPUT INSTRUCTIONS
# =============================================================================

def build_output_instructions(target_date: str) -> str:
    return f"""
=== OUTPUT INSTRUCTIONS ===
Date: {target_date}

Produce a structured intelligence report with EXACTLY these 7 sections,
in this order. Use ## for section headers, ### for subsections.
Total length: 1,200-2,000 words. Never pad to meet length targets.

---

## Executive Summary
3-5 sentences, prose only, no bullets.
Most important development today + strategic implication.
State the current risk regime and whether it differs from recent days.

---

## Key Developments
Max 5 developments. Each as a short paragraph (3-4 sentences).
Format:
  **[CATEGORY | REGION]** Title
  What happened + what it means + macro connection + [Article N]
Skip routine news. Only include items with strategic significance.

---

## Macro Dashboard
Current macro configuration in compact format.
Risk regime (with streak days) + active convergences + key divergences.
Notable market movements as table:
  | Indicator | Value | Delta | Signal |
Only indicators with materiality >= notable.
End with 2-sentence interpretation of the overall configuration.

---

## Early Warning Signals
Time horizon: 1-4 WEEKS ONLY.
Identify what is changing BEFORE it becomes mainstream.
Sources: macro-OSINT divergences, nascent convergences (confidence 0.4-0.6),
storylines with accelerating momentum, SC signals not yet priced.

Format (max 4 signals):
  SIGNAL: [Name]
  Basis: [macro data | OSINT source | both]
  Watch for: [specific trigger to confirm or deny]
  Time: [days/weeks before resolution expected]

If fewer than 2 credible signals exist: say so. Never manufacture signals.

---

## Strategic Positioning
Time horizon: 1-6 MONTHS ONLY.

### By Sector
For each sector with active SC signals: exposure, direction, confidence level.

### By Geography
Max 3 regions with meaningful macro-geopolitical developments today.

### Chokepoints
Critical dependencies or single points of failure identified today.
Only include if genuinely identified — do not speculate.

---

## Scenario Analysis
Time horizon: 3-12 MONTHS ONLY.
Produce EXACTLY 2-3 scenarios. No more — quality over quantity.

Format per scenario:
  ### Scenario [N]: [Name] — [BASE | BEAR | BULL | TAIL RISK]
  Probability: [Low <20% | Medium 20-50% | High >50%]
  Thesis: 2-3 sentences on the core narrative.
  Assumes: what must remain true for this to play out.
  Confirmed by: specific observable event that raises probability.
  Denied by: specific observable event that invalidates this scenario.
  Exposed sectors: which sectors/geographies are most affected, and how.

Requirements:
  - Scenarios must be mutually distinguishable (different triggers/outcomes)
  - Grounded in current regime + active convergences + storylines
  - Triggers must be specific and observable, not vague

---

## Supply Chain Monitor
Operational format. Only signals with confidence_final >= medium AND fresh data.
Monthly indicator signals (NICKEL): label [STRUCTURAL CONTEXT], not fresh signals.

Format per signal:
  [SECTOR] [confidence] | lag: [timeframe]
  Signal: [what macro data shows]
  OSINT: [Article N if found] | "No OSINT confirmation — monitor [subcategories]"
  Watch: [what to monitor for confirmation]

---

## Strategic Storyline Tracker
Top active storylines from Narrative Engine. Max 6.

Format per storyline:
  [STORYLINE TITLE] | Momentum: [score] | Active: [X days]
  Status: 1-2 sentences on latest development.
  Macro connection: how today's regime/convergences affect this storyline.
  [Article N] if today's OSINT adds new information.

---

FORMATTING RULES:
- No filler phrases ("it is worth noting", "as we can see", "importantly")
- Each section adds information not repeated elsewhere
- [Article N] required for every specific factual claim from OSINT
- Data quality caveats inline, not grouped
- Low-signal days: shorter report is correct, do not pad
""".strip()


# =============================================================================
# PROMPT ASSEMBLER
# =============================================================================

def build_strategic_intelligence_prompt(
    macro_analysis_json: dict,
    macro_regime_context_xml: str,
    storylines_xml: str,
    articles: list,
    target_date: str,
    data_quality_flags: list,
) -> tuple[str, str]:
    """
    Assembla il prompt completo per la LLM call #2.

    Returns (system_prompt: str, user_prompt: str)
    Il system_prompt va nel campo 'system' della API call.
    Lo user_prompt va nel campo 'user'.

    Ordine deliberato nel user_prompt:
      [1] Data quality caveat    — orienta il frame critico
      [2] Regime history 60gg    — contesto strutturale
      [3] Macro analysis oggi    — segnali del giorno
      [4] Storylines             — narrative in evoluzione
      [5] OSINT articles         — fonti primarie
      [6] Cross-validation rules — come incrociare tutto
      [7] Output instructions    — cosa produrre

    Questo ordine massimizza la coerenza del ragionamento LLM:
    prima il frame e i limiti, poi i dati, poi le istruzioni.
    """

    user_sections = []

    # [1] Data quality
    user_sections.append(_build_data_quality_section(
        macro_analysis_json, data_quality_flags
    ))

    # [2] Regime history
    user_sections.append(
        "=== MACRO REGIME CONTEXT (60-day history) ===\n" +
        macro_regime_context_xml
    )

    # [3] Macro analysis today
    user_sections.append(_build_macro_analysis_section(macro_analysis_json))

    # [4] Storylines
    user_sections.append(
        "=== ACTIVE NARRATIVE STORYLINES (top 10 by momentum) ===\n" +
        storylines_xml
    )

    # [5] OSINT articles
    user_sections.append(_build_articles_section(articles))

    # [6] Cross-validation rules
    user_sections.append(CROSS_VALIDATION_BLOCK)

    # [7] Output instructions
    user_sections.append(build_output_instructions(target_date))

    user_prompt = "\n\n".join(user_sections)

    return STRATEGIC_INTELLIGENCE_SYSTEM_PROMPT, user_prompt


# =============================================================================
# SECTION BUILDERS (helpers)
# =============================================================================

def _build_data_quality_section(
    macro_analysis_json: dict,
    data_quality_flags: list,
) -> str:
    lines = ["=== DATA QUALITY CONTEXT ==="]

    if not data_quality_flags:
        lines.append(
            "All macro indicators fresh and reliable. "
            "No data quality issues today."
        )
    else:
        lines.append("Data quality issues affecting today's analysis:")
        for flag in data_quality_flags:
            lines.append(f"  {flag}")

    lines.append("")
    lines.append("Permanent reliability flags (apply always):")
    lines.append(
        "  USD_CNH: restricted — PBoC fixing distorts offshore rate. "
        "Use CNH-CNY spread only, not absolute level."
    )
    lines.append(
        "  NICKEL: monthly FRED (~2 month lag). "
        "SC signals are structural context, not fresh triggers."
    )

    if macro_analysis_json.get("confidence_degraded_by_staleness"):
        lines.append("")
        lines.append(
            "CONFIDENCE DEGRADED: active convergences had stale trigger indicators. "
            "Weight convergence signals proportionally less."
        )

    return "\n".join(lines)


def _build_macro_analysis_section(macro_analysis_json: dict) -> str:
    lines = ["=== TODAY'S MACRO ANALYSIS ==="]

    # Regime
    regime = macro_analysis_json.get("risk_regime", {})
    conf = regime.get("confidence", 0)
    lines.append(f"\nRISK REGIME: {regime.get('label', 'unknown').upper()} ({conf:.0%} confidence)")
    for d in regime.get("drivers", []):
        lines.append(f"  • {d}")

    # Active convergences
    convergences = macro_analysis_json.get("active_convergences", [])
    if convergences:
        lines.append(f"\nACTIVE CONVERGENCES ({len(convergences)}):")
        for c in convergences:
            pct = f"{c.get('confidence', 0):.0%}"
            lines.append(f"  [{pct}] {c.get('label', c.get('id', ''))}")
            if c.get("narrative"):
                lines.append(f"    {c['narrative']}")
            if c.get("disambiguation_applied"):
                lines.append(f"    Disambiguation applied: {c['disambiguation_applied']}")
    else:
        lines.append("\nACTIVE CONVERGENCES: None above threshold today.")

    # Divergences
    divergences = macro_analysis_json.get("key_divergences", [])
    if divergences:
        lines.append(f"\nKEY DIVERGENCES:")
        for d in divergences:
            sev = d.get("severity", "notable").upper()
            lines.append(f"  [{sev}] {d.get('description', '')}")

    # SC signals
    sc = macro_analysis_json.get("supply_chain_signals", [])
    if sc:
        lines.append(f"\nSUPPLY CHAIN SIGNALS:")
        for s in sc:
            conf_f = s.get("confidence_final", s.get("confidence", "?"))
            lag = s.get("lag", "?")
            sector = s.get("sector", "").upper()
            lines.append(f"  [{conf_f.upper()} | {lag}] {sector}")
            lines.append(f"    {s.get('signal', '')}")
            sources = s.get("monitor_sources", [])
            if sources:
                lines.append(f"    Monitor: {', '.join(sources)}")
    else:
        lines.append("\nSUPPLY CHAIN SIGNALS: None above threshold today.")

    # Macro narrative (da call #1)
    narrative = macro_analysis_json.get("macro_narrative", "")
    if narrative:
        lines.append(f"\nMACRO NARRATIVE:\n{narrative}")

    # Top movers
    dashboard = [
        d for d in macro_analysis_json.get("dashboard_items", [])
        if d.get("materiality") in ("notable", "significant")
    ]
    if dashboard:
        lines.append("\nNOTABLE MOVEMENTS:")
        lines.append("  Indicator                  Value      Delta    Signal")
        for item in dashboard[:8]:
            delta = item.get("delta_pct", 0)
            sign = "+" if delta > 0 else ""
            mat = item.get("materiality", "").upper()
            note = f" {item['note']}" if item.get("note") else ""
            lines.append(
                f"  {item.get('key', ''):25}"
                f"  {str(item.get('value', '')):10}"
                f"  {sign}{delta:.2f}%   [{mat}]{note}"
            )

    lines.append(f"\nFreshness: {macro_analysis_json.get('freshness_note', 'N/A')}")
    return "\n".join(lines)


# Maximum articles included in the LLM prompt context.
# Matches the "top 10 by relevance" stated in the system prompt and output instructions.
# Increase with caution — each article adds ~300-500 tokens; 10 keeps total input ~7,800 tokens.
_MAX_ARTICLES_IN_PROMPT: int = 10


def _build_articles_section(articles: list) -> str:
    lines = [
        f"=== TODAY'S OSINT ARTICLES (top {_MAX_ARTICLES_IN_PROMPT} by relevance) ===",
        "Cite as [Article N] for every specific factual claim.\n",
    ]
    for i, article in enumerate(articles[:_MAX_ARTICLES_IN_PROMPT], 1):
        lines.append(f"[Article {i}]")
        lines.append(f"  Title:    {article.get('title', 'N/A')}")
        lines.append(f"  Source:   {article.get('source', 'N/A')}")
        cat = article.get('category', 'N/A')
        sub = article.get('subcategory', 'N/A')
        lines.append(f"  Category: {cat} / {sub}")
        lines.append(f"  Date:     {article.get('date', 'N/A')}")
        summary = article.get("summary", article.get("content", ""))[:500]
        if summary:
            lines.append(f"  Summary:  {summary}")
        lines.append("")
    return "\n".join(lines)
