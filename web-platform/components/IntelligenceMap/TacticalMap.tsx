'use client';

import { useEffect, useRef, useState, useCallback } from 'react';
import mapboxgl from 'mapbox-gl';
import 'mapbox-gl/dist/mapbox-gl.css';
import GridOverlay from './GridOverlay';
import HUDOverlay from './HUDOverlay';
import EntityDossier from './EntityDossier';
import FilterPanel from './FilterPanel';
import { ENTITY_TYPE_COLORS } from '@/types/entities';
import { COMMUNITY_PALETTE, COMMUNITY_OTHER } from '@/lib/communityColors';
import type { EntityFilters } from '@/utils/api';
import type { EntityCollection, MapStats } from '@/types/entities';

interface EntityData {
    id: number;
    name: string;
    entity_type: string;
    latitude: number;
    longitude: number;
    mention_count: number;
    first_seen: string;
    last_seen: string;
    metadata: Record<string, any>;
    related_articles: any[];
    related_storylines: any[];
}

type ColorMode = 'entity_type' | 'community';

interface LayerToggles {
    heatmap: boolean;
    arcs: boolean;
    pulse: boolean;
    colorMode: ColorMode;
}

// Color expression: maps entity_type → hex color
const ENTITY_COLOR_MATCH: any[] = [
    'match',
    ['get', 'entity_type'],
    'GPE',    ENTITY_TYPE_COLORS.GPE,
    'ORG',    ENTITY_TYPE_COLORS.ORG,
    'PERSON', ENTITY_TYPE_COLORS.PERSON,
    'LOC',    ENTITY_TYPE_COLORS.LOC,
    'FAC',    ENTITY_TYPE_COLORS.FAC,
    '#888888',
];

// Community color expression: maps primary_community_id % palette_length → color.
// Deterministic and sync with StorylineGraph palette.
const buildCommunityColorExpr = (): any[] => {
    const n = COMMUNITY_PALETTE.length;
    const expr: any[] = ['match', ['%', ['coalesce', ['get', 'primary_community_id'], -1], n]];
    for (let i = 0; i < n; i++) {
        expr.push(i, COMMUNITY_PALETTE[i]);
    }
    expr.push(COMMUNITY_OTHER);
    return expr;
};

const COMMUNITY_COLOR_MATCH = buildCommunityColorExpr();

