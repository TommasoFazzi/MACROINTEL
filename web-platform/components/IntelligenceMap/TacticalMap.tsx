'use client';

import { useEffect, useLayoutEffect, useRef, useCallback, useState } from 'react';
import mapboxgl from 'mapbox-gl';
import 'mapbox-gl/dist/mapbox-gl.css';
import GridOverlay from './GridOverlay';
import HUDOverlay from './HUDOverlay';
import EntityDossier from './EntityDossier';
import FilterPanel from './FilterPanel';
import { ENTITY_TYPE_COLORS } from '@/types/entities';
import { useMapPosition } from '@/hooks/useMapPosition';
import { useMapLayers, ENTITY_COLOR_MATCH } from '@/hooks/useMapLayers';
import { useMapData } from '@/hooks/useMapData';
import type { EntityCollection } from '@/types/entities';
import { HelpModal } from '@/components/HelpModal';
import type { HelpSection } from '@/components/HelpModal';

// ── Types ────────────────────────────────────────────────────────────────────

interface TacticalMapProps {
    storylineId?: number | null;
}

// ── Map Guide Content ─────────────────────────────────────────────────────────

const MAP_GUIDE_SECTIONS: HelpSection[] = [
    {
        key: 'entities',
        label: 'Entity Markers',
        labelColor: 'text-orange-300',
        bgColor: 'bg-orange-500/8 border-orange-500/15',
        content: 'Each marker represents a tracked geopolitical entity (country, organization, person, location). Size and colour reflect entity type or community membership. Scroll to zoom; click a marker to open its dossier.',
    },
    {
        key: 'heatmap',
        label: 'Heatmap Layer',
        labelColor: 'text-red-300',
        bgColor: 'bg-red-500/8 border-red-500/15',
        content: 'Orange heat density weighted by intelligence score — darker areas have higher narrative activity. Toggle with the HEATMAP button on the left.',
    },
    {
        key: 'arcs',
        label: 'Arc Lines',
        labelColor: 'text-cyan-300',
        bgColor: 'bg-cyan-500/8 border-cyan-500/15',
        content: 'Cyan arcs connect entities that share active storylines. Arc width and opacity scale with the shared narrative weight. Toggle with ARCS.',
        tip: 'Thick arcs indicate strong narrative overlap between two entities.',
    },
    {
        key: 'pulse',
        label: 'Pulse Indicator',
        labelColor: 'text-green-300',
        bgColor: 'bg-green-500/8 border-green-500/15',
        content: 'Green pulsing animation marks entities mentioned in articles published within the last 48 hours — confirming recent activity. Toggle with PULSE.',
    },
    {
        key: 'color',
        label: 'Color Mode',
        labelColor: 'text-purple-300',
        bgColor: 'bg-purple-500/8 border-purple-500/15',
        content: 'Toggle between COLOR: TYPE (entity category — country, org, person) and COLOR: COMM (Louvain community clusters from the narrative graph). Community colors match the Stories graph.',
    },
    {
        key: 'storyline',
        label: 'Storyline Highlight',
        labelColor: 'text-yellow-300',
        bgColor: 'bg-yellow-500/8 border-yellow-500/15',
        content: 'Navigate to a storyline in the Stories graph and click "View on Map" to highlight all entities in that storyline. A gold banner appears at the top. Click CLEAR to remove the highlight.',
    },
];

interface StorylineBanner {
    title: string;
    count: number;
}

// ── Component ────────────────────────────────────────────────────────────────

