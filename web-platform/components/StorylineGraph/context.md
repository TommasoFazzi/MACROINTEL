# StorylineGraph Components Context

## Purpose
React/TypeScript components for the WebGL narrative storyline graph visualization. Renders the network of active intelligence storylines and their inter-connections (TF-IDF weighted Jaccard edges from the Narrative Engine) using **Sigma.js** (WebGL) + **graphology** + **ForceAtlas2** in a web worker. Supports **community coloring** (Louvain clusters), **ego network** exploration, **momentum-based filtering**, and convex hull community overlays.

## Architecture Role
Presentation components consumed by `app/stories/page.tsx`. Uses the same dynamic import / SSR-disabled pattern as before, because Sigma.js requires the browser WebGL API. The component tree is split into small focused components following the `@react-sigma/core v5` composition model.

## Key Files

### `GraphLoader.tsx` — Client-side dynamic loader
- Marked `'use client'`
- Uses `next/dynamic` with `{ ssr: false, loading: () => <GraphSkeleton /> }`
- Unchanged from previous implementation

### `GraphSkeleton.tsx` — Loading skeleton
- Unchanged from previous implementation

### `GraphContext.tsx` — Shared React context
- `FilterState`: `{ momentumMin, isolate, highlightIds: Set<number> }`
- `GraphContextValue`:
  - `selectedId: number | null` — selected node (React state, drives ego mode)
  - `setSelectedId: Dispatch<SetStateAction<number | null>>`
  - `hoveredNodeRef: MutableRefObject<number | null>` — **useRef, not state** (hover fires 50+/sec; keeping it out of React tree avoids cascading nodeReducer runs)
  - `egoNeighborIds: Set<number>` — `useMemo`-stabilised, changes only when `selectedId` changes
  - `filterState: FilterState` — `useMemo`-stabilised
  - `communityColorMap: Map<number, string>` — rank-based, recomputed on new API data

