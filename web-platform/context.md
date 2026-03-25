# Web Platform Context

## Purpose
Modern Next.js/React frontend for interactive intelligence visualization. Provides a tactical intelligence map (Mapbox), a **narrative storyline graph** (force-directed), a dashboard with reports, and a landing page. Consumes data from the FastAPI backend.

## Architecture Role
Advanced visualization layer consuming data from `src/api/` REST endpoints. Provides interactive exploration of geopolitical entities (map), **narrative storyline network** (graph), and intelligence reports (dashboard). Separate from the Streamlit HITL dashboard.

## Key Files

### App Structure
- `app/layout.tsx` - Root Next.js layout (Google Analytics, GSC verification)
- `app/globals.css` - Global styles with animations
- `app/page.tsx` - Landing page (Hero, Features, ProductShowcase, ICPSection, StatsCounter, CTASection, waitlist)
- `app/map/page.tsx` - Tactical map route (SSR metadata + dynamic import)
- `app/dashboard/page.tsx` - Dashboard route (SWR data fetching)
- `app/dashboard/report/[id]/page.tsx` - **Report detail route (updated with comparison UI)**
- `app/access/page.tsx` - **Access code entry form**: validates code against `ACCESS_CODES` env var via `app/api/access/verify/route.ts`, issues JWT signed with `JWT_SECRET`, sets `macrointel_access` cookie, redirects to original route. Open to all.
- `app/insights/page.tsx` - **Public intelligence briefings list**: fetches from `/api/v1/insights`, renders briefings with category badges and summaries. No auth required — public SEO page.
- `app/insights/[slug]/page.tsx` - **Briefing detail**: renders full executive summary. No auth required.
- `middleware.ts` - **JWT access control**: protects `/dashboard`, `/map`, `/stories`, `/oracle` — verifies `macrointel_access` cookie via `jose.jwtVerify`; redirects to `/access?from=<path>` on missing/invalid token.
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
- `app/sitemap.ts` - Sitemap XML generata server-side (/, /dashboard, /stories, /map, /oracle, /insights, /insights/[slug])
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
- `StatsCard.tsx` - Individual KPI card
- `StatsGrid.tsx` - Grid of stats cards
- `ReportsTable.tsx` - Paginated reports table
- `DashboardSkeleton.tsx` - Loading skeletons
- `ErrorState.tsx` - Error handling states

#### Oracle Chat Components (`components/oracle/`)
Oracle 2.0 UI fully decomposed into separate components. `app/oracle/page.tsx` is the thin shell that wires state.

- `OracleHeader.tsx` — sticky header: ◆ Oracle logo, `?` Guide, ⚙ Settings, `+` Nuova sessione; "key mancante" badge when BYOK not set
- `OracleMessage.tsx` — `UserBubble` + `AssistantBubble`
  - **Inline citation badges**: preprocesses `[1]` → `` `__CITE__1__` `` (unique marker), intercepts in `code` react-markdown component → renders clickable orange badge; clicking scrolls sidebar to source card
  - **Follow-up badge**: shows "↩ Continuazione" when `metadata.is_follow_up === true`
  - **Collapsible query plan**: "Analisi elaborazione" section below answer — intent (Italian label), complexity, execution time, tools, sub-queries (COMPARATIVE), execution step descriptions
- `OracleThinkingState.tsx` — replaces 3-dot spinner; shows sequential processing steps with ASCII braille spinner: "Analisi semantica → Scansione database vettoriale (N fonti) → Estrazione documenti → Sintesi strategica"
- `OracleSourceCard.tsx` — freshness pill (green <7d / yellow 7–30d / red >30d), index badge (for citation correlation), similarity progress bar, `source` domain badge; `forwardRef` for sidebar scroll-to
- `OracleSourcesSidebar.tsx` — desktop sidebar (hidden md:flex); `highlightedSource` prop scrolls to matching card via `scrollIntoView`; each card has numbered `ref`
- `OracleEmptyState.tsx` — professional welcome screen: 2×3 grid of intent type cards (Fattuale/Analitico/Narrativo/Mercato/Comparativo/Panoramica) + 4 quick-example chips; clicking injects query into textarea
- `OracleGuideModal.tsx` — full-screen modal (ESC to close): Cos'è Oracle, 6 intent types with clickable examples, filters guide, technical limits
- `OracleSettingsPanel.tsx` — right-side drawer: BYOK Gemini API key (save/remove, show/hide, validation), modalità ricerca, tipo di ricerca, date range, GPE filter; **"Azzera memoria di sessione"** button (2-step confirm) calls `clearMessages()` + closes panel

#### Insights Components (`components/insights/`)
- `WaitlistInline.tsx` - Email waitlist signup form embedded in insights pages

#### Landing Components (`components/landing/`)
- `Navbar.tsx` - Navigation with links to Dashboard, Storylines, Intelligence Map, Oracle
- `Hero.tsx` - Hero section with CTA
- `Features.tsx` - Feature list
- `ProductShowcase.tsx` - Interactive product screenshots showcase
- `ICPSection.tsx` - Ideal customer profile section
- `StatsCounter.tsx` - Live dashboard statistics counter (total articles, reports, entities)
- `CTASection.tsx` - Call-to-action section with waitlist
- `Footer.tsx` - Footer with links

#### UI Components (`components/ui/`)
- Shadcn components: Button, Card, Skeleton, Table, Badge

### Configuration
- `app/layout.tsx` - Root layout con Google Analytics (`G-MBHW2XG1Q3`) e meta tag Google Search Console (`verification.google`)
- `.env.local` - Environment variables:
  - `NEXT_PUBLIC_MAPBOX_TOKEN` - Mapbox API token (client-side, restrict by domain)
  - `INTELLIGENCE_API_URL` - Backend API URL (server-side only)
  - `INTELLIGENCE_API_KEY` - API authentication key (server-side only, via proxy)
  - `JWT_SECRET` - Secret for signing access JWTs (middleware + verify route)
  - `ACCESS_CODES` - Comma-separated valid access codes for `/access` page
  - `ORACLE_REQUIRE_GEMINI_KEY` - `true` = BYOK enforced for Oracle
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
  - `framer-motion` - Animations
  - `tailwindcss` (4.x) - Styling
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
