# Web Platform Context

## Purpose
Modern Next.js/React frontend for interactive intelligence visualization. Provides a tactical intelligence map (Mapbox), a **narrative storyline graph** (force-directed), a dashboard with reports, and a landing page. Consumes data from the FastAPI backend.

## Architecture Role
Advanced visualization layer consuming data from `src/api/` REST endpoints. Provides interactive exploration of geopolitical entities (map), **narrative storyline network** (graph), and intelligence reports (dashboard). Separate from the Streamlit HITL dashboard.

## Key Files

### App Structure
- `app/layout.tsx` - Root Next.js layout (Google Analytics gated by Consent Mode v2, GSC verification, renders `<CookieConsent />`)
- `app/globals.css` - Global styles with animations
- `app/page.tsx` - Landing page (Navbar, Hero+Scena 2, Ticker, Scena 1, Synthesis, Scena 3, Personas, Capabilities, **FAQ**, FinalCTA, Footer). RSC: fa i due fetch live (`getLiveGraphData`, `getLiveBriefing`) e passa i dati a Hero/Ticker/Synthesis/Scena 1. Exports canonical metadata + 4 JSON-LD blocks (SoftwareApplication, Organization, WebSite, FAQPage — derived from `FAQS` constant) via inline `<script>` from `lib/landing/schema.ts`. Reference design: `Landing Page.html` alla root del repo.
- `app/about/page.tsx` - **About page (`/about`)**: RSC, full metadata + JSON-LD `AboutPage`, usa `<Navbar solid />` esistente. Sezioni: `AboutHero`, `MissionVision`, `WhoItsFor` (6 persona dedicate diverse dalla landing), `Coverage` (8 topic 4×2), `AboutCTA`, `AboutFooter`. Reference design: `about.html` alla root del repo.
- `app/map/page.tsx` - **DISABLED (2026-07-17)**: renders a full-page "Work in Progress" banner (MacroIntel tactical style, `robots: noindex`) instead of the map — section is stale and not maintained. `components/IntelligenceMap/` is untouched; to restore, revert this file to the previous Suspense + MapLoader version. Links to `/map` (dashboard, landing footer/CTA, StorylineDossier deep-links) intentionally left in place — they land on the banner.
- `app/dashboard/page.tsx` - Dashboard route (SWR data fetching)
- `app/dashboard/report/[id]/page.tsx` - **Report detail route (updated with comparison UI)**
- `app/access/page.tsx` - Legacy route — redirects to `/dashboard` (platform is now fully public).
- `app/insights/page.tsx` - **Public intelligence briefings list**: fetches from `/api/v1/insights`, renders briefings with category badges and summaries. No auth required — public SEO page.
- `app/insights/[slug]/page.tsx` - **Briefing detail**: renders full executive summary. No auth required.
- `app/romania/page.tsx` - **Romania Intelligence vertical** (`/romania`): public page with macro dashboard (5 indicator cards with sparklines) + briefing list (Daily/Weekly tab). Server component layout + client SWR components. No auth.
- `app/romania/[id]/page.tsx` - **Single Romania briefing** (`/romania/{id}`): server-side fetch + full content render as pre-formatted text. Shows macro header summary bar when available. No auth.
- `middleware.ts` - **Sets a per-request nonce CSP** on every non-static route (matcher excludes `_next/static`, `_next/image`, and static asset extensions). No authentication: the platform is fully public and no route is gated. Emits `Content-Security-Policy` + `x-nonce`; `script-src` needs `'unsafe-eval'` for mapbox-gl's WebGL shader compilation and sigma/graphology-layout-forceatlas2. `frame-ancestors` and `form-action` are set explicitly because they do not inherit from `default-src`. **Note:** the nonce makes every matched route dynamic (`cache-control: private, no-cache, no-store`), so the landing page's full route cache is off and its RSC fetches run on every request — the fetch-level `revalidate` is the only cache in play.
- `lib/communityColors.ts` - **Shared 15-color palette**: used by both `TacticalMap` (COLOR: COMM toggle) and `StorylineGraph` for visual consistency across pages.
  - State: `compareId` (nullable) to track which report is being compared
  - Fetches: `report` detail, `compareReport` detail (when `compareId` is set), `comparison` delta (LLM-synthesized)
  - Conditional layout:
    - If `compareId === null`: Standard 3-column layout (TOC + content + sources)
    - If `compareId !== null`: Split 2-column layout with independent scroll per column (`h-[calc(100vh-200px)]`)
  - Dropdown "Compare with..." in header filters by same `report_type` (daily/weekly)
  - `ComparisonDelta` banner above split layout, visible with skeleton loader while Gemini processes (10–20s)
  - "Close ×" button to exit comparison mode
- **`app/stories/page.tsx`** - Storyline graph route (SSR metadata + dynamic import)
- `app/sitemap.ts` - Sitemap XML server-side: `/` (priority 1.0), `/insights` (0.9), dynamic insight slugs. Protected routes excluded to avoid redirect signals to Google.
- `app/robots.ts` - robots.txt con riferimento a sitemap.xml