export default function TacticalMap({ storylineId = null }: TacticalMapProps) {
    const mapContainer = useRef<HTMLDivElement>(null);
    const map = useRef<mapboxgl.Map | null>(null);
    const popup = useRef<mapboxgl.Popup | null>(null);

    const { mapState, setMapState } = useMapPosition();
    const { layers, toggleLayer, stopPulse } = useMapLayers({ mapRef: map });

    // addSourceAndLayers must be defined before useMapData (passed as dep)
    // We use a ref + stable wrapper to break the circular dependency
    const addSourceAndLayersRef = useRef<(data: EntityCollection) => void>(() => {});
    // Stable function identity — never changes, always delegates to current ref
    const addSourceAndLayersStable = useCallback((data: EntityCollection) => {
        addSourceAndLayersRef.current(data);
    }, []);

    const { entityCount, mapStats, selectedEntity, setSelectedEntity, loadEntities, loadStats, entityDataRef } =
        useMapData({ mapRef: map, addSourceAndLayers: addSourceAndLayersStable });

    const [storylineBanner, setStorylineBanner] = useState<StorylineBanner | null>(null);
    const [showHelp, setShowHelp] = useState(false);

    // ── Event handlers ───────────────────────────────────────────────────────

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

    // ── Build all map layers ─────────────────────────────────────────────────

    const addSourceAndLayers = useCallback((entityData: EntityCollection) => {
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

        // Heatmap (intelligence_score weighted, hidden by default)
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

        // Arc lines — dynamic width/color by shared_storylines
        map.current.addLayer({
            id: 'arc-lines',
            type: 'line',
            source: 'entity-arcs',
            layout: { visibility: 'none', 'line-join': 'round', 'line-cap': 'round' },
            paint: {
                'line-color': [
                    'interpolate', ['linear'], ['get', 'shared_storylines'],
                    1, '#00A8E8',
                    5, '#FFD700',
                    10, '#FF6B35',
                ],
                'line-width': [
                    'interpolate', ['linear'], ['get', 'shared_storylines'],
                    1, 1.0,
                    3, 2.5,
                    5, 4.0,
                    10, 6.0,
                ],
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

        // Entity markers — property-driven opacity for storyline highlight (_hl: 'on'|'off')
        map.current.addLayer({
            id: 'entity-markers',
            type: 'circle',
            source: 'entities',
            filter: ['!', ['has', 'point_count']],
            paint: {
                'circle-radius': ['interpolate', ['linear'], ['zoom'], 3, 6, 10, 12],
                'circle-color': ENTITY_COLOR_MATCH,
                'circle-stroke-width': [
                    'case',
                    ['==', ['get', '_hl'], 'on'], 4,
                    2,
                ],
                'circle-stroke-color': [
                    'case',
                    ['==', ['get', '_hl'], 'on'], '#FFD700',
                    'rgba(255, 255, 255, 0.4)',
                ],
                'circle-opacity': [
                    'case',
                    ['==', ['get', '_hl'], 'on'], 1.0,
                    ['==', ['get', '_hl'], 'off'], 0.15,
                    0.85,
                ],
            },
        });

        // Pulse ring (entities seen < 48h, hidden by default)
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
                'circle-stroke-color': ENTITY_COLOR_MATCH,
                'circle-stroke-opacity': 1,
                'circle-opacity': 0,
            },
        });

        // Entity labels — property-driven opacity for storyline highlight
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
                'text-color': ENTITY_COLOR_MATCH,
                'text-halo-color': '#0A1628',
                'text-halo-width': 1,
                'text-opacity': [
                    'case',
                    ['==', ['get', '_hl'], 'on'], 1.0,
                    ['==', ['get', '_hl'], 'off'], 0.1,
                    1.0,
                ],
            },
            minzoom: 5,
        });

        setupEventHandlers();
    }, []); // eslint-disable-line react-hooks/exhaustive-deps

    // Keep ref in sync (in a layout effect: refs must not be written during render)
    useLayoutEffect(() => {
        addSourceAndLayersRef.current = addSourceAndLayers;
    });


    // ── Stable refs for init effect (avoid re-running on callback identity change) ──
    const loadEntitiesRef = useRef(loadEntities);
    const loadStatsRef = useRef(loadStats);
    const stopPulseRef = useRef(stopPulse);
    useLayoutEffect(() => {
        loadEntitiesRef.current = loadEntities;
        loadStatsRef.current = loadStats;
        stopPulseRef.current = stopPulse;
    });

    // ── Map initialization (runs once) ────────────────────────────────────────

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
            map.current.on('load', () => { loadEntitiesRef.current(); loadStatsRef.current(); });
        } catch (error) {
            console.error('Error initializing map:', error);
        }

        return () => {
            stopPulseRef.current();
            popup.current?.remove();
            map.current?.remove();
            map.current = null;
        };
    }, []); // eslint-disable-line react-hooks/exhaustive-deps

    // ── Storyline highlight mode (property-based, works with clustering) ─────

    useEffect(() => {
        if (!map.current) return;

        // Clear previous highlight by restoring original data (no _hl property)
        const clearHighlight = () => {
            if (!map.current || !entityDataRef.current) return;
            const source = map.current.getSource('entities') as mapboxgl.GeoJSONSource | undefined;
            if (source) source.setData(entityDataRef.current);
            setStorylineBanner(null);
        };

        if (!storylineId) {
            clearHighlight();
            return;
        }

        // Stamp _hl property on GeoJSON features and update source
        const applyHighlight = async () => {
            if (!map.current || !entityDataRef.current) return;

            try {
                const { fetchEntitiesByStoryline } = await import('@/utils/api');
                const data = await fetchEntitiesByStoryline(storylineId);
                if (!map.current || !entityDataRef.current) return;

                const highlightIds = new Set(data.entity_ids);
                const original = entityDataRef.current;

                // Stamp each feature with _hl: 'on' | 'off'
                const stamped = {
                    ...original,
                    features: original.features.map((f) => ({
                        ...f,
                        properties: {
                            ...f.properties,
                            _hl: highlightIds.has(f.properties?.id) ? 'on' : 'off',
                        },
                    })),
                };

                const source = map.current.getSource('entities') as mapboxgl.GeoJSONSource;
                if (source) source.setData(stamped);

                // Fit bounds to highlighted entities (maxZoom: 5 to avoid ocean zoom)
                const bounds = new mapboxgl.LngLatBounds();
                let hasBounds = false;
                for (const f of stamped.features) {
                    if (f.properties._hl === 'on' && f.geometry?.type === 'Point') {
                        const [lng, lat] = f.geometry.coordinates as [number, number];
                        bounds.extend([lng, lat]);
                        hasBounds = true;
                    }
                }
                if (hasBounds) {
                    map.current.fitBounds(bounds, { padding: 100, maxZoom: 5, duration: 1000 });
                }

                setStorylineBanner({ title: data.storyline_title, count: data.entity_count });
            } catch (error) {
                console.error('Error applying storyline highlight:', error);
            }
        };

        // If source is ready, apply immediately; otherwise wait for sourcedata
        if (map.current.isStyleLoaded() && map.current.getSource('entities')) {
            applyHighlight();
        } else {
            const onSourceData = (e: mapboxgl.MapSourceDataEvent) => {
                if (e.sourceId === 'entities' && e.isSourceLoaded) {
                    map.current?.off('sourcedata', onSourceData);
                    applyHighlight();
                }
            };
            map.current.on('sourcedata', onSourceData);
        }
    }, [storylineId, entityDataRef]);

    // ── Clear storyline highlight ────────────────────────────────────────────

    const clearStorylineHighlight = useCallback(() => {
        if (!map.current || !entityDataRef.current) return;
        // Restore original data without _hl properties
        const source = map.current.getSource('entities') as mapboxgl.GeoJSONSource | undefined;
        if (source) source.setData(entityDataRef.current);
        setStorylineBanner(null);
        window.history.replaceState(null, '', '/map');
    }, [entityDataRef]);

    // ── Render ───────────────────────────────────────────────────────────────

    return (
        <div className="relative w-full h-[100dvh] bg-black overflow-hidden">
            <div ref={mapContainer} className="absolute inset-0" style={{ width: '100%', height: '100%' }} />

            <GridOverlay />

            <HUDOverlay
                latitude={mapState.latitude}
                longitude={mapState.longitude}
                zoom={mapState.zoom}
                entityCount={entityCount}
                stats={mapStats}
            />

            {/* Storyline highlight banner */}
            {storylineBanner && (
                <div className="absolute top-4 left-1/2 -translate-x-1/2 z-40 pointer-events-auto">
                    <div className="flex items-center gap-3 px-4 py-2 rounded-lg border border-[#FFD700]/40 bg-[#0A1628]/95 backdrop-blur-sm font-mono text-xs">
                        <span className="w-2 h-2 rounded-full bg-[#FFD700] animate-pulse" />
                        <span className="text-[#FFD700] font-bold">STORYLINE</span>
                        <span className="text-gray-300 max-w-[300px] truncate">{storylineBanner.title}</span>
                        <span className="text-gray-500">{storylineBanner.count} entities</span>
                        <button
                            type="button"
                            onClick={clearStorylineHighlight}
                            className="ml-2 px-2 py-0.5 rounded border border-gray-600 text-gray-400 hover:text-white hover:border-gray-400 transition-all"
                        >
                            CLEAR
                        </button>
                    </div>
                </div>
            )}

            {/* Layer toggle controls */}
            <div className="absolute top-44 left-6 z-30 pointer-events-auto font-mono text-xs space-y-1">
                <LayerToggleBtn label="HEATMAP"  active={layers.heatmap}                      color="#FF6B35" onClick={() => toggleLayer('heatmap')} />
                <LayerToggleBtn label="ARCS"     active={layers.arcs}                         color="#00A8E8" onClick={() => toggleLayer('arcs')} />
                <LayerToggleBtn label="PULSE"    active={layers.pulse}                        color="#39D353" onClick={() => toggleLayer('pulse')} />
                <LayerToggleBtn
                    label={layers.colorMode === 'entity_type' ? 'COLOR: TYPE' : 'COLOR: COMM'}
                    active={layers.colorMode === 'community'}
                    color="#7B61FF"
                    onClick={() => toggleLayer('colorMode')}
                />
                <button
                    type="button"
                    onClick={() => setShowHelp(true)}
                    className="flex items-center gap-2 px-3 py-1.5 rounded border border-white/10 bg-black/60 text-gray-400 hover:text-white hover:border-white/30 transition-all backdrop-blur-sm mt-2"
                >
                    <span className="text-[10px] font-bold tracking-wider uppercase">? Guide</span>
                </button>
            </div>

            <FilterPanel onFilterChange={loadEntities} entityCount={entityCount} />

            <EntityDossier entity={selectedEntity} onClose={() => setSelectedEntity(null)} />

            <HelpModal
                open={showHelp}
                onClose={() => setShowHelp(false)}
                title="Intelligence Map Guide"
                subtitle="How to navigate the geospatial layer"
                intro="The Intelligence Map shows all tracked geopolitical entities on a dark Mapbox canvas. Entities are clustered at low zoom and reveal individual markers as you zoom in."
                sections={MAP_GUIDE_SECTIONS}
            />
        </div>
    );
}

// ── Layer Toggle Button ──────────────────────────────────────────────────────

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
