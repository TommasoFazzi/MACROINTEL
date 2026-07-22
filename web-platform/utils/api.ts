/**
 * API Client for Intelligence Map
 *
 * Fetches entity data via Next.js server-side proxy (no API keys in browser)
 */

// Explicit import rather than the ambient `GeoJSON.*` global: that global used to arrive
// transitively through @types/mapbox-gl, and the 3.16 → 3.26 bump stopped providing it,
// which broke `next build`'s type check. @types/geojson is now a direct devDependency so
// the type no longer depends on another package's transitive types.
import type { FeatureCollection } from 'geojson';
import type { EntityCollection, EntityDetails, MapStats } from '../types/entities';

export interface EntityFilters {
  limit?: number;
  entity_type?: string;   // comma-separated: "GPE,ORG"
  days?: number;           // only entities seen in last N days
  min_mentions?: number;
  min_score?: number;      // minimum intelligence_score (0–1)
  search?: string;
}

/**
 * Fetch entities with optional filters in GeoJSON format
 */
export async function fetchEntities(
  filters: EntityFilters = {}
): Promise<EntityCollection> {
  const params = new URLSearchParams();

  params.set('limit', String(filters.limit ?? 5000));
  if (filters.entity_type) params.set('entity_type', filters.entity_type);
  if (filters.days) params.set('days', String(filters.days));
  if (filters.min_mentions) params.set('min_mentions', String(filters.min_mentions));
  if (filters.min_score != null) params.set('min_score', String(filters.min_score));
  if (filters.search) params.set('search', filters.search);

  const response = await fetch(`/api/proxy/map/entities?${params.toString()}`);

  if (!response.ok) {
    throw new Error(`Failed to fetch entities: ${response.statusText}`);
  }

  return response.json();
}

/**
 * Fetch single entity details with related articles AND storylines
 */
export async function fetchEntityDetails(entityId: number): Promise<EntityDetails> {
  const response = await fetch(`/api/proxy/map/entities/${entityId}`);

  if (!response.ok) {
    throw new Error(`Failed to fetch entity ${entityId}: ${response.statusText}`);
  }

  return response.json();
}

/**
 * Fetch entity arc connections (entities sharing storylines) as GeoJSON LineStrings
 */
export async function fetchEntityArcs(minScore = 0.3, limit = 300): Promise<FeatureCollection> {
  const params = new URLSearchParams({ min_score: String(minScore), limit: String(limit) });
  const response = await fetch(`/api/proxy/map/arcs?${params.toString()}`);

  if (!response.ok) {
    throw new Error(`Failed to fetch entity arcs: ${response.statusText}`);
  }

  return response.json();
}

/**
 * Fetch entity IDs linked to a specific storyline (cross-filter: graph → map)
 */
export interface StorylineEntities {
  storyline_id: number;
  storyline_title: string;
  entity_ids: number[];
  entity_count: number;
}

export async function fetchEntitiesByStoryline(storylineId: number): Promise<StorylineEntities> {
  const response = await fetch(`/api/proxy/map/entities/by-storyline/${storylineId}`);

  if (!response.ok) {
    throw new Error(`Failed to fetch entities for storyline ${storylineId}: ${response.statusText}`);
  }

  return response.json();
}

/**
 * Fetch map stats for HUD overlay
 */
export async function fetchMapStats(): Promise<MapStats> {
  const response = await fetch('/api/proxy/map/stats');

  if (!response.ok) {
    throw new Error(`Failed to fetch map stats: ${response.statusText}`);
  }

  return response.json();
}
