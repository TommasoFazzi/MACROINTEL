'use client';

import { useEffect, useRef } from 'react';
import { useSigma } from '@react-sigma/core';
import { useGraphContext } from './GraphContext';

export default function GraphStyle() {
  const sigma = useSigma();
  const { selectedId, hoveredNodeRef, egoNeighborIds, filterState, communityColorMap } = useGraphContext();
  const pendingRefreshRef = useRef(false);

  const scheduleRefresh = () => {
    if (pendingRefreshRef.current) return;
    pendingRefreshRef.current = true;
    requestAnimationFrame(() => {
      sigma.refresh();
      pendingRefreshRef.current = false;
    });
  };

  useEffect(() => {
    const { momentumMin, isolate, highlightIds } = filterState;
    const egoActive = egoNeighborIds.size > 0;

    sigma.setSettings({
      nodeReducer: (node, data) => {
        const id = Number(node);
        const attrs = { ...data };
        const momentum: number = (attrs.momentum_score as number) ?? 0;
        const baseColor: string = communityColorMap.get(attrs.community_id as number) ?? (attrs.color as string ?? '#2A3A4A');

        // Momentum filter
        if (momentumMin > 0 && momentum < momentumMin) {
          return { ...attrs, hidden: true };
        }

        // Isolate mode: hide non-matching nodes
        if (isolate && highlightIds.size > 0 && !highlightIds.has(id)) {
          return { ...attrs, hidden: true };
        }

        // Dim mode: non-matching nodes get low alpha
        if (!isolate && highlightIds.size > 0 && !highlightIds.has(id) && !egoActive) {
          return { ...attrs, color: baseColor + '14', size: (attrs.size as number) * 0.7 };
        }

        // Ego mode: dim non-neighbors; scale neighbors by edge weight for hierarchy
        if (egoActive) {
          if (!egoNeighborIds.has(id)) {
            return { ...attrs, color: baseColor + '0D', size: (attrs.size as number) * 0.6 };
          }
          if (id !== selectedId) {
            // Find the edge connecting this neighbor to the selected node
            const graph = sigma.getGraph();
            const edgeKey = graph.edge(node, String(selectedId)) ?? graph.edge(String(selectedId), node);
            const edgeWeight: number = edgeKey ? (graph.getEdgeAttribute(edgeKey, 'weight') as number ?? 0) : 0;
            // Scale size by connection strength: weak neighbor ~70% size, strong neighbor ~150%
            const scaleFactor = 0.7 + edgeWeight * 1.5;
            // Opacity by connection strength
            const alpha = Math.round((0.45 + edgeWeight * 0.55) * 255).toString(16).padStart(2, '0');
            return { ...attrs, color: baseColor + alpha, size: (attrs.size as number) * scaleFactor };
          }
        }

        // Selected node
        if (id === selectedId) {
          return { ...attrs, color: '#FFFFFF', highlighted: true };
        }

        // Hovered node
        if (id === hoveredNodeRef.current) {
          return { ...attrs, highlighted: true };
        }

        return attrs;
      },

      edgeReducer: (edge, data) => {
        if (egoNeighborIds.size === 0) return data;

        const graph = sigma.getGraph();
        const [src, tgt] = graph.extremities(edge);
        const srcId = Number(src);
        const tgtId = Number(tgt);
        const isEgoEdge = egoNeighborIds.has(srcId) && egoNeighborIds.has(tgtId);

        if (isEgoEdge) {
          // Scale ego edges by weight: strong connection = thick bright orange, weak = thin pale
          const w: number = (data.weight as number) ?? 0;
          const opacity = 0.35 + w * 0.65;
          const size = 1.0 + w * 4.0;
          return { ...data, color: `rgba(249,115,22,${opacity.toFixed(2)})`, size };
        }
        return { ...data, color: 'rgba(150,190,220,0.02)', size: 0.2 };
      },
    });

    scheduleRefresh();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, egoNeighborIds, filterState, communityColorMap]);

  return null;
}
