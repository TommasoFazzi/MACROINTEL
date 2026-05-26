'use client';

import { useEffect, useRef } from 'react';
import { useSigma } from '@react-sigma/core';
import type { GraphNetwork } from '@/types/stories';

const LAYOUT_STORAGE_KEY = 'story-graph-layout-v4';
const LAYOUT_HASH_KEY = 'story-graph-hash-v4';
const FA2_DURATION_MS = 12000;
const LAYOUT_READY_DELAY_MS = 500;

interface GraphDataLoaderProps {
  graphData: GraphNetwork | null;
  communityColorMap: Map<number, string>;
  onLayoutReady: () => void;
  onOptimizing: (v: boolean) => void;
}

export default function GraphDataLoader({
  graphData,
  communityColorMap,
  onLayoutReady,
  onOptimizing,
}: GraphDataLoaderProps) {
  const sigma = useSigma();
  const fa2Ref = useRef<InstanceType<typeof import('graphology-layout-forceatlas2/worker').default> | null>(null);
  const stopTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const readyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!graphData) return;

    const graph = sigma.getGraph();
    graph.clear();

    // Lightweight signature of the graph data to detect changes. Counts alone
    // collide when a storyline is swapped for another with the same totals, so
    // fold the sorted node ids into the hash (FNV-1a) before persisting layout.
    const idSignature = graphData.nodes
      .map((n) => n.id)
      .sort((a, b) => a - b)
      .join(',');
    let h = 0x811c9dc5;
    for (let i = 0; i < idSignature.length; i++) {
      h ^= idSignature.charCodeAt(i);
      h = Math.imul(h, 0x01000193);
    }
    const dataHash = `${graphData.nodes.length}:${graphData.links.length}:${(h >>> 0).toString(36)}`;

    // Attempt to restore saved layout positions from localStorage
    let savedPositions: Record<string, { x: number; y: number }> = {};
    let layoutMatchesData = false;
    try {
      const raw = localStorage.getItem(LAYOUT_STORAGE_KEY);
      const savedHash = localStorage.getItem(LAYOUT_HASH_KEY);
      if (raw) savedPositions = JSON.parse(raw);
      layoutMatchesData = savedHash === dataHash && Object.keys(savedPositions).length > 0;
    } catch {
      // ignore
    }

    // Build nodes
    for (const node of graphData.nodes) {
      const color = communityColorMap.get(node.community_id ?? -1) ?? '#2A3A4A';
      const size = 4 + node.momentum_score * 12;
      const saved = savedPositions[String(node.id)];
      graph.addNode(String(node.id), {
        label: node.title,
        size,
        color,
        momentum_score: node.momentum_score,
        community_id: node.community_id ?? null,
        narrative_status: node.narrative_status,
        key_entities: node.key_entities,
        x: saved?.x ?? (Math.random() - 0.5) * 1000,
        y: saved?.y ?? (Math.random() - 0.5) * 1000,
      });
    }

    // Build edges
    const nodeIds = new Set(graphData.nodes.map((n) => String(n.id)));
    for (const link of graphData.links) {
      const src = String(link.source);
      const tgt = String(link.target);
      if (!nodeIds.has(src) || !nodeIds.has(tgt)) continue;
      const alpha = 0.04 + link.weight * 0.10;
      graph.addEdge(src, tgt, {
        weight: link.weight,
        size: 0.4 + link.weight * 1.4,
        color: `rgba(150,190,220,${alpha.toFixed(2)})`,
      });
    }

    // Clear any running layout + timers
    if (fa2Ref.current) {
      try { fa2Ref.current.kill(); } catch { /* ignore */ }
      fa2Ref.current = null;
    }
    if (stopTimerRef.current) clearTimeout(stopTimerRef.current);
    if (readyTimerRef.current) clearTimeout(readyTimerRef.current);

    let workerStarted = false;
    try {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const FA2Worker = require('graphology-layout-forceatlas2/worker').default;
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const { inferSettings } = require('graphology-layout-forceatlas2');
      // Tuned to SPREAD the graph instead of collapsing it into a central ball:
      // higher repulsion (scalingRatio), no strong gravity, mild centering, and
      // outboundAttractionDistribution to push high-degree hubs apart.
      const settings = {
        ...inferSettings(graph),
        barnesHutOptimize: true,
        barnesHutTheta: 0.6,
        scalingRatio: 14,
        strongGravityMode: false,
        gravity: 0.18, // pull isolated "lone star" nodes inward — kills the empty halo
        outboundAttractionDistribution: true,
        edgeWeightInfluence: 0.5,
        slowDown: 5,
      };

      fa2Ref.current = new FA2Worker(graph, { settings });
      // Skip FA2 if layout already matches current data — avoids re-randomizing on every reload
      if (!layoutMatchesData) {
        fa2Ref.current!.start();
        workerStarted = true;
        onOptimizing(true);
      }
    } catch {
      // FA2 worker failed (path resolution in some Next.js builds) — use sync fallback
      try {
        // eslint-disable-next-line @typescript-eslint/no-require-imports
        const { assign, inferSettings } = require('graphology-layout-forceatlas2');
        if (!layoutMatchesData) {
          assign(graph, {
            settings: {
              ...inferSettings(graph),
              barnesHutOptimize: true,
              scalingRatio: 14,
              strongGravityMode: false,
              gravity: 0.18,
              outboundAttractionDistribution: true,
              edgeWeightInfluence: 0.5,
            },
            iterations: 300,
          });
        }
      } catch { /* layout stays random */ }
    }

    // Signal UI ready after LAYOUT_READY_DELAY_MS (show graph immediately)
    readyTimerRef.current = setTimeout(() => {
      onLayoutReady();
    }, LAYOUT_READY_DELAY_MS);

    if (workerStarted) {
      // Stop worker after FA2_DURATION_MS, save positions to localStorage
      stopTimerRef.current = setTimeout(() => {
        if (fa2Ref.current) {
          try { fa2Ref.current.stop(); } catch { /* ignore */ }
        }
        onOptimizing(false);

        // Persist layout
        try {
          const positions: Record<string, { x: number; y: number }> = {};
          graph.forEachNode((node: string, attrs: Record<string, unknown>) => {
            positions[node] = { x: attrs.x as number, y: attrs.y as number };
          });
          localStorage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify(positions));
          localStorage.setItem(LAYOUT_HASH_KEY, dataHash);
        } catch { /* quota exceeded or SSR */ }
      }, FA2_DURATION_MS);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graphData]);

  useEffect(() => {
    return () => {
      if (fa2Ref.current) {
        try { fa2Ref.current.kill(); } catch { /* ignore */ }
        fa2Ref.current = null;
      }
      if (stopTimerRef.current) clearTimeout(stopTimerRef.current);
      if (readyTimerRef.current) clearTimeout(readyTimerRef.current);
    };
  }, []);

  return null;
}