### Components

#### Map Components (`components/IntelligenceMap/`)
- `TacticalMap.tsx` - Main Mapbox GL component with clustering and **Tier 3 layer toggles**: HEATMAP (intelligence_score weighted), ARCS (entity co-occurrence LineStrings, lazy-fetched), PULSE (animated ring for recent entities), COLOR:COMM (community-based coloring)
- `FilterPanel.tsx` - **Entity filter panel**: TYPE checkboxes (GPE/ORG/PERSON/LOC), SCORE slider (0–1 min intelligence_score), DAYS lookback, SEARCH text — all applied server-side via query params to `/api/v1/map/entities`
- `MapLoader.tsx` - Client wrapper for dynamic import (ssr: false)
- `MapSkeleton.tsx` - Loading skeleton for map
- `GridOverlay.tsx` - Tactical grid visualization
- `HUDOverlay.tsx` - HUD elements (ZULU clock, coordinates)
- `EntityDossier.tsx` - Entity detail panel with intelligence_score, storyline_count, top_storyline

#### **Storyline Graph Components (`components/StorylineGraph/`)**
See `components/StorylineGraph/context.md` for full detail.

- `StorylineGraph.tsx` - Main force-directed graph (react-force-graph-2d), `'use client'` (~479 lines)
  - **Top-N community coloring strategy**: 15-color `COMMUNITY_PALETTE` assigned by community size rank. Top 15 communities by node count get unique perceptually-distinct colors. All other communities rendered in `OTHER_COLOR = '#2A3A4A'` (neutral dark gray). Color assignment computed in `useMemo` via `communityColorMap` (Map<community_id, hex>).
  - **Momentum-as-brightness**: Node opacity = `Math.max(0.5, Math.min(1.0, 0.5 + momentum_score * 0.5))` — range [0.5, 1.0]. High-momentum storylines appear brighter; low-momentum ones are dimmer but always visible.
  - **Ghost highlight in ego mode**: When ego network is active, neighbor nodes that are normally gray (`OTHER_COLOR`) highlight to `EGO_HIGHLIGHT = '#FFFFFF'` (white) to stand out against the dimmed background.
  - Custom `paintNode` (Canvas 2D): radius = `4 + momentum_score * 12` (range 4–16 px); color by community via `communityColorMap`; glow ring on selected/hovered; label drawn with dark background pill, visible only when `globalScale > 1.5`, `momentum_score > 0.7`, or node is selected/hovered; selected node fills white with color border
  - `nodePointerAreaPaint`: extends hit area by +4 px beyond rendered radius so small nodes remain clickable
  - Custom `paintLink` (Canvas 2D): `strokeStyle = rgba(100,100,100, 0.2 + weight*0.6)`, `lineWidth = 0.5 + weight*2.5`
  - d3-force config: `warmupTicks=300`, `cooldownTicks=0`, `d3AlphaDecay=0.05`, `d3VelocityDecay=0.4`; drag, zoom, pan all enabled; `linkDirectionalParticles=0`
  - Node click toggles `selectedId` (click same node again deselects); hover sets `hoveredNode`
  - `handleNavigate(id)`: sets `selectedId`, calls `graphRef.current.centerAt(x, y, 500)` + `.zoom(3, 500)` for animated graph camera navigation
  - HUD overlay (top-left): NARRATIVE GRAPH label + NODES, EDGES, **COMMUNITIES**, AVG MOMENTUM, **EDGES/NODE** stats
  - **Community legend (top-right)**: Dynamic list of top 15 communities by size with colored dots and entity-based labels. Includes "Others (N)" row at bottom aggregating all minor communities and their total node count. Hidden when a node is selected.
  - **Entity filter (top-right)**: Autocomplete input (min 2 chars) populated from `key_entities` of all graph nodes. Selected entities shown as dismissible chips (OR logic — any match highlights). Highlights and zooms to matching nodes in dim mode; "Show only matches" toggle removes non-matching nodes entirely (isolate mode). `allEntities` memo derived from raw `graph?.nodes` (pre-momentum-filter). `entityHighlightIds` memo derived from `graphData.nodes` post-filter (defined after `graphData` to avoid TDZ). Entity filter + title filter share the same dim/isolate logic.
  - **Title search (top-right)**: Text input that filters nodes by keyword in `title` field, combinable with entity filter (AND logic between entity+title).
  - **"Show only matches" isolate toggle**: Appears when entity or title filter is active. When checked, `graphData` memo removes non-matching nodes from the force simulation entirely; edges between removed nodes also removed. When unchecked, non-matching nodes dimmed to `alpha=0.08` (same as ticker dim logic).
  - Momentum slider (top-right): Interactive range slider (0–1, step 0.1) for filtering nodes by minimum momentum score
  - Tooltip (bottom-left): shows hovered node title, momentum, article_count, category
  - Inline loading/error/empty states rendered over canvas
  - Corner bracket decorations (CSS, `pointer-events-none`)
