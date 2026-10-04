'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import type mapboxgl from 'mapbox-gl';
import { ENTITY_TYPE_COLORS } from '@/types/entities';
import { buildCommunityColorExpr } from '@/lib/communityColors';

// ── Types ────────────────────────────────────────────────────────────────────

export type ColorMode = 'entity_type' | 'community';

export interface LayerToggles {
    heatmap: boolean;
    arcs: boolean;
    pulse: boolean;
    colorMode: ColorMode;
}

// ── Color expressions ────────────────────────────────────────────────────────

export const ENTITY_COLOR_MATCH: mapboxgl.Expression = [
    'match',
    ['get', 'entity_type'],
    'GPE',    ENTITY_TYPE_COLORS.GPE,
    'ORG',    ENTITY_TYPE_COLORS.ORG,
    'PERSON', ENTITY_TYPE_COLORS.PERSON,
    'LOC',    ENTITY_TYPE_COLORS.LOC,
    'FAC',    ENTITY_TYPE_COLORS.FAC,
    '#888888',
];

export const COMMUNITY_COLOR_MATCH = buildCommunityColorExpr();

// ── Hook ─────────────────────────────────────────────────────────────────────

interface UseMapLayersOptions {
    mapRef: React.MutableRefObject<mapboxgl.Map | null>;
}

export function useMapLayers({ mapRef }: UseMapLayersOptions) {
    const pulseAnimRef = useRef<number | null>(null);
    const pulseState = useRef({ radius: 8, opacity: 1 });
    const arcsLoaded = useRef(false);

    const [layers, setLayers] = useState<LayerToggles>({
        heatmap: false,
        arcs: false,
        pulse: false,
        colorMode: 'entity_type',
    });

    // ── Pulse animation ──────────────────────────────────────────────────────

    const startPulse = useCallback(() => {
        const animate = () => {
            if (!mapRef.current?.getLayer('entity-pulse')) return;
            pulseState.current.opacity -= 0.015;
            pulseState.current.radius += 0.35;
            if (pulseState.current.opacity <= 0) {
                pulseState.current.opacity = 1;
                pulseState.current.radius = 8;
            }
            mapRef.current.setPaintProperty('entity-pulse', 'circle-stroke-opacity', pulseState.current.opacity);
            mapRef.current.setPaintProperty('entity-pulse', 'circle-radius', pulseState.current.radius);
            pulseAnimRef.current = requestAnimationFrame(animate);
        };
        pulseAnimRef.current = requestAnimationFrame(animate);
    }, [mapRef]);

    const stopPulse = useCallback(() => {
        if (pulseAnimRef.current !== null) {
            cancelAnimationFrame(pulseAnimRef.current);
            pulseAnimRef.current = null;
        }
        if (mapRef.current?.getLayer('entity-pulse')) {
            mapRef.current.setPaintProperty('entity-pulse', 'circle-stroke-opacity', 0.5);
            mapRef.current.setPaintProperty('entity-pulse', 'circle-radius', 8);
        }
    }, [mapRef]);

    // ── Load arcs (lazy) ─────────────────────────────────────────────────────

    const loadArcs = useCallback(async () => {
        if (!mapRef.current) return;
        try {
            const { fetchEntityArcs } = await import('@/utils/api');
            const arcsData = await fetchEntityArcs(0.3, 300);
            const source = mapRef.current.getSource('entity-arcs') as mapboxgl.GeoJSONSource;
            if (source) source.setData(arcsData);
            console.log(`✓ Loaded ${(arcsData as typeof arcsData & { arc_count?: number }).arc_count ?? 0} entity arcs`);
        } catch (error) {
            console.error('Error loading arcs:', error);
        }
    }, [mapRef]);

    // ── Sync visibility with Mapbox ──────────────────────────────────────────

    useEffect(() => {
        if (!mapRef.current) return;

        if (mapRef.current.getLayer('intel-heatmap')) {
            mapRef.current.setLayoutProperty('intel-heatmap', 'visibility', layers.heatmap ? 'visible' : 'none');
        }

        if (mapRef.current.getLayer('arc-lines')) {
            mapRef.current.setLayoutProperty('arc-lines', 'visibility', layers.arcs ? 'visible' : 'none');
            if (layers.arcs && !arcsLoaded.current) {
                arcsLoaded.current = true;
                loadArcs();
            }
        }

        if (mapRef.current.getLayer('entity-pulse')) {
            mapRef.current.setLayoutProperty('entity-pulse', 'visibility', layers.pulse ? 'visible' : 'none');
            if (layers.pulse) { startPulse(); } else { stopPulse(); }
        }

        // Community vs entity_type color mode
        const colorExpr = layers.colorMode === 'community' ? COMMUNITY_COLOR_MATCH : ENTITY_COLOR_MATCH;
        if (mapRef.current.getLayer('entity-markers')) {
            mapRef.current.setPaintProperty('entity-markers', 'circle-color', colorExpr);
        }
        if (mapRef.current.getLayer('entity-labels')) {
            mapRef.current.setPaintProperty('entity-labels', 'text-color', colorExpr);
        }
    }, [layers, mapRef, loadArcs, startPulse, stopPulse]);

    // ── Toggle handler ───────────────────────────────────────────────────────

    const toggleLayer = useCallback((key: keyof LayerToggles) => {
        setLayers(prev => {
            if (key === 'colorMode') {
                return { ...prev, colorMode: prev.colorMode === 'entity_type' ? 'community' : 'entity_type' };
            }
            return { ...prev, [key]: !prev[key as 'heatmap' | 'arcs' | 'pulse'] };
        });
    }, []);

    return { layers, toggleLayer, stopPulse, loadArcs };
}
