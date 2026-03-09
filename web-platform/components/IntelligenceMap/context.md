# IntelligenceMap Components Context

## Purpose
React/TypeScript components for the tactical intelligence map visualization. Provides the core UI elements for displaying geocoded entities on a Mapbox GL map with military-style HUD overlays.

## Architecture Role
Presentation components consumed by the `app/map/page.tsx` route. Uses dynamic import pattern with SSR disabled for Mapbox GL compatibility. Each component handles a specific visualization concern following React composition patterns.

## Key Files

- `MapLoader.tsx` - Client-side dynamic loader
  - Uses `next/dynamic` with `ssr: false`
  - Shows MapSkeleton during bundle download
  - Enables code splitting for ~500KB Mapbox bundle

- `MapSkeleton.tsx` - Loading skeleton
  - Displays while Mapbox GL JS loads
  - Shows spinner and HUD placeholders
  - Tactical grid preview

- `TacticalMap.tsx` - Main map component
  - Initializes Mapbox GL with dark military style
  - Fetches entities from API (`/api/v1/map/entities`)
  - Implements clustering for large entity counts (5000+ limit)
  - Handles entity selection on click
  - Manages map state (latitude, longitude, zoom)
  - Composes GridOverlay, HUDOverlay, and EntityDossier
  - Default center: Rome (41.9028, 12.4964)

- `GridOverlay.tsx` - Tactical grid visualization
  - CSS-based grid overlay on top of map
  - Military/tactical aesthetic grid lines
  - Corner bracket decorations
  - Scanline animation effect

- `HUDOverlay.tsx` - Head-Up Display elements
  - Real-time ZULU (UTC) clock display
  - Live coordinate tracking (lat/lng)
  - Zoom level indicator
  - System status display
  - Classification banner
  - Control hints

- `EntityDossier.tsx` - Entity detail panel
  - Displays selected entity information
  - Shows: name, type, mention count, first/last seen dates
  - Lists related articles with links
  - Slide-in panel animation (450px wide)
  - Color-coded entity types

## Loading Architecture

```
app/map/page.tsx (Server Component)
    ├── Metadata (SEO, rendered server-side)
    └── <MapLoader> (Client Component)
            └── dynamic(() => import('./TacticalMap'), { ssr: false })
                    └── <MapSkeleton> (while loading)
```

**Note**: The sibling directory `components/StorylineGraph/` follows the same dynamic import pattern (GraphLoader → StorylineGraph) for the force-directed narrative graph at `/stories`.

## Dependencies

- **Internal**: `@/utils/api` for entity fetching, `@/types/entities` for interfaces
- **External**:
  - `mapbox-gl` - Map rendering engine
  - `next/dynamic` - Dynamic imports
  - `react` - Component framework
  - `framer-motion` - Animations (dossier panel)
  - `lucide-react` - Icons

## Data Flow

- **Input**:
  - GeoJSON features from API
  - Mapbox access token from environment (`NEXT_PUBLIC_MAPBOX_TOKEN`)
  - User interactions (clicks, pan, zoom)

- **Output**:
  - Rendered map with entity markers
  - Entity clusters at low zoom
  - Individual markers at high zoom
  - Selected entity dossier panel

## Entity Marker Colors

| Type | Color | Description |
|------|-------|-------------|
| GPE | Cyan | Geopolitical entities (countries, cities) |
| LOC | Green | Locations |
| FAC | Orange | Facilities |
| ORG | Yellow | Organizations |
| PERSON | Purple | People |
| Default | Gray | Other entity types |

Colors are shared with `/lib/communityColors.ts`. Toggle "COLOR: COMM" switches to community-based coloring (same palette as StorylineGraph).

## Cluster Colors

| Point Count | Color |
|-------------|-------|
| 0-10 | Cyan (#00A8E8) |
| 10-100 | Orange (#FF6B35) |
| 100-750 | Pink (#F72585) |
| 750+ | Red (#FF0000) |

## Layer Toggles (Tier 3)

| Toggle | Layer ID | Description |
|--------|----------|-------------|
| HEATMAP | `intel-heatmap` | Heatmap weighted by `intelligence_score` |
| ARCS | `arc-lines` | LineStrings between entities sharing storylines (lazy-fetched from `/api/v1/map/arcs`) |
| PULSE | `entity-pulse` | Animated ring on entities with `hours_ago < 48` |
| COLOR: COMM | `entity-markers` | Community color mode (primary_community_id % 15) |

## GeoJSON Feature Properties (enriched)

Beyond basic `id, name, entity_type, mention_count, first_seen, last_seen`:
- `intelligence_score` (0–1): composite signal significance score
- `storyline_count`: number of linked active storylines
- `top_storyline`: title of highest-momentum storyline
- `primary_community_id`: community_id from highest-momentum storyline
- `hours_ago`: hours since last_seen (used for pulse filter)
