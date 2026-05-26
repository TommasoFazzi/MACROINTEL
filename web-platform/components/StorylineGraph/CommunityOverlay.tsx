'use client';

import { useEffect, useMemo, useRef, MutableRefObject } from 'react';
import type Sigma from 'sigma';
import type { StorylineNode } from '@/types/stories';

interface CommunityLabel {
  cid: number;
  label: string;
  count: number;
}

interface CommunityOverlayProps {
  sigmaRef: MutableRefObject<Sigma | null>;
  nodes: StorylineNode[];
  communityColorMap: Map<number, string>;
  communityLabels: CommunityLabel[];
}

// Jarvis March convex hull — O(nh), adequate for clusters ≤ 50 nodes
function convexHull(pts: [number, number][]): [number, number][] {
  if (pts.length < 3) return pts;
  let leftmost = 0;
  for (let i = 1; i < pts.length; i++) {
    if (pts[i][0] < pts[leftmost][0]) leftmost = i;
  }
  const hull: [number, number][] = [];
  let p = leftmost;
  do {
    hull.push(pts[p]);
    let q = (p + 1) % pts.length;
    for (let i = 0; i < pts.length; i++) {
      const cross =
        (pts[q][0] - pts[p][0]) * (pts[i][1] - pts[p][1]) -
        (pts[q][1] - pts[p][1]) * (pts[i][0] - pts[p][0]);
      if (cross < 0) q = i;
    }
    p = q;
  } while (p !== leftmost && hull.length <= pts.length);
  return hull;
}

export default function CommunityOverlay({
  sigmaRef,
  nodes,
  communityColorMap,
  communityLabels,
}: CommunityOverlayProps) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  // rAF-based throttle with trailing edge so the final frame after a pan/zoom
  // burst is never dropped (otherwise hulls stay misaligned until the next render).
  const rafRef = useRef<number | null>(null);

  // Static community → node-id grouping. Node *positions* are read fresh every
  // frame (they keep moving while FA2 runs), so we only memo the id buckets here.
  const labelByCid = useMemo(() => {
    const m = new Map<number, string>();
    for (const l of communityLabels) m.set(l.cid, l.label);
    return m;
  }, [communityLabels]);

  useEffect(() => {
    const sigma = sigmaRef.current;
    if (!sigma || !svgRef.current) return;

    // Bucket node ids by community once — cheap and stable across frames.
    const communityNodeIds = new Map<number, string[]>();
    for (const node of nodes) {
      const cid = node.community_id;
      if (cid == null) continue;
      if (!communityNodeIds.has(cid)) communityNodeIds.set(cid, []);
      communityNodeIds.get(cid)!.push(String(node.id));
    }

    const render = () => {
      rafRef.current = null;
      const s = sigmaRef.current;
      if (!s || !svgRef.current) return;
      const graph = s.getGraph();

      const svg = svgRef.current;
      while (svg.firstChild) svg.removeChild(svg.firstChild);

      for (const [cid, ids] of communityNodeIds) {
        if (ids.length < 3) continue;
        const color = communityColorMap.get(cid) ?? '#2A3A4A';
        const label = labelByCid.get(cid) ?? '';

        // Read CURRENT graph positions each frame, then project to screen space.
        const screenPts: [number, number][] = [];
        for (const id of ids) {
          let x: number, y: number;
          try {
            x = graph.getNodeAttribute(id, 'x') as number;
            y = graph.getNodeAttribute(id, 'y') as number;
          } catch {
            continue; // node not in graph yet
          }
          if (x == null || y == null) continue;
          const vp = s.graphToViewport({ x, y });
          screenPts.push([vp.x, vp.y]);
        }
        if (screenPts.length < 3) continue;

        const hull = convexHull(screenPts);
        if (hull.length < 3) continue;

        // Pad hull by 18px
        const cx = hull.reduce((s, p) => s + p[0], 0) / hull.length;
        const cy = hull.reduce((s, p) => s + p[1], 0) / hull.length;
        const padded = hull.map(([x, y]): [number, number] => {
          const dx = x - cx;
          const dy = y - cy;
          const len = Math.sqrt(dx * dx + dy * dy) || 1;
          return [x + (dx / len) * 18, y + (dy / len) * 18];
        });

        const points = padded.map(([x, y]) => `${x},${y}`).join(' ');

        // Polygon
        const poly = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
        poly.setAttribute('points', points);
        poly.setAttribute('fill', color);
        poly.setAttribute('fill-opacity', '0.06');
        poly.setAttribute('stroke', color);
        poly.setAttribute('stroke-opacity', '0.18');
        poly.setAttribute('stroke-width', '1.5');
        poly.setAttribute('stroke-dasharray', '4 3');
        svg.appendChild(poly);

        // Label at centroid
        if (label) {
          const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
          text.setAttribute('x', String(cx));
          text.setAttribute('y', String(cy));
          text.setAttribute('text-anchor', 'middle');
          text.setAttribute('dominant-baseline', 'middle');
          text.setAttribute('font-size', '13');
          text.setAttribute('font-weight', 'bold');
          text.setAttribute('font-family', '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif');
          text.setAttribute('fill', color);
          text.setAttribute('opacity', '0.28');
          text.setAttribute('pointer-events', 'none');
          text.textContent = label.toUpperCase();
          svg.appendChild(text);
        }
      }
    };

    // Throttle to one render per animation frame, but always honour the trailing
    // frame (afterRender fires faster than we want to redraw the SVG).
    const scheduleRender = () => {
      if (rafRef.current != null) return;
      rafRef.current = requestAnimationFrame(render);
    };

    sigma.on('afterRender', scheduleRender);
    render(); // draw once immediately so hulls appear before the first interaction

    return () => {
      sigma.off('afterRender', scheduleRender);
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, communityColorMap, communityLabels, labelByCid]);

  return (
    <svg
      ref={svgRef}
      className="absolute inset-0 w-full h-full pointer-events-none"
      xmlns="http://www.w3.org/2000/svg"
    />
  );
}
