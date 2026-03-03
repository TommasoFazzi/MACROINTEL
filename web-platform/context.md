# Web Platform Context

## Purpose
Modern Next.js/React frontend for interactive intelligence visualization. Provides a tactical intelligence map (Mapbox), a **narrative storyline graph** (force-directed), a dashboard with reports, and a landing page. Consumes data from the FastAPI backend.

## Architecture Role
Advanced visualization layer consuming data from `src/api/` REST endpoints. Provides interactive exploration of geopolitical entities (map), **narrative storyline network** (graph), and intelligence reports (dashboard). Separate from the Streamlit HITL dashboard.

## Key Files

### App Structure
- `app/layout.tsx` - Root Next.js layout
- `app/globals.css` - Global styles with animations
- `app/page.tsx` - Landing page
- `app/map/page.tsx` - Tactical map route (SSR metadata + dynamic import)
- `app/dashboard/page.tsx` - Dashboard route (SWR data fetching)
- `app/dashboard/report/[id]/page.tsx` - Report detail route
- **`app/stories/page.tsx`** - Storyline graph route (SSR metadata + dynamic import)
- `app/sitemap.ts` - Sitemap XML generata server-side (4 route: /, /dashboard, /stories, /map)
- `app/robots.ts` - robots.txt con riferimento a sitemap.xml

### Components

#### Map Components (`components/IntelligenceMap/`)
- `TacticalMap.tsx` - Main Mapbox GL component with clustering
- `MapLoader.tsx` - Client wrapper for dynamic import (ssr: false)
- `MapSkeleton.tsx` - Loading skeleton for map
- `GridOverlay.tsx` - Tactical grid visualization
- `HUDOverlay.tsx` - HUD elements (ZULU clock, coordinates)
- `EntityDossier.tsx` - Entity detail panel

#### **Storyline Graph Components (`components/StorylineGraph/`)**
See `components/StorylineGraph/context.md` for full detail.

- `StorylineGraph.tsx` - Main force-directed graph (react-force-graph-2d), `'use client'`
  - Custom `paintNode` (Canvas 2D): radius = `4 + momentum_score * 12` (range 4–16 px); color by status (emerging=#FF6B35, active=#00A8E8, stabilized=#666); glow ring on selected/hovered; label drawn with dark background pill, visible only when `globalScale > 1.5`, `momentum_score > 0.7`, or node is selected/hovered; selected node fills white with color border
  - `nodePointerAreaPaint`: extends hit area by +4 px beyond rendered radius so small nodes remain clickable
  - Custom `paintLink` (Canvas 2D): `strokeStyle = rgba(100,100,100, 0.2 + weight*0.6)`, `lineWidth = 0.5 + weight*2.5`
  - d3-force config: `cooldownTicks=100`, `d3AlphaDecay=0.02`, `d3VelocityDecay=0.3`; drag, zoom, pan all enabled; `linkDirectionalParticles=0`
  - Node click toggles `selectedId` (click same node again deselects); hover sets `hoveredNode`
  - `handleNavigate(id)`: sets `selectedId`, calls `graphRef.current.centerAt(x, y, 500)` + `.zoom(3, 500)` for animated graph camera navigation
  - HUD overlay (top-left): NARRATIVE GRAPH label + total_nodes, total_edges, avg_momentum from `graph.stats`
  - Status legend (top-right): hidden when a node is selected
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

#### Dashboard Components (`components/dashboard/`)
- `StatsCard.tsx` - Individual KPI card
- `StatsGrid.tsx` - Grid of stats cards
- `ReportsTable.tsx` - Paginated reports table
- `DashboardSkeleton.tsx` - Loading skeletons
- `ErrorState.tsx` - Error handling states

#### Oracle Chat Components (`app/oracle/page.tsx`)
- `OraclePage` — `'use client'`, 2-column layout: chat + sources sidebar
  - `UserBubble` / `AssistantBubble` (react-markdown rendering with custom styles)
  - `TypingIndicator` — 3-dot bouncing animation while loading
  - `QueryPlanBadges` — shows intent + complexity + tool badges per assistant message
  - `SourceCard` — shows REPORT/ARTICOLO badge, similarity %, preview
  - Suggestion chips on empty state; auto-scroll to latest message

#### Landing Components (`components/landing/`)
- `Navbar.tsx` - Navigation with links to Dashboard, **Storylines**, Intelligence Map, **Oracle**
- `Hero.tsx`, `Features.tsx`, `Footer.tsx` - Landing page sections

#### UI Components (`components/ui/`)
- Shadcn components: Button, Card, Skeleton, Table, Badge

### Configuration
- `app/layout.tsx` - Root layout con Google Analytics (`G-MBHW2XG1Q3`) e meta tag Google Search Console (`verification.google`)
- `.env.local` - Environment variables:
  - `NEXT_PUBLIC_MAPBOX_TOKEN` - Mapbox API token (client-side, restrict by domain)
  - `INTELLIGENCE_API_URL` - Backend API URL (server-side only)
  - `INTELLIGENCE_API_KEY` - API authentication key (server-side only, via proxy)
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
- **`types/oracle.ts`** - Oracle 2.0 TypeScript interfaces:
  - `OracleSource`, `QueryPlan`, `ExecutionStep`, `OracleResponse`, `OracleChatMessage`, `OracleChatFilters`
- **`hooks/useOracleChat.ts`** - Oracle chat state management:
  - `useOracleChat()` → `{ messages, isLoading, error, sendMessage, clearMessages, lastAssistantMessage }`
  - Stable `session_id` via `crypto.randomUUID()` (persists within browser session, reset on `clearMessages`)
  - POST to `/api/proxy/oracle/chat` with 120s `AbortController` timeout
  - Optimistic user message insertion before API response
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