### `GraphDataLoader.tsx` — Graph builder (inside `<SigmaContainer>`)
- Calls `useSigma()` → `sigma.getGraph()` to access the stable graphology Graph
- On each `graphData` change (SWR 60s poll): `graph.clear()` + full in-place rebuild
- **Restores saved node positions** from `localStorage` (`story-graph-layout-v4` — bump the version to force a re-layout after changing FA2 params) before adding nodes — eliminates "explosion" animation on reload. Hash = node-count:edge-count:FNV-1a(sorted ids)
- Builds nodes with: `label`, `size` (4 + momentum × 12), `color` (from `communityColorMap`), `x/y` (restored or random ±500)
- Builds edges with: `weight`, `size` (0.4 + weight × 1.4), `color` (`rgba(150,190,220,…)`, alpha 0.04 + weight × 0.10 — kept faint so the graph doesn't read as a bright tangle)
- **FA2 worker** — tuned to **spread** the connected core without flinging isolated nodes into an empty halo: `barnesHutOptimize: true`, `barnesHutTheta: 0.6`, `scalingRatio: 14`, `strongGravityMode: false`, `gravity: 0.18` (compacts isolated "lone star" nodes, which have no edges holding them), `outboundAttractionDistribution: true` (pushes hubs apart), `edgeWeightInfluence: 0.5`, `slowDown: 5`
  - **Fallback**: `assign(graph, { iterations: 300 })` sync (same spread settings) if worker path fails in Next.js build
- Stops worker after **12s**, saves positions to `localStorage`, calls `onOptimizing(false)`
- **Edge density**: `useGraphNetwork` requests `?min_edge_weight=0.18` (API default 0.10 returned ~18k edges / 11.9 per node → hairball). 0.18 keeps meaningful TF-IDF links; lone high-momentum nodes survive via the server's "lone stars" rule
- Calls `onLayoutReady()` after **500ms** (show graph immediately without waiting for full convergence)
- Cleanup: `fa2.kill()` on unmount/route change

### `useScheduledRefresh.ts` — Shared rAF-batched refresh hook
Single source of truth for `scheduleRefresh()`, used by both `GraphEvents` and `GraphStyle` (was duplicated in each). Coalesces refresh requests into at most one `sigma.refresh()` per animation frame.

### `useGraphFilters.ts` — Filter business logic (no UI)
Owns all filter state (`minMomentum`, `selectedTicker`, `selectedEntities`, `entityQuery`, `titleQuery`, `filterIsolate`, `legendExpanded`, `showNew`) and derivations: `communityColorMap` (rank-based), `communityLabels`/`othersCount`/`othersNodes`, `tickerHighlightIds`, `entityHighlightIds`, `newHighlightIds`, `filterState`, and `entitySuggestions`. Keeps `StorylineGraph.tsx` focused on layout/rendering. Takes `graphData`, returns state + setters + derived values.
- **`showNew` ("Novità")**: highlights `narrative_status === 'emerging'` storylines. No graph history exists in the DB (storylines store only current state), so "what's new" is derived from the current `narrative_status` field — no temporal snapshots involved.
- `filterState.highlightIds` priority: ticker → entity → `newHighlightIds` (first non-empty wins).

### `GraphEvents.tsx` — Sigma event handlers (inside `<SigmaContainer>`)
- `clickNode` → functional toggle: `setSelectedId(prev => prev === id ? null : id)`
- `enterNode` / `leaveNode` → **`hoveredNodeRef.current` mutation + `onHoverNode(...)` + `scheduleRefresh()`**
  - `onHoverNode({ id, x, y })` drives the parent's hover tooltip (`event.x`/`event.y` = viewport px). Fires once per enter/leave, not per mouse-move.
  - `scheduleRefresh()` from `useScheduledRefresh`
- `clickStage` → `setSelectedId(null)`

### `GraphStyle.tsx` — Node/edge reducers (inside `<SigmaContainer>`)
Sets Sigma's `nodeReducer` and `edgeReducer` via `sigma.setSettings()` whenever `selectedId`, `egoNeighborIds`, `filterState`, or `communityColorMap` changes. Neutral paths return `data` directly (no per-node object clone). `withAlpha(hex, 0..1)` helper appends an 8-bit hex alpha.

**nodeReducer priority (high → low):**
1. `momentum < momentumMin` → `hidden: true`
2. `isolate && !highlightIds.has(id)` → `hidden: true`
3. `!isolate && highlightIds active && !highlighted` → alpha 0.08, size × 0.7
4. `egoActive && !isNeighbor` → alpha 0.05, size × 0.6; ego neighbor → scaled by edge weight
5. `id === selectedId` → `color: '#FFFFFF', highlighted: true`
6. **Hover focus** (`hoveredNodeRef.current != null && !egoActive`): hovered → `highlighted`; direct neighbor (`graph.hasEdge`) → full color; everything else → alpha 0.18

**edgeReducer (ego mode OR hover):**
- Hover (not ego): edges incident to hovered node → orange `0.4 + w·0.6` opacity, size `1 + w·4`; others faded `rgba(150,190,220,0.02)`
- Ego edge (both endpoints in egoNeighborIds): orange `0.35 + w·0.65`, size `1 + w·4`; non-ego faded
- Neutral path (no ego, no hover): returns `data` unchanged

Calls `scheduleRefresh()` after settings update.

### `CommunityOverlay.tsx` — SVG hull + labels (outside `<SigmaContainer>`)
- Subscribes to `sigma.on('afterRender', scheduleRender)`
- **rAF throttle with trailing edge** (`requestAnimationFrame`, one redraw per frame, final frame never dropped) — fixes hulls staying misaligned after a pan/zoom burst
- **Reads node positions FRESH each frame** (`graph.getNodeAttribute(id, 'x'/'y')`) — node ids are bucketed by community once, but coords are re-read every render so hulls follow the FA2 layout animation (previously coords were captured once at mount → frozen hulls)
- **DOM mutation directly** on `svgRef.current` — no React `setState`
- For each community with 3+ nodes:
  - Converts graph coords → screen: `sigma.graphToViewport({ x, y })`
  - Computes **Jarvis March convex hull** (O(nh)) on screen points
  - Pads hull outward by 18px from centroid
  - Renders `<polygon>`: fill 6% opacity, dashed stroke 18% opacity
  - Renders `<text>` at centroid: 13px bold sans-serif, 28% opacity, uppercase
- Positioned `absolute inset-0 w-full h-full pointer-events-none`

### `SigmaRefBridge` — Inline component in `StorylineGraph.tsx`
Bridges the Sigma instance from inside `<SigmaContainer>` (where `useSigma()` works) to a `sigmaRef` in the parent, enabling camera controls and `CommunityOverlay` to access it.
```ts
function SigmaRefBridge({ sigmaRef }) {
  const sigma = useSigma();
  useEffect(() => { sigmaRef.current = sigma; return () => { sigmaRef.current = null; }; }, [sigma, sigmaRef]);
  return null;
}
```

### `StorylineGraph.tsx` — Root orchestrator
- Stable graphology graph: `useRef(new Graph({ type: 'undirected', multi: false })).current` — **never recreated**, SigmaContainer holds the WebGL context for the session
- `sigmaRef: MutableRefObject<Sigma | null>` — populated by `SigmaRefBridge`
- State: `selectedId`, `layoutReady`, `optimizing`; filter state lives in `useGraphFilters(graphData)` (see above)
- `hoveredNodeRef: MutableRefObject<number | null>` — not React state
- `hoverInfo: HoverInfo | null` — React state for the tooltip (updated only on enter/leave via `onHoverNode`); `nodeById` map gives O(1) lookup of the full node payload
- `egoNeighborIds: Set<number>` — `useMemo` on `useEgoNetwork(selectedId, 0.05)`
- Hover tooltip: absolutely-positioned `pointer-events-none` div at `hoverInfo.x/y` showing title, status, momentum, article count, top entities
- Camera API:
  - `zoomToFit()` → `sigma.getCamera().animatedReset({ duration: 400 })`
  - `zoomBy(factor)` → `camera.animate({ ratio: camera.ratio * factor })` — backs the +/− zoom buttons
  - `handleNavigate(id)` → reads node attrs, `sigma.getCamera().animate({ x, y, ratio: 0.3 }, { duration: 500 })`
  - Ticker zoom → bounding box of highlighted nodes, `ratio = min(1, span/400)`
  - Deep-link `?highlight=<id>` → `handleNavigate` after `layoutReady` + 600ms delay
- **`SIGMA_SETTINGS`** (stable object, defined outside component):
  ```ts
  {
    renderLabels: true,
    labelFont: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    labelSize: 11,
    labelColor: { color: '#C8D4E0' },
    labelRenderedSizeThreshold: 10,   // LOD: labels only for larger nodes
    labelGridCellSize: 250,           // bigger grid + low density → far fewer overlapping labels
    labelDensity: 0.25,
    hideLabelsOnMove: true,
    defaultDrawNodeHover: () => {},   // disable Sigma's white hover-label box (white-on-white); React tooltip replaces it
    defaultEdgeColor: 'rgba(150,190,220,0.06)',
    defaultNodeColor: '#00A8E8',
    minCameraRatio: 0.05,
    maxCameraRatio: 5,
    enableEdgeEvents: false,
    hideEdgesOnMove: true,
  }
  ```
- HUD shows **"OPTIMIZING LAYOUT…"** badge while FA2 worker is running
- HUD **always-visible KEY legend** (size = momentum, color = theme, ● = new < 3d) — answers the first-glance questions without opening a menu (Ramo A: panoramica leggibile)
- Filter panel **"Novità (emerging)"** toggle → `setShowNew` → dims/highlights emerging storylines via `filterState`
- **Navigation trail breadcrumb** (`navHistory: number[]`, top-center, desktop): the chain of storylines visited node→neighbor→neighbor. Derived from `selectedId` changes — re-visiting a node truncates the trail back to it; clearing the selection resets it. Crumbs call `handleNavigate(id)`. Lets the analyst back-track without losing the starting point (Ramo B: indagine concatenata)

### `StorylineDossier.tsx` — Detail side panel
Props: `{ storylineId, onClose, onNavigate, getSharedEntities? }`. `onNavigate` calls `handleNavigate(id)` in the parent. `getSharedEntities(relatedId)` returns the entities shared between the open storyline and a connected one — rendered as cyan chips under each "Connected Storylines" row ("↔ shared: …") to answer *why* two storylines are linked.

**Shared-entities ("why connected?")** — the edge `weight` is a TF-IDF weighted Jaccard over `key_entities`, and `storyline_edges` does NOT store the overlap. So the connection reason is recomputed **client-side** in `StorylineGraph.getSharedEntities(otherId)`: intersect `nodeById[selectedId].key_entities ∩ nodeById[otherId].key_entities` (case-insensitive). Zero API/edge-column changes. Surfaced in two places:
- Hover tooltip: when a node is selected and you hover one of its neighbors → "↔ shared: a · b"
- Dossier "Connected Storylines" rows → cyan chips

## Component Tree

```
app/stories/page.tsx  (Server Component)
    └── <GraphLoader>  ('use client', SSR disabled)
            └── <StorylineGraph>  (root state owner)
                    ├── GraphContext.Provider
                    ├── <SigmaContainer graph={sigmaGraph} settings={SIGMA_SETTINGS}>
                    │       ├── <SigmaRefBridge />         — sigma instance → sigmaRef
                    │       ├── <GraphDataLoader />         — build graph + FA2 worker
                    │       ├── <GraphEvents />             — click/hover → state/ref
                    │       └── <GraphStyle />              — nodeReducer + edgeReducer
                    ├── <CommunityOverlay />               — SVG hull + labels
                    ├── HUD panel (top-left)
                    ├── Filter panel (top-right)
                    └── <StorylineDossier />               — unchanged
```

## Data Flow

```
useGraphNetwork()
    └── SWR GET /api/proxy/stories/graph (60s poll)
            └── GraphDataLoader
                    ├── graph.clear() + rebuild in-place
                    └── FA2 worker (8s) → positions saved to localStorage
                            └── sigma WebGL renders graph
                                    ├── GraphStyle (nodeReducer/edgeReducer)
                                    │       ├── momentum filter → hidden
                                    │       ├── entity/ticker filter → dim/hide
                                    │       ├── ego mode → dim non-neighbors
                                    │       └── selection → white highlight
                                    ├── CommunityOverlay (afterRender, 30fps)
                                    │       └── convex hull SVG per community
                                    └── node click → setSelectedId(id)
                                            ├── useEgoNetwork(id, 0.05)
                                            └── useStorylineDetail(id)
                                                    └── StorylineDossier panel
```

## Performance Patterns

| Pattern | Why |
|---------|-----|
| `hoveredNodeRef` (useRef) for reducers | nodeReducer reads hover live without a React render; `hoverInfo` state (tooltip) updates only once per enter/leave |
| `scheduleRefresh()` (rAF batch, `useScheduledRefresh`) | max one `sigma.refresh()` per frame even with rapid hover events; shared by GraphEvents + GraphStyle |
| nodeReducer/edgeReducer neutral path returns `data` | no per-node object clone on the common path — less GC pressure on large graphs |
| `CommunityOverlay` DOM mutation | React reconciler at 60fps is the bottleneck — direct SVG DOM avoids it |
| `CommunityOverlay` rAF throttle (trailing) | one hull redraw per frame; trailing frame kept so hulls settle correctly after pan/zoom |
| `localStorage` layout hash (FNV-1a over node ids) | counts-only hash collided on equal-size swaps → wrong restored positions; id signature prevents it |
| `hideEdgesOnMove: true` | Hides all 17854 edges during pan/zoom for smooth camera movement |
| `hideLabelsOnMove: true` | No label render during movement |
| `labelRenderedSizeThreshold: 8` | LOD: only nodes with size ≥ 8 (momentum ≥ 0.5) show labels |
| `localStorage` layout save | Avoids "explosion" animation on reload |
| Stable graphology graph ref | SigmaContainer never reinitializes WebGL context on SWR polls |

## Dependencies

- **Internal**: `@/hooks/useStories`, `@/types/stories`, `@/lib/communityColors`
- **External**:
  - `@react-sigma/core@5.0.6` — React bindings for Sigma.js v3
  - `sigma@3.0.3` — WebGL graph renderer
  - `graphology@0.26.0` — Graph data structure
  - `graphology-layout-forceatlas2@0.10.1` — FA2 layout (worker + sync)
  - `next/dynamic` — Dynamic import with SSR control

## Color Reference

Same `COMMUNITY_PALETTE` and `COMMUNITY_OTHER` as before (see `lib/communityColors.ts`).
Top 15 communities by node count get `COMMUNITY_PALETTE[rank]`; all others get `COMMUNITY_OTHER = '#2A3A4A'`.
Selected node color: `#FFFFFF`. Ego edges: `rgba(249,115,22,0.9)` (orange).
