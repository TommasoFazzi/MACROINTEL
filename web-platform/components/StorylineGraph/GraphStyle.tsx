'use client';

import { useEffect } from 'react';
import { useSigma } from '@react-sigma/core';
import { useGraphContext } from './GraphContext';
import { useScheduledRefresh } from './useScheduledRefresh';
import { SINGLETON_COLOR } from '@/lib/communityColors';

const EGO_EDGE_BASE = 'rgba(249,115,22,'; // orange
const FADED_EDGE = 'rgba(150,190,220,0.02)';

/** Append an 8-bit hex alpha (0..1 → "00".."ff") to a #rrggbb color. */
function withAlpha(hex: string, alpha: number): string {
  const a = Math.round(Math.max(0, Math.min(1, alpha)) * 255)
    .toString(16)
    .padStart(2, '0');
  return hex + a;
}

export default function GraphStyle() {
  const sigma = useSigma();
  const { selectedId, hoveredNodeRef, egoNeighborIds, filterState, communityColorMap } = useGraphContext();
  const scheduleRefresh = useScheduledRefresh();

  useEffect(() => {
    const { momentumMin, isolate, highlightIds } = filterState;
    const egoActive = egoNeighborIds.size > 0;

    sigma.setSettings({
      nodeReducer: (node, data) => {
        const id = Number(node);
        const momentum = (data.momentum_score as number) ?? 0;

        // Momentum filter — hide below threshold
        if (momentumMin > 0 && momentum < momentumMin) {
          return { ...data, hidden: true };
        }

        // Isolate mode — hide non-matching nodes entirely
        if (isolate && highlightIds.size > 0 && !highlightIds.has(id)) {
          return { ...data, hidden: true };
        }

        const baseColor =
          communityColorMap.get(data.community_id as number) ?? (data.color as string) ?? SINGLETON_COLOR;

        // Dim mode — non-matching filter nodes get low alpha (ego/hover take over below)
        if (!isolate && highlightIds.size > 0 && !highlightIds.has(id) && !egoActive) {
          return { ...data, color: withAlpha(baseColor, 0.08), size: (data.size as number) * 0.7 };
        }

        // Ego mode — dim non-neighbors; scale neighbors by edge weight for hierarchy
        if (egoActive) {
          if (!egoNeighborIds.has(id)) {
            return { ...data, color: withAlpha(baseColor, 0.05), size: (data.size as number) * 0.6 };
          }
          if (id !== selectedId) {
            const graph = sigma.getGraph();
            const edgeKey = graph.edge(node, String(selectedId)) ?? graph.edge(String(selectedId), node);
            const edgeWeight = edgeKey ? ((graph.getEdgeAttribute(edgeKey, 'weight') as number) ?? 0) : 0;
            const scaleFactor = 0.7 + edgeWeight * 1.5;
            return {
              ...data,
              color: withAlpha(baseColor, 0.45 + edgeWeight * 0.55),
              size: (data.size as number) * scaleFactor,
            };
          }
        }

        // Selected node — always wins
        if (id === selectedId) {
          return { ...data, color: '#FFFFFF', highlighted: true };
        }

        // Hover focus — brighten hovered node + direct neighbors, dim the rest.
        // Reads the ref live (reducers re-run via scheduleRefresh on enter/leave).
        const hovered = hoveredNodeRef.current;
        if (hovered != null && !egoActive) {
          if (id === hovered) return { ...data, highlighted: true };
          const graph = sigma.getGraph();
          const isNeighbor =
            graph.hasEdge(String(hovered), node) || graph.hasEdge(node, String(hovered));
          if (!isNeighbor) return { ...data, color: withAlpha(baseColor, 0.18) };
          return data; // neighbor keeps full color
        }

        return data; // neutral path — no allocation
      },

      edgeReducer: (edge, data) => {
        const hovered = hoveredNodeRef.current;

        // No ego, no hover → leave edges as-is (cheap default path)
        if (!egoActive && hovered == null) return data;

        const graph = sigma.getGraph();
        const [src, tgt] = graph.extremities(edge);
        const srcId = Number(src);
        const tgtId = Number(tgt);
        const w = (data.weight as number) ?? 0;

        // Hover focus (takes precedence when not in ego mode)
        if (hovered != null && !egoActive) {
          if (srcId === hovered || tgtId === hovered) {
            return { ...data, color: `${EGO_EDGE_BASE}${(0.3 + w * 0.4).toFixed(2)})`, size: 0.8 + w * 2.2 };
          }
          return { ...data, color: FADED_EDGE, size: 0.2 };
        }

        // Ego mode
        const isEgoEdge = egoNeighborIds.has(srcId) && egoNeighborIds.has(tgtId);
        if (isEgoEdge) {
          return { ...data, color: `${EGO_EDGE_BASE}${(0.25 + w * 0.45).toFixed(2)})`, size: 0.8 + w * 2.2 };
        }
        return { ...data, color: FADED_EDGE, size: 0.2 };
      },
    });

    scheduleRefresh();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, egoNeighborIds, filterState, communityColorMap]);

  return null;
}
