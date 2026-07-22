/**
 * SCENE 3 — ASK ANYTHING — shared constants.
 *
 * Tool names and SOP paths are copied verbatim from `src/llm/oracle_orchestrator.py`
 * (`_register_tools` for the 9 tool `name` attributes, `_build_system_prompt` for the
 * 9 SOP paths and their `time_decay_k`). See design.md's stated risk: a scripted demo
 * that shows a tool path Oracle would never take is worse than the old fake typewriter,
 * so nothing here may drift from the orchestrator's real routing.
 *
 * DEMO_TRACE below is not pulled from a logged `oracle_query_log` row (no DB/API access
 * in this environment) — it's a manual derivation: the query is run mentally through the
 * real PATH OVERVIEW SOP text (rag_search, mode=both/vector, top_k=15 → graph_navigation,
 * time_decay_k=0.005) exactly as written in the orchestrator, so the tool sequence and
 * rationale are what Oracle's own routing rules dictate for this query, not an invented
 * one. Swap in a real `oracle_query_log` trace when one is available.
 *
 * Deliberately a macro/geopolitical query, not a trading one — MARKET/TICKER paths exist
 * and are real, but the showcase query shouldn't make the platform read as a stock-signal
 * tool front and center. Topic carries over from the corridor question the old DemoOracle
 * already shipped (Iran/Pakistan/Afghanistan trade routes), just run through the real SOP.
 */

export type OracleToolId =
  | 'rag_search'
  | 'sql_query'
  | 'aggregation'
  | 'graph_navigation'
  | 'market_analysis'
  | 'ticker_themes'
  | 'report_compare'
  | 'reference_lookup'
  | 'spatial_query';

export type OracleTool = {
  id: OracleToolId;
  label: string;
  description: string;
};

// Order matches _register_tools in oracle_orchestrator.py.
export const ORACLE_TOOLS: OracleTool[] = [
  { id: 'rag_search', label: 'RAG Search', description: 'Semantic + full-text retrieval over the article vector store' },
  { id: 'sql_query', label: 'SQL Query', description: 'Guarded read-only SQL against the structured schema' },
  { id: 'aggregation', label: 'Aggregation', description: 'Pre-built trend, top-N and distribution statistics' },
  { id: 'graph_navigation', label: 'Graph Navigation', description: 'Traverses the storyline graph and its clusters' },
  { id: 'market_analysis', label: 'Market Analysis', description: 'Live BULLISH/BEARISH/WATCHLIST trade signals' },
  { id: 'ticker_themes', label: 'Ticker Themes', description: 'Market themes and narratives tied to a specific ticker' },
  { id: 'report_compare', label: 'Report Compare', description: 'Diffs two dated reports for what changed' },
  { id: 'reference_lookup', label: 'Reference Lookup', description: 'Country profiles, sanctions, IMF forecasts, trade flows' },
  { id: 'spatial_query', label: 'Spatial Query', description: 'Geospatial radius search over conflict events and assets' },
];

export type OracleSopPath =
  | 'FACTUAL'
  | 'ANALYTICAL'
  | 'OVERVIEW'
  | 'MARKET'
  | 'REFERENCE'
  | 'NARRATIVE'
  | 'TICKER'
  | 'SPATIAL'
  | 'COMPARATIVE';

// timeDecayK is null where the orchestrator's SOP doesn't state one — ANALYTICAL and
// SPATIAL lead with sql_query/spatial_query, not rag_search, so no decay constant applies.
export const ORACLE_SOP_PATHS: Record<OracleSopPath, { timeDecayK: number | null; summary: string }> = {
  FACTUAL: { timeDecayK: 0.03, summary: 'Recent news, events, statements' },
  ANALYTICAL: { timeDecayK: null, summary: 'Counts, trends, distributions' },
  OVERVIEW: { timeDecayK: 0.005, summary: 'Country-level panorama' },
  MARKET: { timeDecayK: 0.04, summary: 'Trading signals, macro, opportunities' },
  REFERENCE: { timeDecayK: 0.001, summary: 'Country profiles, sanctions, forecasts' },
  NARRATIVE: { timeDecayK: 0.02, summary: 'Storyline evolution and connections' },
  TICKER: { timeDecayK: 0.03, summary: 'Ticker-level market themes' },
  SPATIAL: { timeDecayK: null, summary: 'Geospatial radius search' },
  COMPARATIVE: { timeDecayK: 0.015, summary: 'Entity/period comparison' },
};

