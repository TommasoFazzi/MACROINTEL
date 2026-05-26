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
- **Restores saved node positions** from `localStorage` (`story-graph-layout-v1`) before adding nodes — eliminates "explosion" animation on reload
- Builds nodes with: `label`, `size` (4 + momentum × 12), `color` (from `communityColorMap`), `x/y` (restored or random ±500)
- Builds edges with: `weight`, `size` (0.5 + weight × 2.0), `color` (`rgba(150,190,220,...)`)
- **FA2 worker** (`graphology-layout-forceatlas2/worker`): `barnesHutOptimize: true`, `scalingRatio: 2`, `strongGravityMode: true`, `gravity: 0.05`, `slowDown: 10`
  - **Fallback**: `assign(graph, { iterations: 150 })` sync if worker path fails in Next.js build
- Stops worker after **8s**, saves positions to `localStorage`, calls `onOptimizing(false)`
- Calls `onLayoutReady()` after **500ms** (show graph immediately without waiting for full convergence)
- Cleanup: `fa2.kill()` on unmount/route change

### `GraphEvents.tsx` — Sigma event handlers (inside `<SigmaContainer>`)
- `clickNode` → functional toggle: `setSelectedId(prev => prev === id ? null : id)`
- `enterNode` / `leaveNode` → **`hoveredNodeRef.current` mutation + `scheduleRefresh()`**
  - `scheduleRefresh()`: rAF-batched — at most one `sigma.refresh()` per animation frame
- `clickStage` → `setSelectedId(null)`

### `GraphStyle.tsx` — Node/edge reducers (inside `<SigmaContainer>`)
Sets Sigma's `nodeReducer` and `edgeReducer` via `sigma.setSettings()` whenever `selectedId`, `egoNeighborIds`, `filterState`, or `communityColorMap` changes.

**nodeReducer priority (high → low):**
1. `momentum < momentumMin` → `hidden: true`
2. `isolate && !highlightIds.has(id)` → `hidden: true`
3. `!isolate && highlightIds active && !highlighted` → color + `'14'` alpha, size × 0.7
4. `egoActive && !isNeighbor` → color + `'0D'` alpha (~5%), size × 0.8
5. `id === selectedId` → `color: '#FFFFFF', highlighted: true`
6. `id === hoveredNodeRef.current` → `highlighted: true`

**edgeReducer (ego mode only):**
- Ego edge (both endpoints in egoNeighborIds): `rgba(249,115,22,0.9)`, size 3.0
- Non-ego edge: `rgba(150,190,220,0.03)`, size 0.3

Calls `scheduleRefresh()` (rAF-batched) after settings update.

### `CommunityOverlay.tsx` — SVG hull + labels (outside `<SigmaContainer>`)
- Subscribes to `sigma.on('afterRender', updatePositions)`
- **Throttled to ~30fps** (`Date.now()` gap ≥ 33ms) — no need to recompute hull at 60fps
- **DOM mutation directly** on `svgRef.current` — no React `setState` (React reconciler is the bottleneck at frame rate)
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
- State: `selectedId`, `layoutReady`, `optimizing`, filter states
- `hoveredNodeRef: MutableRefObject<number | null>` — not React state
- `egoNeighborIds: Set<number>` — `useMemo` on `useEgoNetwork(selectedId, 0.05)`
- `communityColorMap` — rank-based (top 15 by count → `COMMUNITY_PALETTE`, rest → `COMMUNITY_OTHER`)
- `filterState` — `useMemo` combining momentum, entity, ticker highlights
- Camera API:
  - `zoomToFit()` → `sigma.getCamera().animatedReset({ duration: 400 })`
  - `handleNavigate(id)` → reads node attrs, `sigma.getCamera().animate({ x, y, ratio: 0.3 }, { duration: 500 })`
  - Ticker zoom → bounding box of highlighted nodes, `ratio = min(1, span/400)`
  - Deep-link `?highlight=<id>` → `handleNavigate` after `layoutReady` + 600ms delay
- **`SIGMA_SETTINGS`** (stable object, defined outside component):
  ```ts
  {
    renderLabels: true,
    labelFont: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    labelSize: 12,
    labelColor: { color: '#E0E0E0' },
    labelRenderedSizeThreshold: 8,  // LOD: labels only for nodes size >= 8
    hideLabelsOnMove: true,
    defaultEdgeColor: 'rgba(150,190,220,0.08)',
    defaultNodeColor: '#00A8E8',
    minCameraRatio: 0.05,
    maxCameraRatio: 5,
    enableEdgeEvents: false,
    hideEdgesOnMove: true,       // critical for 17854 edges
  }
  ```
- HUD shows **"OPTIMIZING LAYOUT…"** badge while FA2 worker is running

### `StorylineDossier.tsx` — Detail side panel
Unchanged. Props: `{ storylineId, onClose, onNavigate }`. `onNavigate` calls `handleNavigate(id)` in the parent.

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
| `hoveredNodeRef` (useRef) | hover fires 50+/sec — React state would cascade 1542-node reducer runs |
| `scheduleRefresh()` (rAF batch) | max one `sigma.refresh()` per frame even with rapid hover events |
| `CommunityOverlay` DOM mutation | React reconciler at 60fps is the bottleneck — direct SVG DOM avoids it |
| `afterRender` throttle 30fps | Hull coords don't need 60fps precision |
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
