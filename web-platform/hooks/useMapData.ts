'use client';

import { useCallback, useRef, useState } from 'react';
import type mapboxgl from 'mapbox-gl';
import type { EntityFilters } from '@/utils/api';
import type { Article, EntityCollection, EntityStoryline, MapStats } from '@/types/entities';

// ── Types ────────────────────────────────────────────────────────────────────

export interface EntityData {
    id: number;
    name: string;
    entity_type: string;
    latitude: number;
    longitude: number;
    mention_count: number;
    first_seen: string;
    last_seen: string;
    metadata: Record<string, unknown>;
    related_articles: Article[];
    related_storylines?: EntityStoryline[];
}

// ── Hook ─────────────────────────────────────────────────────────────────────

interface UseMapDataOptions {
    mapRef: React.MutableRefObject<mapboxgl.Map | null>;
    addSourceAndLayers: (data: EntityCollection) => void;
}

export function useMapData({ mapRef, addSourceAndLayers }: UseMapDataOptions) {
    const [entityCount, setEntityCount] = useState({ filtered: 0, total: 0 });
    const [mapStats, setMapStats] = useState<MapStats | null>(null);
    const [selectedEntity, setSelectedEntity] = useState<EntityData | null>(null);
    const entityDataRef = useRef<EntityCollection | null>(null);

    const loadEntities = useCallback(async (filters: EntityFilters = {}) => {
        if (!mapRef.current) return;
        try {
            const { fetchEntities } = await import('@/utils/api');
            const entityData: EntityCollection = await fetchEntities(filters);
            entityDataRef.current = entityData;
            setEntityCount({ filtered: entityData.filtered_count, total: entityData.total_count });
            console.log(`✓ Loaded ${entityData.features.length} entities`);

            const source = mapRef.current.getSource('entities') as mapboxgl.GeoJSONSource;
            if (source) {
                source.setData(entityData);
            } else {
                addSourceAndLayers(entityData);
            }
        } catch (error) {
            console.error('Error loading entities:', error);
        }
    }, [mapRef, addSourceAndLayers]);

    const loadStats = useCallback(async () => {
        try {
            const { fetchMapStats } = await import('@/utils/api');
            setMapStats(await fetchMapStats());
        } catch (error) {
            console.error('Error loading map stats:', error);
        }
    }, []);

    return { entityCount, mapStats, selectedEntity, setSelectedEntity, loadEntities, loadStats, entityDataRef };
}
