'use client';

import { useCallback, useRef, useState, useMemo } from 'react';
import ForceGraph2D from 'react-force-graph-2d';
import { useGraphNetwork, useEgoNetwork } from '@/hooks/useStories';
import StorylineDossier from './StorylineDossier';
import type { NarrativeStatus } from '@/types/stories';

const STATUS_COLORS: Record<NarrativeStatus, string> = {
  emerging: '#FF6B35',
  active: '#00A8E8',
  stabilized: '#666666',
};

interface GraphNode {
  id: number;
  title: string;
  narrative_status: NarrativeStatus;
  momentum_score: number;
  article_count: number;
  category: string | null;
  community_id?: number | null;
  key_entities?: string[];
  x?: number;
  y?: number;
}

interface GraphLink {
  source: number | GraphNode;
  target: number | GraphNode;
  weight: number;
  relation_type: string;
}

// Palette for community coloring (community 0 = largest = most prominent color)
const COMMUNITY_COLORS = [
  '#FF6B35', '#00A8E8', '#39D353', '#FFD700',
  '#FF4081', '#7B61FF', '#00E5CC', '#FF7043',
];

export default function StorylineGraph() {
  const graphRef = useRef<any>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const { graph, isLoading, error, refresh } = useGraphNetwork();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [hoveredNode, setHoveredNode] = useState<GraphNode | null>(null);
  const [minMomentum, setMinMomentum] = useState(0);

  // Ego-network state: loaded on node click, overlay on top of frozen global graph
  const { egoNetwork } = useEgoNetwork(selectedId, 0.05);
  const egoNeighborIds = useMemo<Set<number>>(() => {
    if (!egoNetwork || !selectedId) return new Set();
    return new Set([selectedId, ...egoNetwork.neighbors.map((n) => n.id)]);
  }, [egoNetwork, selectedId]);

  // Transform API data for react-force-graph (with momentum filter)
  const graphData = useMemo(() => {
    if (!graph) return { nodes: [], links: [] };

    const allNodes: GraphNode[] = graph.nodes.map((n) => ({
      id: n.id,
      title: n.title,
      narrative_status: n.narrative_status as NarrativeStatus,
      momentum_score: n.momentum_score,
      article_count: n.article_count,
      category: n.category,
      community_id: n.community_id ?? null,
      key_entities: n.key_entities,
    }));

    const filteredNodes = minMomentum > 0
      ? allNodes.filter((n) => n.momentum_score >= minMomentum)
      : allNodes;
    const filteredIds = new Set(filteredNodes.map((n) => n.id));

    const links: GraphLink[] = graph.links
      .filter((l) => filteredIds.has(l.source as number) && filteredIds.has(l.target as number))
      .map((l) => ({
        source: l.source,
        target: l.target,
        weight: l.weight,
        relation_type: l.relation_type,
      }));

    return { nodes: filteredNodes, links };
  }, [graph, minMomentum]);

  // Compute community labels from visible node data (top 10 by size)
  const communityLabels = useMemo(() => {
    const communityMap = new Map<number, { count: number; entities: Map<string, number> }>();

    for (const node of graphData.nodes) {
      const cid = node.community_id;
      if (cid == null) continue;
      if (!communityMap.has(cid)) {
        communityMap.set(cid, { count: 0, entities: new Map() });
      }
      const entry = communityMap.get(cid)!;
      entry.count++;
      for (const e of (node.key_entities || []).slice(0, 5)) {
        entry.entities.set(e, (entry.entities.get(e) || 0) + 1);
      }
    }

    return Array.from(communityMap.entries())
      .map(([cid, { count, entities }]) => {
        const topEntity = [...entities.entries()]
          .sort((a, b) => b[1] - a[1])[0]?.[0] || `Community ${cid}`;
        return { cid, label: topEntity, count };
      })
      .sort((a, b) => b.count - a.count)
      .slice(0, 10);
  }, [graphData.nodes]);

  // Compute community centroids for canvas labels (only communities with 3+ nodes)
  const communityCentroids = useMemo(() => {
    const groups = new Map<number, { xs: number[]; ys: number[] }>();
    for (const node of graphData.nodes) {
      const cid = node.community_id;
      if (cid == null || node.x == null || node.y == null) continue;
      if (!groups.has(cid)) groups.set(cid, { xs: [], ys: [] });
      groups.get(cid)!.xs.push(node.x);
      groups.get(cid)!.ys.push(node.y);
    }
    return new Map(
      [...groups.entries()]
        .filter(([, g]) => g.xs.length >= 3)
        .map(([cid, g]) => [cid, {
          x: g.xs.reduce((a, b) => a + b, 0) / g.xs.length,
          y: g.ys.reduce((a, b) => a + b, 0) / g.ys.length,
          label: communityLabels.find((c) => c.cid === cid)?.label || '',
        }])
    );
  }, [graphData.nodes, communityLabels]);

  // Node rendering
  const paintNode = useCallback(
    (node: any, ctx: CanvasRenderingContext2D, globalScale: number) => {
      const { x, y, title, momentum_score, narrative_status } = node as GraphNode;
      if (x === undefined || y === undefined) return;

      const isSelected = node.id === selectedId;
      const isHovered = hoveredNode?.id === node.id;
      const isEgoActive = egoNeighborIds.size > 0;
      const isNeighbor = egoNeighborIds.has(node.id);

      // Use community color if available, fallback to status color
      const communityId = (node as GraphNode).community_id;
      const color = communityId != null
        ? COMMUNITY_COLORS[communityId % COMMUNITY_COLORS.length]
        : (STATUS_COLORS[narrative_status] || STATUS_COLORS.active);

      // Dim non-neighbor nodes when ego mode is active
      const alpha = isEgoActive && !isNeighbor ? 0.08 : 1.0;
      ctx.globalAlpha = alpha;

      // Node radius based on momentum (min 4, max 16)
      const radius = 4 + momentum_score * 12;

      // Glow effect for selected/hovered
      if (isSelected || isHovered) {
        ctx.beginPath();
        ctx.arc(x, y, radius + 4, 0, 2 * Math.PI);
        ctx.fillStyle = `${color}33`;
        ctx.fill();
      }

      // Main circle
      ctx.beginPath();
      ctx.arc(x, y, radius, 0, 2 * Math.PI);
      ctx.fillStyle = isSelected ? '#FFFFFF' : color;
      ctx.fill();

      // Border
      ctx.strokeStyle = isSelected ? color : `${color}88`;
      ctx.lineWidth = isSelected ? 2 : 1;
      ctx.stroke();

      // Label (only show when zoomed in enough or for high-momentum nodes)
      if (globalScale > 1.5 || momentum_score > 0.7 || isHovered || isSelected) {
        const label = title.length > 30 ? title.slice(0, 30) + '...' : title;
        const fontSize = Math.max(10 / globalScale, 3);
        ctx.font = `${fontSize}px monospace`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';

        // Text background
        const textWidth = ctx.measureText(label).width;
        ctx.fillStyle = 'rgba(10, 22, 40, 0.85)';
        ctx.fillRect(
          x - textWidth / 2 - 2,
          y + radius + 2,
          textWidth + 4,
          fontSize + 4
        );

        // Text
        ctx.fillStyle = isSelected ? '#FFFFFF' : '#CCCCCC';
        ctx.fillText(label, x, y + radius + 4);
      }

      // Reset alpha so subsequent canvas draws are unaffected
      ctx.globalAlpha = 1.0;
    },
    [selectedId, hoveredNode, egoNeighborIds]
  );

  // Community label overlay drawn after all nodes
  const paintFramePost = useCallback(
    (ctx: CanvasRenderingContext2D) => {
      if (communityCentroids.size === 0) return;
      ctx.save();
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';

      for (const [cid, { x, y, label }] of communityCentroids) {
        if (!label) continue;
        const color = COMMUNITY_COLORS[cid % COMMUNITY_COLORS.length];
        ctx.font = 'bold 18px monospace';
        ctx.globalAlpha = 0.22;
        ctx.fillStyle = color;
        ctx.fillText(label.toUpperCase(), x, y);
      }
      ctx.restore();
    },
    [communityCentroids]
  );

  // Link rendering
  const paintLink = useCallback(
    (link: any, ctx: CanvasRenderingContext2D) => {
      const { source, target, weight } = link;
      if (!source.x || !target.x) return;

      const isEgoActive = egoNeighborIds.size > 0;
      const srcId = typeof source === 'object' ? source.id : source;
      const tgtId = typeof target === 'object' ? target.id : target;
      const isEgoEdge = egoNeighborIds.has(srcId) && egoNeighborIds.has(tgtId);

      // Dim non-ego links; brighten ego links
      const alpha = isEgoActive ? (isEgoEdge ? 0.9 : 0.03) : (0.2 + weight * 0.6);
      const lineWidth = isEgoActive && isEgoEdge ? 1.0 + weight * 3.0 : 0.5 + weight * 2.5;

      ctx.beginPath();
      ctx.moveTo(source.x, source.y);
      ctx.lineTo(target.x, target.y);
      ctx.strokeStyle = isEgoEdge
        ? `rgba(255, 107, 53, ${alpha})`
        : `rgba(100, 100, 100, ${alpha})`;
      ctx.lineWidth = lineWidth;
      ctx.stroke();
    },
    [egoNeighborIds]
  );

  const handleNodeClick = useCallback((node: any) => {
    setSelectedId((prev) => (prev === node.id ? null : node.id));
  }, []);

  const handleNavigate = useCallback((id: number) => {
    setSelectedId(id);
    if (graphRef.current) {
      const node = graphData.nodes.find((n) => n.id === id);
      if (node && node.x !== undefined && node.y !== undefined) {
        graphRef.current.centerAt(node.x, node.y, 500);
        graphRef.current.zoom(3, 500);
      }
    }
  }, [graphData.nodes]);

  return (
    <div ref={containerRef} className="relative w-full h-screen bg-[#0A1628] overflow-hidden">
      {/* Force Graph */}
      <ForceGraph2D
        ref={graphRef}
        graphData={graphData}
        nodeId="id"
        nodeCanvasObject={paintNode}
        nodePointerAreaPaint={(node: any, color, ctx) => {
          const radius = 4 + (node.momentum_score || 0.5) * 12;
          ctx.beginPath();
          ctx.arc(node.x!, node.y!, radius + 4, 0, 2 * Math.PI);
          ctx.fillStyle = color;
          ctx.fill();
        }}
        linkCanvasObject={paintLink}
        onRenderFramePost={paintFramePost}
        onNodeClick={handleNodeClick}
        onNodeHover={(node: any) => setHoveredNode(node || null)}
        backgroundColor="#0A1628"
        warmupTicks={300}
        cooldownTicks={0}
        d3AlphaDecay={0.05}
        d3VelocityDecay={0.4}
        linkDirectionalParticles={0}
        enableNodeDrag={true}
        enableZoomInteraction={true}
        enablePanInteraction={true}
      />

      {/* HUD Overlay - Top Left */}
      <div className="absolute top-4 left-4 pointer-events-none">
        <div className="bg-[#0A1628]/80 backdrop-blur-sm border border-[#FF6B35]/30 rounded px-4 py-3">
          <div className="flex items-center gap-2 mb-2">
            <div className="w-2 h-2 bg-[#FF6B35] rounded-full animate-pulse" />
            <span className="text-[#FF6B35] font-mono text-sm font-bold tracking-wider">
              NARRATIVE GRAPH
            </span>
          </div>
          {graph?.stats && (
            <div className="space-y-1 text-xs font-mono text-gray-400">
              <div>NODES: <span className="text-white">{graph.stats.total_nodes}</span></div>
              <div>EDGES: <span className="text-white">{graph.stats.total_edges}</span></div>
              <div>COMMUNITIES: <span className="text-white">{graph.stats.communities_count || '—'}</span></div>
              <div>AVG MOMENTUM: <span className="text-white">{graph.stats.avg_momentum.toFixed(2)}</span></div>
              <div>EDGES/NODE: <span className="text-white">{graph.stats.avg_edges_per_node?.toFixed(1) || '—'}</span></div>
            </div>
          )}
        </div>
      </div>

      {/* Momentum filter + Dynamic community legend - Top Right */}
      <div className="absolute top-4 right-4 pointer-events-auto">
        {!selectedId && (
          <div className="bg-[#0A1628]/80 backdrop-blur-sm border border-white/10 rounded px-4 py-3 min-w-[190px]">
            {/* Momentum slider */}
            <div className="mb-3">
              <div className="flex justify-between text-xs font-mono text-gray-500 mb-1">
                <span className="uppercase">Min Momentum</span>
                <span className="text-white">{minMomentum.toFixed(1)}</span>
              </div>
              <input
                type="range"
                aria-label="Minimum momentum filter"
                min={0}
                max={1}
                step={0.1}
                value={minMomentum}
                onChange={(e) => setMinMomentum(parseFloat(e.target.value))}
                className="w-full h-1 accent-[#FF6B35] cursor-pointer"
              />
            </div>

            {/* Dynamic community legend */}
            {communityLabels.length > 0 && (
              <>
                <div className="text-xs font-mono text-gray-500 mb-2 uppercase">Communities</div>
                <div className="space-y-1.5 max-h-[240px] overflow-y-auto">
                  {communityLabels.map(({ cid, label, count }) => (
                    <div key={cid} className="flex items-center gap-2">
                      <div
                        className="w-3 h-3 rounded-full flex-shrink-0"
                        style={{ backgroundColor: COMMUNITY_COLORS[cid % COMMUNITY_COLORS.length] }}
                      />
                      <span className="text-xs font-mono text-gray-300 truncate max-w-[120px]">
                        {label}
                      </span>
                      <span className="text-xs font-mono text-gray-600 ml-auto">{count}</span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        )}
      </div>

      {/* Hovered node tooltip */}
      {hoveredNode && !selectedId && (
        <div className="absolute bottom-4 left-4 pointer-events-none">
          <div className="bg-[#0A1628]/90 backdrop-blur-sm border border-[#FF6B35]/30 rounded px-4 py-3 max-w-sm">
            <div className="text-white font-mono text-sm font-bold mb-1">
              {hoveredNode.title}
            </div>
            <div className="flex items-center gap-3 text-xs font-mono text-gray-400">
              <span>Momentum: <span className="text-[#FF6B35]">{hoveredNode.momentum_score.toFixed(2)}</span></span>
              <span>Articles: <span className="text-white">{hoveredNode.article_count}</span></span>
              {hoveredNode.category && (
                <span className="text-[#00A8E8]">{hoveredNode.category}</span>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Loading overlay */}
      {isLoading && !graph && (
        <div className="absolute inset-0 flex items-center justify-center bg-[#0A1628]/80">
          <div className="text-center">
            <div className="w-12 h-12 border-2 border-[#FF6B35] border-t-transparent rounded-full animate-spin mx-auto mb-4" />
            <div className="text-[#FF6B35] font-mono text-sm">Loading graph data...</div>
          </div>
        </div>
      )}

      {/* Error state */}
      {error && !graph && (
        <div className="absolute inset-0 flex items-center justify-center bg-[#0A1628]/80">
          <div className="text-center max-w-md">
            <div className="text-red-400 font-mono text-lg mb-2">Connection Error</div>
            <div className="text-gray-400 font-mono text-sm mb-4">
              Unable to load narrative graph data. Make sure the API server is running.
            </div>
            <button
              type="button"
              onClick={() => refresh()}
              className="px-4 py-2 bg-[#FF6B35]/20 border border-[#FF6B35]/40 text-[#FF6B35] font-mono text-sm rounded hover:bg-[#FF6B35]/30 transition-colors"
            >
              RETRY
            </button>
          </div>
        </div>
      )}

      {/* Empty state */}
      {!isLoading && !error && graph && graph.nodes.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="text-center">
            <div className="text-gray-500 font-mono text-lg mb-2">No Active Storylines</div>
            <div className="text-gray-600 font-mono text-sm">
              Run the narrative pipeline to generate storylines.
            </div>
          </div>
        </div>
      )}

      {/* Storyline Dossier Panel */}
      <StorylineDossier
        storylineId={selectedId}
        onClose={() => setSelectedId(null)}
        onNavigate={handleNavigate}
      />

      {/* Corner brackets */}
      <div className="absolute top-2 left-2 w-8 h-8 border-l-2 border-t-2 border-[#FF6B35]/30 pointer-events-none" />
      <div className="absolute top-2 right-2 w-8 h-8 border-r-2 border-t-2 border-[#FF6B35]/30 pointer-events-none" />
      <div className="absolute bottom-2 left-2 w-8 h-8 border-l-2 border-b-2 border-[#FF6B35]/30 pointer-events-none" />
      <div className="absolute bottom-2 right-2 w-8 h-8 border-r-2 border-b-2 border-[#FF6B35]/30 pointer-events-none" />
    </div>
  );
}