- `GraphLoader.tsx` - Client wrapper for dynamic import (ssr: false, same pattern as MapLoader)
  - `next/dynamic` with `{ ssr: false, loading: () => <GraphSkeleton /> }`; required because Canvas API is not available in SSR
- `GraphSkeleton.tsx` - Loading skeleton with orange (#FF6B35) accent theme
  - Full-screen dark background, spinning ring, "INITIALIZING STORYLINE GRAPH" monospace label, HUD corner skeleton bars, subtle orange grid overlay, corner brackets
- `StorylineDossier.tsx` - Storyline detail side panel (follows EntityDossier pattern), `'use client'`
  - Fixed position panel: `right-4 top-4 bottom-4 w-[450px]`, `z-50`; renders `null` when `storylineId` is null
  - Calls `useStorylineDetail(storylineId)` internally
  - Momentum section: numeric score, HIGH/MEDIUM/LOW/MINIMAL label (thresholds: ≥0.8, ≥0.5, ≥0.3), article count, days active, animated color bar
  - Summary, key entities (badge list), connected storylines (clickable → `onNavigate(id)`), recent articles (scrollable 300 px, Italian date format)
  - `onNavigate(id)` callback triggers `handleNavigate` in StorylineGraph to center the graph camera

#### Report Components (`components/report/`)
- `ReportSections.tsx` - Accordion-based report content display with article sources
  - Displays article metadata, link, relevance score, and optional **bullet points** (key insights from AI analysis)
  - Expandable bullet points section with toggle state tracking per article
- **`ComparisonDelta.tsx`** (new) - Delta analysis banner for report comparisons
  - 4 collapsible sections: new_developments (green ✨), resolved_topics (orange ✓), trend_shifts (blue ⚡), persistent_themes (gray ⊗)
  - Skeleton loader during LLM processing (10–20 seconds)
  - Collapsible design with default expanded state

#### Dashboard Components (`components/dashboard/`)
- `StatsCard.tsx` - Individual KPI card. `trend` object accepts optional `label?: string` (e.g. "today", "7d") rendered as `+N today`.
- `StatsGrid.tsx` - 4-card grid: Total Articles (+N today/7d trend from `articles.articles_today`), Intelligence Briefs, Active Storylines (from `useStoriesCount()`), Sources Monitored (static 33).
- `ReportsTable.tsx` - Paginated reports table. Columns: Title (+ BLUF preview from `executive_summary`) | Type | Date | Articles. Status and Category columns removed. `recap` type has purple badge.

#### Shared UI Components
- `components/HelpModal.tsx` - Generic help modal (reuses OracleGuideModal UI pattern). Props: `open, onClose, title, subtitle, intro?, sections: HelpSection[]`. Used by Dashboard, TacticalMap, StorylineGraph. No Oracle-specific `onQuerySelect` prop.
- `DashboardSkeleton.tsx` - Loading skeletons (4-col grid)
- `ErrorState.tsx` - Error handling states
- **`components/CookieConsent.tsx`** (`'use client'`, 2026-07-17) — Google Consent Mode v2 banner. On mount: if `localStorage['mi_cookie_consent']` is `granted`/`denied`, silently replays that choice via `gtag('consent','update',...)`; otherwise shows the banner. Accept/Reject buttons call `gtag('consent','update',...)` + persist to `localStorage`. Also listens for a global `open-cookie-preferences` `window` event (dispatched by Footer's "Manage cookies" button) to reopen itself for consent revocation. Rendered once in `app/layout.tsx`, after `{children}`.

#### Oracle Chat Components (`components/oracle/`)
Oracle 2.0 UI fully decomposed into separate components. `app/oracle/page.tsx` is the thin shell that wires state.

- `OracleHeader.tsx` — sticky header: ◆ Oracle logo, `?` Guide, ⚙ Settings, `+` Nuova sessione; "key mancante" badge when BYOK not set
- `OracleMessage.tsx` — `UserBubble` + `AssistantBubble`
  - **Inline citation badges**: preprocesses `[1]` → `` `__CITE__1__` `` (unique marker), intercepts in `code` react-markdown component → renders clickable orange badge; clicking scrolls sidebar to source card
  - **Follow-up badge**: shows "↩ Continuazione" when `metadata.is_follow_up === true`
  - **Collapsible query plan**: "Analisi elaborazione" section below answer — intent (Italian label), complexity, execution time, tools, sub-queries (COMPARATIVE), execution step descriptions
- `OracleThinkingState.tsx` — replaces 3-dot spinner; shows sequential processing steps with ASCII braille spinner: "Analisi semantica → Scansione database vettoriale (N fonti) → Estrazione documenti → Sintesi strategica"
- `OracleSourcesSidebar.tsx` — desktop sidebar (hidden md:flex) + embedded mode (mobile bottom sheet). Converts `OracleSource[]` → `ReportSource[]` via `toReportSources()` and renders identical source items as the report `SourcesSidebar`: `[N]` bracket badge, title, "Open source →" link, relevance %, expandable "Key Points". `embedded={true}` skips the outer sidebar wrapper for mobile use. `OracleSourceCard.tsx` deleted (replaced by this unified approach).
- `OracleEmptyState.tsx` — professional welcome screen: 2×3 grid of intent type cards (Fattuale/Analitico/Narrativo/Mercato/Comparativo/Panoramica) + 4 quick-example chips; clicking injects query into textarea
- `OracleGuideModal.tsx` — full-screen modal (ESC to close): Cos'è Oracle, 6 intent types with clickable examples, filters guide, technical limits
- `OracleSettingsPanel.tsx` — right-side drawer: BYOK Gemini API key (save/remove, show/hide, validation), modalità ricerca, tipo di ricerca, date range, GPE filter; **"Azzera memoria di sessione"** button (2-step confirm) calls `clearMessages()` + closes panel

#### Insights Components (`components/insights/`)
- `WaitlistInline.tsx` - Email waitlist signup form embedded in insights pages

#### Romania Components (`components/romania/`)
- `MacroMiniDashboard.tsx` (`'use client'`) — SWR-fetches `/api/proxy/romania/macro`, renders 5 `MacroIndicatorCard` in a responsive grid; 3 distinct error states: network error (red) / no indicators (gray+setup hint) / all-null values (amber). Skeleton on loading.
- `MacroIndicatorCard.tsx` (`'use client'`) — single indicator card with formatted value, delta vs previous, SVG sparkline; **staleness-aware**: amber border + `is_stale` prop shows "Xg fa" badge and grayed value when data is old relative to its cadence; frequency label ("mensile", "giornaliero") shown top-right. Props: `expectedFrequency`, `isStale`, `stalenessDays` (fed by `/romania/macro` API extension).
- `BriefingList.tsx` (`'use client'`) — tabs Daily/Weekly, SWR-fetches `/api/v1/romania/briefings?type=...&limit=20`, renders `BriefingCard` list; shows command hint when empty.
- `BriefingCard.tsx` (`'use client'`) — single briefing item with date, type badge (Giornaliero/Settimanale), excerpt (3-line clamp), and link to `/romania/{id}`.

#### Landing Components (`components/landing/`)
Refactor 2026 → segue il prototipo `Landing Page.html` alla root del repo (cinematic / tactical HUD). Data e JSON-LD in `lib/landing/`.

**Styling (2026-07-17)**: tutti i 14 componenti convertiti da inline `style={{}}` (158 occorrenze) a classi Tailwind. Rimasti inline solo i valori genuinamente dinamici a runtime — non esprimibili come classi statiche: colori per-item calcolati (`Synthesis`/`AppFrame` `tagColor`/`labelColor`, `Capabilities` background alternato, `Ticker` `dot` color per community), stato React (`Navbar` `scrolled`/`logoHover` → background/backdrop-filter/opacity, `FAQ` `isOpen` → `max-height`/`color`/`rotate`). **Nota specificità CSS**: le utility class `.btn-primary`/`.btn-ghost` in `globals.css` non sono in un `@layer`, quindi battono sempre le utility Tailwind (che Tailwind v4 mette in `@layer utilities`, priorità più bassa a prescindere dall'ordine sorgente) — dove serve un override di padding/font-size su questi bottoni (`FinalCTA`, `Navbar`), va usato `style` inline, non una classe Tailwind.

- `Navbar.tsx` (`'use client'`) — fixed top 60px, logo MACRO+INTEL, link Insights/Features/About, CTA "Open Platform"; scroll-aware (background blur dopo 40px)
- `Hero.tsx` (RSC) — split 2-col, classification tag LIVE, headline 2-righe (seconda con `.gradient-text`), 2 CTA + stats row 4 KPI; preview destra "Narrative Graph" con HUD frame + chip "LAST SYNC ZULU"; background `next/image` `world-map-hero.jpg` cinematic
- `Ticker.tsx` (RSC) — live signal feed orizzontale infinito, alimentato dalle storyline reali ordinate per `momentumScore`; `SIGNALS` resta come fallback
- `Synthesis.tsx` (RSC) — `id="synthesis"`, scala di sintesi verticale a 3 gradini (Daily → Weekly → Monthly) con "spina" gradient che rende grafica la derivazione. Solo Daily ha un artefatto live (`DemoBriefing`); Weekly/Monthly mostrano la card meccanismo (reads/produces), non un report inventato
- `Personas.tsx` (RSC) — 2-col (titolo+CTA / 4 carte persona)
- `Capabilities.tsx` (RSC) — grid 3×2 di 6 capability con icona simbolica
- `FinalCTA.tsx` (RSC) — `id="about"`, badge "NOW FULLY PUBLIC", headline 52px gradient, 3 CTA
- `FAQ.tsx` (`'use client'`) — accordion 7 voci, `+ → ×` rotante 45°, `max-height: 0 ↔ 240px` con transizione. Dati da `FAQS` in `lib/landing/schema.ts`
- `Footer.tsx` (`'use client'`, 2026-07-17: passato da RSC a client per il bottone "Manage cookies") — 3-col (brand+desc | Platform: dashboard/stories/map/oracle | Resources: insights/features/about + "Manage cookies" che dispatcha `open-cookie-preferences`) + bottom bar
- `AppFrame.tsx` (RSC) — chrome stile macOS (3 dot semaforo + label colorata + badge); fallback gradient se `src` mancante
- `DemoBriefing.tsx` (RSC) — wrapper `AppFrame` sul briefing pubblicato più di recente
- `LazyMount.tsx` (`'use client'`) — wrapper generico `IntersectionObserver` (`rootMargin: '100% 0px'`): monta i `children` una sola volta quando la sezione si avvicina al viewport. Serve alle scene GSAP, il cui pin-spacer espande la sezione da 100vh a ~450vh — montare a sezione già visibile produrrebbe un salto di layout

**Le tre scene.** Non sono consecutive (ritmo D5: apertura / centro / chiusura) e condividono un solo principio implementativo: **lo stato è funzione pura del progresso**, mai una simulazione integrata nel tempo — così lo scrub è reversibile e stabile al resize.

- `LivingGraphScene.tsx` — **Scena 2**, dentro il pannello dell'Hero. Unica scena *non* guidata dallo scroll: loop ambient di 20s che parte in viewport (l'Hero deve essere vivo prima che il lettore scrolli). Disegna le community come nebulose, non i singoli nodi. Deliberatamente **muta**: nessuna etichetta, nessun contatore — è la *promessa* che l'atto finale della Scena 1 paga. Se `graph.totalActive === 0` l'Hero mostra il PNG statico (fallback D8, mai un canvas vuoto)
- `SignalDescentCanvas.tsx` — **Scena 1**, `id="features"`, full-bleed, pin GSAP `+=450%` con `scrub: 1`. Sette atti mappati sulla pipeline reale (SWARM → COLLAPSE → FATE → BIRTH → WEB → GRAVITY → IGNITION). Ritmo **variabile**: le transizioni sono battute brevi, i concetti (FATE, GRAVITY, IGNITION) hanno spazio. L'atto finale risolve **sulla stessa immagine della Scena 2** ed è etichettato con i community name reali (`topCommunityNames`) — senza backend l'atto renderizza la forma senza etichette, mai nomi inventati. Oltre `t ≥ 0.94` parte un rAF per il respiro ambient: è l'aggancio verso il loop temporale della Scena 2
- `AskAnythingScene.tsx` — **Scena 3**, `id="oracle"`, contenuta (non full-bleed), pin `+=220%`. Percorso di routing Oracle + assemblaggio documento + citazioni. Fonti mostrate come **template** (badge tipo + skeleton), non titoli/editori inventati

**Asset richiesti** in `public/assets/`: `world-map-hero.jpg`, `narrative-graph-hero.png` (fallback Scena 2), `dashboard-screenshot.png`. Senza asset i componenti renderizzano placeholder gradient (no broken image).

**Data, scene e JSON-LD** in `lib/landing/`:
- `live.ts` — fetch RSC server-only verso `/api/v1/stories/graph` (con `X-API-Key`, **mai** esposta al browser) e `/api/v1/insights?limit=1`, `revalidate: 900`, timeout 2.5s, fallback deterministico ovunque. Esporta anche `topCommunityNames()` per le etichette della Scena 1.
  - **Budget di payload (fix `fix-emerging-storyline-lifecycle-leak`)**: la risposta del grafo deve restare **sotto 1 MB**. Due vincoli distinti, entrambi violati fino al 2026-09: il timeout di 2.5s (misurati 3229 ms su 5.82 MB dentro il container) e il limite fisso di **2 MB per entry** della data cache di Next (`incremental-cache/index.js`, e il corpo è codificato in base64 prima del controllo, quindi il budget reale sul JSON è ~1.5 MB). Superato il secondo, `revalidate` non memorizza **nulla** e ogni render rifà la fetch completa. L'endpoint accetta ora `view=slim` (omette `summary` e `key_entities`, 27% del payload) e limiti su nodi/archi. **Se aggiungi un consumo di dati vivi, misura il payload reale — non stimarlo: la degradazione elegante nasconde il guasto invece di segnalarlo.**
- `nebulaRender.ts` — **primitive canvas condivise fra Scena 1 e Scena 2**: tipo `RGB`, palette community in forma RGB (`dataColor`), `drawNebula`, `rgba`, `lerpColor`, `bezierPoint`. È il modulo che garantisce che le due scene convergano sullo *stesso* oggetto: una reimplementazione parallela divergerebbe alla prima modifica e l'agnizione finale perderebbe senso
- `signalDescentScene.ts` — generatore deterministico (PRNG mulberry32 seeded) della Scena 1: particelle a keyframe + nodi storyline + archi Jaccard + cluster. `sampleParticle()` / `sampleNode()` interpolano a un dato progresso
- `livingGraphLayout.ts` / `livingGraphFixture.ts` — layout della Scena 2 dai dati reali, e fixture seeded per le route `/dev/*` senza backend
- `oracle-demo.ts` — costanti Scena 3 estratte da `oracle_orchestrator.py` (9 tool, 9 path SOP)
- `data.ts` — costanti tipate `SIGNALS`, `SYNTHESIS_LEVELS`, `PERSONAS`, `CAPS`
- `schema.ts` — esporta `FAQS` (7 Q&A) + 5 oggetti JSON-LD: `softwareApplicationSchema`, `organizationSchema`, `websiteSchema` (con SearchAction su `/oracle?q={search_term_string}`), `faqSchema` (derivato da `FAQS`), `aboutPageSchema` (per `/about`)

**Route di prototipo** (`app/dev/*`, `robots: noindex`, non linkate): `signal-descent`, `living-graph`, `ask-anything`. `signal-descent` monta anche la Scena 2 con la fixture in cima alla pagina, così l'agnizione dell'atto finale è verificabile in locale senza backend.

**About page** (`components/about/` + `lib/about/`):
- Componenti dedicati (RSC): `AboutHero`, `MissionVision` (2 card side-stripe), `WhoItsFor` (6 persona DIVERSE dalla landing), `Coverage` (8 topic 4×2), `AboutCTA`, `AboutFooter` (footer minimale 1-riga)
- `lib/about/data.ts` — `ABOUT_PERSONAS` (6) e `ABOUT_COVERAGE` (8) tipati `as const`
- Riusa `Navbar` della landing con prop `solid` (sempre opaco, no scroll listener)

#### App Shell (`components/shell/`)
Chrome delle route applicative — sostituisce il `Navbar` della landing su **tutte** le route interne (`/dashboard`, `/insights`, `/romania`, `/oracle`, `/stories` + le tre route di dettaglio articolo). Una top-bar marketing con "Open Platform" non ha senso quando il lettore è già dentro la piattaforma, e costava 60px di altezza sulle due route che hanno bisogno del viewport intero. `Navbar` resta solo su `/` e `/about`.

- `AppShell.tsx` (`'use client'`) — rail fisso 64px espandibile a 224px in hover (**CSS `group-hover`, non state React**: il rail è un overlay, allargarlo non deve rifluire il contenuto accanto, e un hover che ri-renderizza il sottoalbero sarebbe lavoro sprecato) + header sticky con indicatore di freschezza pipeline. Prop `fullBleed` per `/oracle` e `/stories`: prendono il rail ma non l'header né il padding, così chat e grafo mantengono l'altezza piena. **`/map` è assente dal rail di proposito** — è ancora COMING SOON e deliberatamente non linkata ovunque (Resolved Q1). L'indicatore di stato viene **omesso** (non mostrato in errore) quando `useDashboardStats` fallisce: un elemento di chrome che dice "unknown" su ogni pagina è peggio di nessun elemento
- `CommandPalette.tsx` (`'use client'`) — ⌘K/Ctrl+K. Tre sorgenti in ordine di priorità: route (sempre disponibili, funzionano offline) → storyline per titolo (dal grafo che `/stories` già poller) → fallthrough "Ask Oracle" che instrada su `/oracle?q=…`, così del testo digitato non è mai un vicolo cieco. `Escape` chiude e **restituisce il focus** all'elemento che ce l'aveva all'apertura. L'hotkey è registrata su `window` con `preventDefault`, quindi scatta anche con una textarea a fuoco senza inserire il carattere (è il caso di `/oracle`). Il grafo viene fetchato solo dopo la prima apertura — la shell monta su ogni route e il payload non serve finché nessuno cerca

#### UI Components (`components/ui/`)
- Shadcn components: Button, Card, Skeleton, Table, Badge
- `MarkdownContent.tsx` — renderer condiviso dei corpi Markdown (GFM + sanitizzazione DOMPurify). Prop `variant`: `default` (frammenti UI brevi) o **`editorial`** (misura 68ch, interlinea 1.7, serif). Opt-in e non default perché lo stesso renderer disegna anche anteprime e sommari, dove 68ch e il serif sarebbero entrambi sbagliati

#### Motion (`components/motion/`)
- `Reveal.tsx` (`'use client'`) — wrapper `LazyMotion` + `m` di `motion` (ex `framer-motion`). Riceve `children`, così i genitori RSC non vengono promossi a client component

### Tipografia editoriale
`lib/fonts.ts` esporta `editorialSerif` (**Source Serif 4** via `next/font/google`, self-hosted a build time — nessuna richiesta a `fonts.gstatic.com`). **Non è registrato in `app/layout.tsx` di proposito**: finirebbe sul percorso critico di ogni route, incluse `/` e `/dashboard` che non hanno prosa lunga. Essendo importato solo dalle tre route articolo, Next confina `@font-face` e preload nel loro chunk CSS (verificato: 0 riferimenti nell'HTML di `/` e `/dashboard`).

La scelta fra Source Serif 4 e Newsreader è caduta sul primo perché è il più neutro dei due — x-height alta, contrasto di tratto basso, disegnato come *text face* e non display — e deve convivere nella stessa pagina con metadati e citazioni in Geist Mono senza competerci. Il carattere più editoriale di Newsreader legge "servizio di rivista", registro sbagliato per un briefing di intelligence. Sostituirlo è una riga in `lib/fonts.ts`.

Il token Tailwind `--font-serif` risolve a `var(--font-source-serif)`: i due nomi devono restare distinti, altrimenti la custom property referenzia sé stessa e CSS la scarta del tutto.

### Configuration
- `app/layout.tsx` - Root layout con Google Analytics (`G-MBHW2XG1Q3`), meta tag Google Search Console (`verification.google`), **JSON-LD Organization + WebSite structured data**. **Consent Mode v2 (2026-07-17)**: script `beforeInteractive` imposta `analytics_storage`/`ad_storage`/`ad_user_data`/`ad_personalization` su `denied` di default (con `wait_for_update: 500`) *prima* che il tag GA venga caricato — GA parte comunque in modalità "cookieless ping" ma non traccia fino a consenso esplicito via `<CookieConsent />`. Nessuna pagina privacy policy dedicata esiste ancora (`/privacy` — non referenziata dal banner per evitare link 404).
- `.env.local` - Environment variables:
  - `NEXT_PUBLIC_MAPBOX_TOKEN` - Mapbox API token (client-side, restrict by domain)
  - `INTELLIGENCE_API_URL` - Backend API URL (server-side only)
  - `INTELLIGENCE_API_KEY` - API authentication key (server-side only, via proxy)
  - `JWT_SECRET`, `ACCESS_CODES` - **Still required, despite the access gate being removed**: `app/api/access/verify/route.ts` reads both and throws if `JWT_SECRET` is unset, and nginx still exposes the route (`location /api/access/`). No component calls it any more, but it is publicly reachable and mints 30-day JWT cookies. Keep both in `docker-compose.yml` until the route and its nginx location are deleted together (verified 2026-09-03).
  - ~~`ORACLE_REQUIRE_GEMINI_KEY`~~ - **No longer used**: Oracle BYOK was removed
- `next.config.ts` - Next.js configuration
- `package.json` - Dependencies
- `tsconfig.json` - TypeScript config

### API Proxy (`app/api/proxy/[...path]/route.ts`)
Next.js Route Handler that forwards GET/POST requests from the browser to the FastAPI backend without exposing credentials to the client.

- **URL pattern**: `GET /api/proxy/<path...>` → `GET http://<INTELLIGENCE_API_URL>/api/v1/<path...>`
- **POST support**: Only `oracle/*` paths allowed via POST (120s timeout)
- **Security**: Path traversal rejection (`..`, leading `/`); prefix whitelist (`dashboard`, `reports`, `stories`, `map`, `oracle`)
- **Auth header**: Adds `X-API-Key: <INTELLIGENCE_API_KEY>` to every upstream request (server-side env var only, never in browser bundle)
- **Query string**: Forwarded verbatim to upstream (GET only)
- **Timeout**: GET = 300 s, POST = 120 s (`AbortController`) → 504 on abort, 502 on connection failure
- **Env vars consumed**: `INTELLIGENCE_API_URL` (default `http://localhost:8000`), `INTELLIGENCE_API_KEY`

### Types & Hooks
- `types/entities.ts` - Entity TypeScript interfaces
- `types/dashboard.ts` - Dashboard TypeScript interfaces
  - **`ComparisonDelta`** (new) — new_developments, resolved_topics, trend_shifts, persistent_themes arrays
  - **`ReportComparisonResponse`** (new) — report_a, report_b metadata + delta object
  - **`ReportSource` updated** — added optional `bullet_points?: string[]` field for AI-extracted key insights
- **`types/oracle.ts`** - Oracle 2.0 TypeScript interfaces:
  - `OracleSource`, `QueryPlan` (`intent` union includes `'overview'`), `ExecutionStep`, `OracleResponse`, `OracleChatMessage`, `OracleChatFilters`, **`OracleActiveFilters`** (mode/search_type/start_date/end_date/gpe_filter)
- **`types/stories.ts` updated** — `LinkedArticle` now includes optional `bullet_points?: string[]` field
- **`hooks/useOracleChat.ts`** - Oracle chat state management:
  - `useOracleChat()` → `{ messages, isLoading, error, byokError, sendMessage, clearMessages, lastAssistantMessage, geminiApiKey, setGeminiApiKey, activeFilters, setActiveFilters }`
  - Stable `session_id` via `crypto.randomUUID()` (persists within browser session, reset on `clearMessages`)
  - POST to `/api/proxy/oracle/chat` with 120s `AbortController` timeout; **`activeFilters`** state (mode/search_type/dates/gpe_filter) passed to every request
  - Optimistic user message insertion before API response
- **`hooks/useDashboard.ts` updated**:
  - **`useReportCompare(idA, idB)`** (new) — SWR hook for delta analysis
  - Calls `GET /api/proxy/reports/compare?ids=A,B`
  - Key is `null` when either ID is `null` (no-fetch behavior)
  - 24-hour cache (`dedupingInterval: 86400000`) — reports are static
  - 1 retry on error when online
  - Returns `{ comparison, isLoading, error }`
- **`types/stories.ts`** - Storyline graph TypeScript interfaces:
  - `NarrativeStatus` — `'emerging' | 'active' | 'stabilized'`
  - `StorylineNode` — id, title, summary, category, narrative_status, momentum_score, article_count, key_entities, start_date, last_update, days_active
  - `StorylineEdge` — source, target, weight, relation_type
  - `GraphStats` — total_nodes, total_edges, avg_momentum
  - `GraphNetwork` — nodes, links, stats
  - `GraphNetworkResponse` / `StorylineDetailResponse` — wrapper with success, data, error, generated_at
  - `RelatedStoryline` — id, title, weight, relation_type
  - `LinkedArticle` — id, title, source, published_date
  - `StorylineDetailData` — storyline, related_storylines, recent_articles
- `utils/api.ts` - API client for backend communication
- `hooks/useDashboard.ts` - SWR hooks for dashboard data
- **`hooks/useStories.ts`** - SWR hooks for storyline graph data
  - Shared `fetcher<T>`: 10 s `AbortController` timeout; maps `AbortError` → offline-aware `ApiError`; maps `TypeError`/`Failed to fetch` → offline-aware `ApiError`
  - `useGraphNetwork()` → `GET /api/proxy/stories/graph` — 60 s polling, `revalidateOnFocus: true`, 3 retries (5 s interval), skips retry when offline
  - `useStorylineDetail(id)` → `GET /api/proxy/stories/<id>` — no polling, `revalidateOnFocus: false`, 2 retries; key is `null` when `id` is null (SWR no-fetch)

## Dependencies

- **Internal**: Consumes `src/api/` endpoints
- **External**:
  - `next` (16.x) - React framework with App Router
  - `react` (19.x) - UI library
  - `mapbox-gl` (3.x) - Map rendering
  - **`react-force-graph-2d`** - Force-directed graph visualization (d3-force based)
  - `swr` - Data fetching with polling
  - **`motion`** (12.x) - Animations. Sostituisce `framer-motion`, **rimosso**: `motion` è il pacchetto successore, e l'uso passa da `components/motion/Reveal.tsx` (`LazyMotion` + `m`) per non spedire l'intero runtime di animazione
  - **`gsap`** (3.x) + ScrollTrigger - Solo per le Scene 1 e 3 della landing, **importato dinamicamente** dentro i componenti scena così non entra nel bundle iniziale
  - `sigma` (3.x) + `graphology` - Grafo su `/stories`
  - `tailwindcss` (4.x) - Styling. Il layer di token in `app/globals.css` (`@theme`) è **la sorgente unica** per scala tipografica, durate, easing e palette `--data-1..15`; i componenti non devono reintrodurre letterali `text-[Npx]`/`#hex`
  - `lucide-react` - Icons (GitBranch for Storylines nav)

## Data Flow

All browser-to-backend traffic goes through the Next.js API proxy at `/api/proxy/<path>`, which adds the `X-API-Key` header server-side before forwarding to FastAPI.

- **Input** (browser → proxy → FastAPI):
  - GeoJSON: `GET /api/proxy/map/entities` → `GET /api/v1/map/entities`
  - Dashboard stats: `GET /api/proxy/dashboard/stats` → `GET /api/v1/dashboard/stats`
  - Reports: `GET /api/proxy/reports` → `GET /api/v1/reports`
  - **Graph network**: `GET /api/proxy/stories/graph` → `GET /api/v1/stories/graph` (nodes, links, stats)
  - **Storyline detail**: `GET /api/proxy/stories/{id}` → `GET /api/v1/stories/{id}`
  - Mapbox token: `NEXT_PUBLIC_MAPBOX_TOKEN` env var (client-side, domain-restricted)

- **Output**:
  - Interactive map with entity clustering (Mapbox GL)
  - **Force-directed narrative graph with momentum-scaled nodes** (react-force-graph-2d, Canvas 2D)
  - **Storyline dossier panels on node click** (slide-in, 450 px)
  - Dashboard with live KPIs and reports table

## Route Rendering Architecture

The `/stories` route follows the same 3-layer SSR-split pattern used by `/map`:

```
app/stories/page.tsx  (Server Component)
    ├── Exports Metadata (title, OpenGraph) — rendered server-side for SEO
    └── <GraphLoader>  (Client Component, 'use client')
            └── next/dynamic(() => import('./StorylineGraph'), { ssr: false })
                    ├── <GraphSkeleton>  (shown while JS bundle downloads)
                    └── <StorylineGraph>  (Canvas-based, requires browser APIs)
                            └── <StorylineDossier>  (rendered on node click)
```

**Why `ssr: false`**: `react-force-graph-2d` uses the Canvas API and `requestAnimationFrame`, which are not available in the Node.js SSR environment.

## Running

```bash
cd web-platform
npm install
npm run dev
# Routes:
#   http://localhost:3000/          - Landing page
#   http://localhost:3000/stories   - Storyline graph
#   http://localhost:3000/map       - Tactical intelligence map
#   http://localhost:3000/dashboard - Dashboard with reports
#   http://localhost:3000/oracle    - Oracle 2.0 chat
```
