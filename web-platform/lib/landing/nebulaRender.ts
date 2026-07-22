/**
 * NEBULA RENDER — canvas primitives shared by the two scenes that draw the narrative graph.
 *
 * Scene 2 (LivingGraphScene, in the Hero) and Scene 1's closing act (SignalDescentCanvas,
 * IGNITION) must resolve to *the same picture*: the landing page's payoff is the reader
 * recognizing the shape they saw muted at the top of the page, now named and explained.
 * That only works if it is literally the same drawing code and literally the same palette —
 * a lookalike reimplementation would drift apart on the first tweak to either scene.
 *
 * This module is therefore the single home for:
 *   - the `RGB` tuple type (previously declared twice, in livingGraphLayout + signalDescentScene)
 *   - the community palette in canvas-ready RGB form (previously hex here, tuples there)
 *   - the nebula/edge drawing primitives themselves
 *
 * Pure functions over a 2D context: no DOM lookups, no state, no timers.
 */

import { COMMUNITY_PALETTE } from '@/lib/communityColors';

export type RGB = readonly [number, number, number];

export function hexToRgb(hex: string): RGB {
  const n = parseInt(hex.replace('#', ''), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/**
 * `COMMUNITY_PALETTE` in canvas form. Converted once at module load rather than per frame —
 * both scenes call `dataColor()` inside render loops.
 */
const PALETTE_RGB: readonly RGB[] = COMMUNITY_PALETTE.map(hexToRgb);

/** Nth palette entry, wrapping. Negative indices wrap too (hence the double modulo). */
export function dataColor(index: number): RGB {
  const n = PALETTE_RGB.length;
  return PALETTE_RGB[((index % n) + n) % n];
}

export function rgba(c: RGB, a: number): string {
  return `rgba(${Math.round(c[0])}, ${Math.round(c[1])}, ${Math.round(c[2])}, ${a})`;
}

export function lerpColor(a: RGB, b: RGB, t: number): RGB {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
}

/**
 * The soft cluster halo. A radial gradient rather than a blurred disc: `shadowBlur` at these
 * radii is an order of magnitude more expensive, and the three-stop falloff is what makes it
 * read as haze instead of a glowing ball.
 */
export function drawNebula(
  ctx: CanvasRenderingContext2D,
  cx: number,
  cy: number,
  r: number,
  color: RGB,
  alpha: number
) {
  if (r <= 0.5 || alpha <= 0.01) return;
  const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
  grad.addColorStop(0, rgba(color, alpha * 0.55));
  grad.addColorStop(0.45, rgba(color, alpha * 0.22));
  grad.addColorStop(1, rgba(color, 0));
  ctx.fillStyle = grad;
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.fill();
}

/**
 * Golden angle (~137.5°). The defining constant of phyllotaxis: because it's irrational
 * relative to a full turn, consecutive indices never line up, which is what produces an even
 * scatter with no visible structure.
 */
const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));

/**
 * Offset of the `index`-th member from its cluster centre.
 *
 * Both scenes previously stepped the angle by `2π / count`, which walks the circle smoothly
 * while `sqrt(index / count)` grows the radius — the two together trace an Archimedean
 * spiral, and since consecutive members land as angular neighbours the eye joins them into a
 * continuous arc. Every cluster rendered as a visible hook. The `sqrt` radius shows the
 * intent was a sunflower scatter all along; it just needs the golden angle to work.
 *
 * `jitterAngle` / `jitterRadius` are supplied by the caller's seeded PRNG so the result stays
 * deterministic while not looking mechanically perfect.
 */
export function clusterMemberOffset(
  index: number,
  count: number,
  spread: number,
  jitterAngle: number,
  jitterRadius: number
): { dx: number; dy: number } {
  const a = index * GOLDEN_ANGLE + jitterAngle;
  const r = spread * Math.sqrt((index + 0.5 + jitterRadius) / Math.max(1, count));
  return { dx: Math.cos(a) * r, dy: Math.sin(a) * r };
}

/**
 * Centre of the `index`-th cluster. A ring is the readable base — it keeps clusters from
 * overlapping and spreads them across the frame — but a *perfect* ring reads as a decorative
 * dial rather than a data structure, so both the angle and the radius get seeded wobble.
 */
export function clusterCentre(
  index: number,
  count: number,
  focusX: number,
  ringRadius: number,
  flattenY: number,
  jitterAngle: number,
  jitterRadius: number
): { cx: number; cy: number } {
  const a = (index / Math.max(1, count)) * Math.PI * 2 + jitterAngle;
  const r = ringRadius * jitterRadius;
  return { cx: focusX + Math.cos(a) * r, cy: 0.5 + Math.sin(a) * r * flattenY };
}

/** Point at `u` along a quadratic bezier — used for the bowed connections between clusters. */
export function bezierPoint(
  ax: number,
  ay: number,
  cxp: number,
  cyp: number,
  bx: number,
  by: number,
  u: number
) {
  const mt = 1 - u;
  return {
    x: mt * mt * ax + 2 * mt * u * cxp + u * u * bx,
    y: mt * mt * ay + 2 * mt * u * cyp + u * u * by,
  };
}