export default function TacticalMap() {
    const mapContainer = useRef<HTMLDivElement>(null);
    const map = useRef<mapboxgl.Map | null>(null);
    const popup = useRef<mapboxgl.Popup | null>(null);
    const pulseAnimRef = useRef<number | null>(null);
    const pulseState = useRef({ radius: 8, opacity: 1 });
    const arcsLoaded = useRef(false);

    const [mapState, setMapState] = useState({ latitude: 41.9028, longitude: 12.4964, zoom: 3 });
    const [selectedEntity, setSelectedEntity] = useState<EntityData | null>(null);
    const [entityCount, setEntityCount] = useState({ filtered: 0, total: 0 });
    const [mapStats, setMapStats] = useState<MapStats | null>(null);
    const [layers, setLayers] = useState<LayerToggles>({
        heatmap: false,
        arcs: false,
        pulse: false,
        colorMode: 'entity_type',
    });

    // -----------------------------------------------------------------------
    // 3c — Pulse animation (requestAnimationFrame)
    // -----------------------------------------------------------------------
    const startPulse = useCallback(() => {
        const animate = () => {
            if (!map.current?.getLayer('entity-pulse')) return;
            pulseState.current.opacity -= 0.015;
            pulseState.current.radius += 0.35;
            if (pulseState.current.opacity <= 0) {
                pulseState.current.opacity = 1;
                pulseState.current.radius = 8;
            }
            map.current.setPaintProperty('entity-pulse', 'circle-stroke-opacity', pulseState.current.opacity);
            map.current.setPaintProperty('entity-pulse', 'circle-radius', pulseState.current.radius);
            pulseAnimRef.current = requestAnimationFrame(animate);
        };
        pulseAnimRef.current = requestAnimationFrame(animate);
    }, []);

    const stopPulse = useCallback(() => {
        if (pulseAnimRef.current !== null) {
            cancelAnimationFrame(pulseAnimRef.current);
            pulseAnimRef.current = null;
        }
        if (map.current?.getLayer('entity-pulse')) {
            map.current.setPaintProperty('entity-pulse', 'circle-stroke-opacity', 0.5);
            map.current.setPaintProperty('entity-pulse', 'circle-radius', 8);
        }
    }, []);

    // -----------------------------------------------------------------------
    // 3b — Load arcs (lazy, on first toggle-on)
    // -----------------------------------------------------------------------
    const loadArcs = useCallback(async () => {
        if (!map.current) return;
        try {
            const { fetchEntityArcs } = await import('@/utils/api');
            const arcsData = await fetchEntityArcs(0.3, 300);
            const source = map.current.getSource('entity-arcs') as mapboxgl.GeoJSONSource;
            if (source) source.setData(arcsData as any);
            console.log(`✓ Loaded ${(arcsData as any).arc_count ?? 0} entity arcs`);
        } catch (error) {
            console.error('Error loading arcs:', error);
        }
    }, []);

    // -----------------------------------------------------------------------
    // Layer visibility — react to toggle state changes
    // -----------------------------------------------------------------------
    useEffect(() => {
        if (!map.current) return;

        if (map.current.getLayer('intel-heatmap')) {
            map.current.setLayoutProperty('intel-heatmap', 'visibility', layers.heatmap ? 'visible' : 'none');
        }

        if (map.current.getLayer('arc-lines')) {
            map.current.setLayoutProperty('arc-lines', 'visibility', layers.arcs ? 'visible' : 'none');
            if (layers.arcs && !arcsLoaded.current) {
                arcsLoaded.current = true;
                loadArcs();
            }
        }

        if (map.current.getLayer('entity-pulse')) {
            map.current.setLayoutProperty('entity-pulse', 'visibility', layers.pulse ? 'visible' : 'none');
            if (layers.pulse) { startPulse(); } else { stopPulse(); }
        }

        // 3d — Community vs entity_type color mode
        const colorExpr = layers.colorMode === 'community' ? COMMUNITY_COLOR_MATCH : ENTITY_COLOR_MATCH;
        if (map.current.getLayer('entity-markers')) {
            map.current.setPaintProperty('entity-markers', 'circle-color', colorExpr as any);
        }
        if (map.current.getLayer('entity-labels')) {
            map.current.setPaintProperty('entity-labels', 'text-color', colorExpr as any);
        }
    }, [layers, loadArcs, startPulse, stopPulse]);

    // -----------------------------------------------------------------------
    // Fetch entity data
    // -----------------------------------------------------------------------
    const loadEntities = useCallback(async (filters: EntityFilters = {}) => {
        if (!map.current) return;
        try {
            const { fetchEntities } = await import('@/utils/api');
            const entityData: EntityCollection = await fetchEntities(filters);
            setEntityCount({ filtered: entityData.filtered_count, total: entityData.total_count });
            console.log(`✓ Loaded ${entityData.features.length} entities`);

            const source = map.current.getSource('entities') as mapboxgl.GeoJSONSource;
            if (source) {
                source.setData(entityData as any);
            } else {
                addSourceAndLayers(entityData);
            }
        } catch (error) {
            console.error('Error loading entities:', error);
        }
    }, []); // eslint-disable-line react-hooks/exhaustive-deps

    const loadStats = useCallback(async () => {
        try {
            const { fetchMapStats } = await import('@/utils/api');
            setMapStats(await fetchMapStats());
        } catch (error) {
            console.error('Error loading map stats:', error);
        }
    }, []);

    // -----------------------------------------------------------------------
    // Build all map layers
    // -----------------------------------------------------------------------
    const addSourceAndLayers = (entityData: any) => {
        if (!map.current) return;

        // Entity source (clustered)
        map.current.addSource('entities', {
            type: 'geojson',
            data: entityData,
            cluster: true,
            clusterMaxZoom: 14,
            clusterRadius: 50,
        });

        // Arc source (empty until first toggle-on)
        map.current.addSource('entity-arcs', {
            type: 'geojson',
            data: { type: 'FeatureCollection', features: [] },
        });

        // 3a — Heatmap (intelligence_score weighted, hidden by default)
        map.current.addLayer({
            id: 'intel-heatmap',
            type: 'heatmap',
            source: 'entities',
            maxzoom: 13,
            layout: { visibility: 'none' },
            paint: {
                'heatmap-weight': ['interpolate', ['linear'], ['get', 'intelligence_score'], 0, 0, 1, 1],
                'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 0, 1, 9, 3],
                'heatmap-color': [
                    'interpolate', ['linear'], ['heatmap-density'],
                    0,   'rgba(0, 50, 100, 0)',
                    0.2, 'rgba(0, 168, 232, 0.5)',
                    0.4, 'rgba(0, 229, 204, 0.7)',
                    0.6, 'rgba(255, 215, 0, 0.8)',
                    0.8, 'rgba(255, 107, 53, 0.9)',
                    1,   'rgba(247, 37, 133, 1)',
                ],
                'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 0, 4, 9, 24],
                'heatmap-opacity': 0.75,
            },
        });

        // 3b — Arc lines (entity co-occurrence, hidden by default)
        map.current.addLayer({
            id: 'arc-lines',
            type: 'line',
            source: 'entity-arcs',
            layout: { visibility: 'none', 'line-join': 'round', 'line-cap': 'round' },
            paint: {
                'line-color': '#00A8E8',
                'line-width': ['interpolate', ['linear'], ['get', 'shared_storylines'], 1, 0.5, 5, 2.5],
                'line-opacity': ['interpolate', ['linear'], ['get', 'max_momentum'], 0, 0.15, 1, 0.45],
            },
        });

        // Cluster circles
        map.current.addLayer({
            id: 'clusters',
            type: 'circle',
            source: 'entities',
            filter: ['has', 'point_count'],
            paint: {
                'circle-radius': ['step', ['get', 'point_count'], 20, 10, 30, 100, 40, 750, 50],
                'circle-color': ['step', ['get', 'point_count'], '#00A8E8', 10, '#FF6B35', 100, '#F72585', 750, '#FF0000'],
                'circle-opacity': 0.8,
                'circle-stroke-width': 2,
                'circle-stroke-color': '#FFFFFF',
            },
        });

        // Cluster count labels
        map.current.addLayer({
            id: 'cluster-count',
            type: 'symbol',
            source: 'entities',
            filter: ['has', 'point_count'],
            layout: {
                'text-field': '{point_count_abbreviated}',
                'text-font': ['DIN Offc Pro Medium', 'Arial Unicode MS Bold'],
                'text-size': 12,
            },
            paint: { 'text-color': '#FFFFFF' },
        });

        // Entity markers (color-coded)
        map.current.addLayer({
            id: 'entity-markers',
            type: 'circle',
            source: 'entities',
            filter: ['!', ['has', 'point_count']],
            paint: {
                'circle-radius': ['interpolate', ['linear'], ['zoom'], 3, 6, 10, 12],
                'circle-color': ENTITY_COLOR_MATCH as any,
                'circle-stroke-width': 2,
                'circle-stroke-color': [
                    'case',
                    ['boolean', ['feature-state', 'hover'], false],
                    '#FFFFFF',
                    'rgba(255, 255, 255, 0.4)',
                ],
                'circle-opacity': 0.85,
            },
        });

        // 3c — Pulse ring (entities seen < 48h, hidden by default)
        map.current.addLayer({
            id: 'entity-pulse',
            type: 'circle',
            source: 'entities',
            filter: ['all', ['!', ['has', 'point_count']], ['<=', ['get', 'hours_ago'], 48]],
            layout: { visibility: 'none' },
            paint: {
                'circle-radius': 8,
                'circle-color': 'transparent',
                'circle-stroke-width': 2,
                'circle-stroke-color': ENTITY_COLOR_MATCH as any,
                'circle-stroke-opacity': 1,
                'circle-opacity': 0,
            },
        });

        // Entity labels
        map.current.addLayer({
            id: 'entity-labels',
            type: 'symbol',
            source: 'entities',
            filter: ['!', ['has', 'point_count']],
            layout: {
                'text-field': ['get', 'name'],
                'text-font': ['DIN Offc Pro Medium', 'Arial Unicode MS Bold'],
                'text-size': 11,
                'text-offset': [0, 1.5],
                'text-anchor': 'top',
            },
            paint: {
                'text-color': ENTITY_COLOR_MATCH as any,
                'text-halo-color': '#0A1628',
                'text-halo-width': 1,
            },
            minzoom: 5,
        });

        setupEventHandlers();
    };

    // -----------------------------------------------------------------------
    // Hover + click event handlers
    // -----------------------------------------------------------------------
    const setupEventHandlers = () => {
        if (!map.current) return;

        popup.current = new mapboxgl.Popup({
            closeButton: false,
            closeOnClick: false,
            className: 'entity-tooltip',
            maxWidth: '280px',
            offset: 15,
        });

        map.current.on('mouseenter', 'entity-markers', (e) => {
            if (!map.current || !e.features || e.features.length === 0) return;
            map.current.getCanvas().style.cursor = 'pointer';

            const feature = e.features[0];
            const props = feature.properties;
            if (!props || feature.geometry.type !== 'Point') return;

            const coords = feature.geometry.coordinates as [number, number];
            const typeColor = ENTITY_TYPE_COLORS[props.entity_type] || '#888';
            const scoreStr = props.intelligence_score != null
                ? Number(props.intelligence_score).toFixed(2)
                : '–';
            const topStory = props.top_storyline
                ? `<div style="color:#94a3b8;font-size:10px;margin-top:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:240px;">▸ ${props.top_storyline}</div>`
                : '';

            popup.current?.setLngLat(coords).setHTML(`
                <div style="font-family:'SF Mono','Fira Code',monospace;font-size:12px;color:#e2e8f0;line-height:1.5;">
                    <div style="font-weight:700;font-size:13px;margin-bottom:4px;color:${typeColor};">${props.name}</div>
                    <div style="display:flex;align-items:center;gap:6px;margin-bottom:2px;">
                        <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${typeColor};"></span>
                        <span style="color:#94a3b8;font-size:11px;">${props.entity_type}</span>
                        <span style="color:#475569;">•</span>
                        <span style="color:#94a3b8;font-size:11px;">${props.mention_count} mentions</span>
                    </div>
                    <div style="color:#64748b;font-size:10px;">
                        score: <span style="color:#00A8E8;">${scoreStr}</span>
                        &nbsp;·&nbsp;
                        storylines: <span style="color:#39D353;">${props.storyline_count ?? 0}</span>
                    </div>
                    ${topStory}
                </div>
            `).addTo(map.current!);
        });

        map.current.on('mouseleave', 'entity-markers', () => {
            if (!map.current) return;
            map.current.getCanvas().style.cursor = '';
            popup.current?.remove();
        });

        map.current.on('click', 'entity-markers', async (e) => {
            if (!e.features || e.features.length === 0) return;
            const feature = e.features[0];
            const entityId = feature.properties?.id;
            if (!entityId) return;
            popup.current?.remove();

            try {
                const response = await fetch(`/api/proxy/map/entities/${entityId}`);
                if (!response.ok) throw new Error(`Failed: ${response.statusText}`);
                setSelectedEntity(await response.json());
            } catch (error) {
                console.error('Error fetching entity details:', error);
                if (map.current && feature.geometry.type === 'Point') {
                    new mapboxgl.Popup()
                        .setLngLat(feature.geometry.coordinates as [number, number])
                        .setHTML(`<div style="color:#e2e8f0;font-family:monospace;font-size:12px;"><strong>${feature.properties?.name}</strong><br/><span style="color:#ef4444;">Failed to load details</span></div>`)
                        .addTo(map.current);
                }
            }
        });

        map.current.on('click', 'clusters', (e) => {
            if (!map.current) return;
            const features = map.current.queryRenderedFeatures(e.point, { layers: ['clusters'] });
            if (!features || features.length === 0) return;
            const clusterId = features[0].properties?.cluster_id;
            const source = map.current.getSource('entities') as mapboxgl.GeoJSONSource;
            if (!source || typeof source.getClusterExpansionZoom !== 'function') return;
            source.getClusterExpansionZoom(clusterId, (err, zoom) => {
                if (err || !map.current) return;
                if (features[0].geometry.type === 'Point') {
                    map.current.easeTo({ center: features[0].geometry.coordinates as [number, number], zoom: zoom || 10, duration: 500 });
                }
            });
        });

        map.current.on('mouseenter', 'clusters', () => { if (map.current) map.current.getCanvas().style.cursor = 'pointer'; });
        map.current.on('mouseleave', 'clusters', () => { if (map.current) map.current.getCanvas().style.cursor = ''; });
    };

    // -----------------------------------------------------------------------
    // Map initialization
    // -----------------------------------------------------------------------
    useEffect(() => {
        if (map.current) return;
        if (!mapContainer.current) return;
        const token = process.env.NEXT_PUBLIC_MAPBOX_TOKEN;
        if (!token) return;

        mapboxgl.accessToken = token;
        try {
            map.current = new mapboxgl.Map({
                container: mapContainer.current,
                style: 'mapbox://styles/mapbox/dark-v11',
                center: [mapState.longitude, mapState.latitude],
                zoom: mapState.zoom,
                pitch: 0, bearing: 0,
                attributionControl: false,
            });
            map.current.addControl(new mapboxgl.NavigationControl(), 'top-right');
            map.current.on('move', () => {
                if (!map.current) return;
                const c = map.current.getCenter();
                setMapState({ latitude: c.lat, longitude: c.lng, zoom: map.current.getZoom() });
            });
            map.current.on('load', () => { loadEntities(); loadStats(); });
        } catch (error) {
            console.error('Error initializing map:', error);
        }

        return () => {
            stopPulse();
            popup.current?.remove();
            map.current?.remove();
            map.current = null;
        };
    }, [loadEntities, loadStats, stopPulse]); // eslint-disable-line react-hooks/exhaustive-deps

    const toggleLayer = (key: keyof LayerToggles) => {
        setLayers(prev => {
            if (key === 'colorMode') {
                return { ...prev, colorMode: prev.colorMode === 'entity_type' ? 'community' : 'entity_type' };
            }
            return { ...prev, [key]: !prev[key as 'heatmap' | 'arcs' | 'pulse'] };
        });
    };

    return (
        <div className="relative w-full h-screen bg-black overflow-hidden">
            <div ref={mapContainer} className="absolute inset-0" style={{ width: '100%', height: '100%' }} />

            <GridOverlay />

            <HUDOverlay
                latitude={mapState.latitude}
                longitude={mapState.longitude}
                zoom={mapState.zoom}
                entityCount={entityCount}
                stats={mapStats}
            />

            {/* Layer toggle controls (below INTELITA branding) */}
            <div className="absolute top-44 left-6 z-30 pointer-events-auto font-mono text-[10px] space-y-1">
                <LayerToggleBtn label="HEATMAP"  active={layers.heatmap}                      color="#FF6B35" onClick={() => toggleLayer('heatmap')} />
                <LayerToggleBtn label="ARCS"     active={layers.arcs}                         color="#00A8E8" onClick={() => toggleLayer('arcs')} />
                <LayerToggleBtn label="PULSE"    active={layers.pulse}                        color="#39D353" onClick={() => toggleLayer('pulse')} />
                <LayerToggleBtn
                    label={layers.colorMode === 'entity_type' ? 'COLOR: TYPE' : 'COLOR: COMM'}
                    active={layers.colorMode === 'community'}
                    color="#7B61FF"
                    onClick={() => toggleLayer('colorMode')}
                />
            </div>

            <FilterPanel onFilterChange={loadEntities} entityCount={entityCount} />

            <EntityDossier entity={selectedEntity} onClose={() => setSelectedEntity(null)} />
        </div>
    );
}

function LayerToggleBtn({ label, active, color, onClick }: {
    label: string; active: boolean; color: string; onClick: () => void;
}) {
    return (
        <button
            type="button"
            onClick={onClick}
            className="flex items-center gap-1.5 px-2 py-1 rounded border transition-all"
            style={{
                borderColor: active ? color : 'rgba(255,255,255,0.1)',
                color: active ? color : '#4b5563',
                backgroundColor: active ? `${color}15` : 'rgba(10,22,40,0.8)',
            }}
        >
            <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: active ? color : '#374151' }} />
            {label}
        </button>
    );
}
