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

        // Ego mode: dim non-neighbors
        if (egoActive && !egoNeighborIds.has(id)) {
          return { ...attrs, color: baseColor + '0D', size: (attrs.size as number) * 0.8 };
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
          return { ...data, color: 'rgba(249,115,22,0.9)', size: 3.0 };
        }
        return { ...data, color: 'rgba(150,190,220,0.03)', size: 0.3 };
      },
    });

    scheduleRefresh();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, egoNeighborIds, filterState, communityColorMap]);

  return null;
}
