/**
 * Cross-surface constants. Single source of truth so the same fact never
 * appears as five different numbers across the site (Hero, Footer, About,
 * layout metadata, OG image, dashboard guide all consumed this independently).
 */

// `config/feeds.yaml` defines 56 RSS feeds, but some are known to be broken
// at any given time (see CLAUDE.md feed-staleness notes). Static and rounded
// down on purpose: true regardless of API/build state, no build-time dependency.
export const SOURCE_COUNT = '50+';
