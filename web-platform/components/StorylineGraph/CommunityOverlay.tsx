'use client';

import { useEffect, useRef, MutableRefObject } from 'react';
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
  const lastUpdateRef = useRef(0);
  const THROTTLE_MS = 33; // ~30fps

  useEffect(() => {
    const sigma = sigmaRef.current;
    if (!sigma || !svgRef.current) return;

    // Group node graph-space positions by community
    const communityPoints = new Map<number, [number, number][]>();
    for (const node of nodes) {
      const cid = node.community_id;
      if (cid == null) continue;
      try {
        const attrs = sigma.getGraph().getNodeAttributes(String(node.id));
        if (attrs.x == null || attrs.y == null) continue;
        if (!communityPoints.has(cid)) communityPoints.set(cid, []);
        communityPoints.get(cid)!.push([attrs.x as number, attrs.y as number]);
      } catch {
        // node not in graph yet
      }
    }

    const updatePositions = () => {
      if (!sigmaRef.current || !svgRef.current) return;
      const now = Date.now();
      if (now - lastUpdateRef.current < THROTTLE_MS) return;
      lastUpdateRef.current = now;

      const svg = svgRef.current;
      // Clear existing children
      while (svg.firstChild) svg.removeChild(svg.firstChild);

      for (const [cid, graphPts] of communityPoints) {
        if (graphPts.length < 3) continue;
        const color = communityColorMap.get(cid) ?? '#2A3A4A';
        const label = communityLabels.find((l) => l.cid === cid)?.label ?? '';

        // Convert graph coords → screen coords
        const screenPts: [number, number][] = graphPts.map((pt) => {
          const vp = sigmaRef.current!.graphToViewport({ x: pt[0], y: pt[1] });
          return [vp.x, vp.y];
        });

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

    sigma.on('afterRender', updatePositions);
    // Run once immediately so hulls appear before the first interaction
    updatePositions();

    return () => {
      sigma.off('afterRender', updatePositions);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, communityColorMap, communityLabels]);

  return (
    <svg
      ref={svgRef}
      className="absolute inset-0 w-full h-full pointer-events-none"
      xmlns="http://www.w3.org/2000/svg"
    />
  );
}
