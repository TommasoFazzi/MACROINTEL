/**
 * Shared community color palette.
 *
 * Used by both StorylineGraph (/stories) and TacticalMap (/map) so that
 * community colors are visually consistent across views.
 *
 * Assignment strategy:
 *   - StorylineGraph: rank-based (largest communities get lowest indices)
 *   - TacticalMap:    id-based (community_id % COMMUNITY_PALETTE.length)
 *
 * Both use the same palette, ensuring the same set of colours appears
 * on both pages even if the exact assignment differs.
 *
 * Mirrored 1:1 as CSS custom properties `--data-1`..`--data-15` (+ `--data-other`,
 * `--data-singleton`) in app/globals.css, so the landing page's live-graph scene
 * (canvas, no sigma) reads the same colors as /stories. Keep both in sync manually —
 * sigma/mapbox/canvas consumers need literal JS color strings, not CSS var() refs.
 */

export const COMMUNITY_PALETTE: readonly string[] = [
  '#FF6B35', // 0  orange
  '#00A8E8', // 1  cyan
  '#39D353', // 2  green
  '#FFD700', // 3  gold
  '#FF4081', // 4  pink
  '#7B61FF', // 5  purple
  '#00E5CC', // 6  teal
  '#FF7043', // 7  deep-orange
  '#E040FB', // 8  magenta
  '#00BFA5', // 9  emerald
  '#FFAB40', // 10 amber
  '#448AFF', // 11 blue
  '#FF5252', // 12 red
  '#69F0AE', // 13 mint
  '#40C4FF', // 14 light-blue
] as const;

// "Others" bucket — a real community that fell outside the top-N palette.
export const COMMUNITY_OTHER = '#2A3A4A';

// Singleton / isolated storyline (community_id === null, degree=0). Distinct
// neutral gray so isolated nodes read differently from the "Others" bucket.
// See Phase 1F task 1.23 / design.md § Decision 12.
export const SINGLETON_COLOR = '#6B7280';

/**
 * Return a deterministic colour for a community ID.
 * Null / undefined IDs (isolated storylines) get SINGLETON_COLOR (neutral gray).
 */
export function communityColor(id: number | null | undefined): string {
  if (id == null) return SINGLETON_COLOR;
  return COMMUNITY_PALETTE[((id % COMMUNITY_PALETTE.length) + COMMUNITY_PALETTE.length) % COMMUNITY_PALETTE.length];
}

/**
 * Build a Mapbox GL JS 'match' expression that maps community_id → colour.
 * Covers ids 0–(TOP-1); everything else maps to COMMUNITY_OTHER.
 *
 * Usage in a paint property:
 *   'circle-color': buildCommunityColorExpr()
 */
export function buildCommunityColorExpr(top = 30): mapboxgl.Expression {
  const expr: unknown[] = ['match', ['%', ['coalesce', ['get', 'primary_community_id'], -1], COMMUNITY_PALETTE.length]];
  for (let i = 0; i < COMMUNITY_PALETTE.length; i++) {
    expr.push(i, COMMUNITY_PALETTE[i]);
  }
  expr.push(COMMUNITY_OTHER);
  return expr as mapboxgl.Expression;
}
