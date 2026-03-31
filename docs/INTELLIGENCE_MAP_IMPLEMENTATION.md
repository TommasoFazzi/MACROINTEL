# Intelligence Map - Implementation Guide

## 🎯 Project Overview

**Goal:** Create a Call of Duty-style tactical intelligence map with cinematic interactions, entity-based visualization, and real-time narrative data from PostgreSQL/PostGIS.

**Tech Stack:**
- **Frontend:** Next.js (App Router), React 19, TypeScript
- **Map Engine:** Mapbox GL JS (`react-map-gl` wrapper not used, raw mapboxgl used for fine-grained performance)
- **Animations:** Framer Motion
- **Backend:** FastAPI (Python)
- **Database:** PostgreSQL 17 + pgvector + PostGIS
- **Geocoding:** Hybrid Pipeline (GeoNames local DB + Gemini Flash for disambiguation + Photon fallback)

---

## 🏗️ Architecture & Core Components

### 1. The Map Engine (`TacticalMap.tsx`)
The map is built on a dark military style (`mapbox://styles/mapbox/dark-v11`) and uses a custom React implementation managing raw `mapboxgl` instances to optimize GeoJSON source updates.

It relies on a layered architecture (Tier 3 layers):

- **Heatmap Layer (`HEATMAP`)**: Density is weighted by the `intelligence_score` of entities. Areas with higher narrative activity and stronger macro signals burn brighter (orange/pink).
- **Arc Lines (`ARCS`)**: Cyan arcs connect entities that share active storylines. Line width scales with the number of shared storylines; opacity scales with the maximum `momentum_score`.
- **Pulse Indicator (`PULSE`)**: A green pulsing ring that highlights entities mentioned in articles published within the last 48 hours, signaling fresh intelligence.
- **Color Modes (`COLOR: TYPE` / `COLOR: COMM`)**: Dynamic coloring. Nodes can be colored by their entity type (GeoPolitical, Organization, Person) or by their assigned Louvain narrative community (matching the Storyline Graph colors).
- **Storyline Highlight**: Cross-navigation from the Narrative Graph. Clicking "View on Map" isolates and highlights all entities within that specific storyline, dimming the rest and showing a gold banner.

### 2. Frontend UI Overlays
- **`HUDOverlay.tsx`**: A tactical Heads-Up Display showing live map coordinates, zoom level, total entity counts, and intelligence stats.
- **`EntityDossier.tsx`**: A side-panel that pops open when an entity marker is clicked. It fetches detailed intelligence (scores, recent articles, top storylines) from the `/api/v1/map/entities/{id}` endpoint.
- **`FilterPanel.tsx`**: Allows live filtering of the map features based on entity type, date range, or score thresholds.
- **`GridOverlay.tsx`**: Provides the cinematic, tactical aesthetic via a subtle CSS grid pattern layered over the map.

---

## 🗺️ Geographic Entity Extraction

### Hybrid Geocoding Service (`scripts/geocode_geonames.py`)

To ensure high accuracy and overcome API rate limits, the system uses a 4-step geocoding pipeline rather than basic Nominatim:

1. **GeoNames Lookup**: Exact/ascii/alternate name matching against a local `geo_gazetteer` table (populated by `scripts/load_geonames.py`).
2. **LLM Disambiguation (Gemini 2.0 Flash)**: If GeoNames returns multiple candidates (e.g., "Paris" in France vs. Texas), Gemini uses the narrative context to resolve the correct location.
3. **Filtered GeoNames Lookup**: Re-queries the local database with the LLM's spatial constraints.
4. **Photon Fallback**: For hyper-specific or edge-case locations not available in the local gazetteer, it falls back to a self-hosted Photon instance (or `komoot.io`).

### Database Spatial Integration
The database leverages **PostGIS** for spatial indexing, allowing fast bounding-box queries and geographic radius searches:

```sql
-- Spatial columns built into the entities/gazetteer infrastructure
ALTER TABLE entities 
ADD COLUMN IF NOT EXISTS geom geometry(Point, 4326);

-- PostGIS spatial index
CREATE INDEX IF NOT EXISTS idx_entities_geom 
ON entities USING GIST(geom);
```

---

## 🚦 Data Flow & State Management

The frontend uses specialized SWR hooks to manage state without blocking the UI:

- `useMapData`: Fetches GeoJSON entities and arcs from the FastAPI backend. Implements a unified `addSourceAndLayers` callback to inject data into Mapbox GL efficiently.
- `useMapLayers`: Manages the state of the toggles (HEATMAP, ARCS, PULSE, Color Mode).
- `useMapPosition`: Persists coordinates and zoom levels across route changes.

## 🚀 Performance Considerations

- **Clustering**: Entity markers are clustered (`cluster: true`) natively via Mapbox GL to handle thousands of items without dropping frames. Clusters break apart smoothly at higher zoom levels.
- **Property-Driven UI**: Storyline highlighting (`_hl: 'on' | 'off'`) is applied via GeoJSON properties to leverage Mapbox's WebGL renderer, avoiding heavy React re-renders.
- **Entity Mentions Bridge**: The `intelligence_score` is aggregated via the `mv_entity_storyline_bridge` materialized view to prevent expensive JOINs during map load.