export type DemoStep = {
  tool: OracleToolId;
  rationale: string;
};

// Deliberately just [N] + type — no title, publisher or date. The source registry in the
// demo is a template of *how* citations get visualized, not a claim that these specific
// sources exist; real title/publisher text here would read as fabricated reporting.
export type DemoSource = {
  n: number;
  type: 'REPORT' | 'ARTICLE';
};

export type DemoDocument = {
  executiveSummary: string;
  detailedAnalysis: string;
  strategicImplications: string;
};

export const DEMO_TRACE: {
  query: string;
  path: OracleSopPath;
  steps: DemoStep[];
  document: DemoDocument;
  sources: DemoSource[];
} = {
  // Deliberately about mechanism, not current status: "how does a disruption at X behave
  // differently than one at Y" is evergreen (geography and cause-and-effect don't go
  // stale or need a source date), whereas a "current state as of [date]" claim would need
  // real reporting behind every number and goes wrong the moment it's outdated. An earlier
  // draft asserted specific current-events figures and got a real fact-check wrong in the
  // process (claimed Hormuz-closed cargo "reroutes around the Cape of Good Hope" — the
  // Cape route bypasses Suez/the Red Sea, not Hormuz, which is the Gulf's only entrance;
  // if Hormuz is closed there is no reroute into the Gulf at all). Rewritten to avoid
  // asserting anything that needs a live source to still be true.
  query:
    'What are the key chokepoints in the Iran–Pakistan–Afghanistan trade corridor, and how does a disruption at the Strait of Hormuz affect it differently than a closure at the Pakistan–Afghanistan border?',
  path: 'OVERVIEW',
  steps: [
    {
      tool: 'rag_search',
      rationale:
        'A structural, region-wide question — retrieve broad context with vector search (avoids AND-matching on a multi-term query) and minimal time decay so the geographic baseline isn\'t dropped.',
    },
    {
      tool: 'graph_navigation',
      rationale:
        'Once the relevant context is anchored, traverse the storyline graph to see how the chokepoint and corridor threads connect.',
    },
  ],
  document: {
    executiveSummary:
      'The corridor has two structurally different failure points [1]: a maritime chokepoint at the Strait of Hormuz, and an overland chokepoint at the Pakistan–Afghanistan border. A disruption at either strains the corridor in a different way [2].',
    detailedAnalysis:
      'A Hormuz disruption cuts the corridor\'s only maritime leg — the region has no practical maritime bypass of its own, so affected cargo has nowhere reliable to go [3]. A border closure instead removes one overland crossing while other overland paths stay technically open, just undersized for the redirected volume — the effect shows up as congestion and cost, not a hard stop [4]. The two compound when they coincide: overland routes absorb volume they were never sized for while the maritime option stays shut [2].',
    strategicImplications:
      'Because the two legs fail independently, they can also ease independently: relieving the maritime chokepoint helps regardless of the border\'s status, and vice versa [5]. Whichever eases first determines which alternate routes stay under strain the longest — not just whether both eventually resolve.',
  },
  sources: [
    { n: 1, type: 'ARTICLE' },
    { n: 2, type: 'REPORT' },
    { n: 3, type: 'ARTICLE' },
    { n: 4, type: 'ARTICLE' },
    { n: 5, type: 'REPORT' },
  ],
};
