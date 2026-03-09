/**
 * TypeScript types for Intelligence Map
 */

export interface EntityFeature {
  type: 'Feature';
  geometry: {
    type: 'Point';
    coordinates: [number, number]; // [lng, lat]
  };
  properties: {
    id: number;
    name: string;
    entity_type: string;
    mention_count: number;
    metadata: Record<string, any>;
    first_seen: string | null;
    last_seen: string | null;
  };
}

export interface EntityCollection {
  type: 'FeatureCollection';
  features: EntityFeature[];
  total_count: number;
  filtered_count: number;
}

export interface EntityStoryline {
  id: number;
  title: string;
  narrative_status: string;
  momentum_score: number;
  article_count: number;
  community_id: number | null;
}

export interface EntityDetails {
  id: number;
  name: string;
  entity_type: string;
  latitude: number | null;
  longitude: number | null;
  mention_count: number;
  first_seen: string | null;
  last_seen: string | null;
  metadata: Record<string, any>;
  related_articles: Article[];
  related_storylines: EntityStoryline[];
}

export interface Article {
  id: number;
  title: string;
  link: string;
  published_date: string | null;
  source: string;
}

export interface MapStats {
  total_entities: number;
  geocoded_entities: number;
  active_storylines: number;
  entity_types: Record<string, number>;
}

/**
 * Entity type color palette — consistent across map markers, labels, and dossier.
 */
export const ENTITY_TYPE_COLORS: Record<string, string> = {
  GPE:    '#00D4FF', // cyan
  ORG:    '#FF6B35', // orange
  PERSON: '#A855F7', // purple
  LOC:    '#22C55E', // green
  FAC:    '#EF4444', // red
};

export const ENTITY_TYPE_LABELS: Record<string, string> = {
  GPE:    'Geopolitical Entity',
  ORG:    'Organization',
  PERSON: 'Person',
  LOC:    'Location',
  FAC:    'Facility',
};
