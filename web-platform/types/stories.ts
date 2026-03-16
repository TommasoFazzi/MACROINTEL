// Storyline & Graph API Response Types

export type NarrativeStatus = 'emerging' | 'active' | 'stabilized';

export interface StorylineNode {
  id: number;
  title: string;
  summary: string | null;
  category: string | null;
  narrative_status: NarrativeStatus;
  momentum_score: number;
  article_count: number;
  key_entities: string[];
  start_date: string | null;
  last_update: string | null;
  days_active: number | null;
  community_id?: number | null;
  community_name?: string | null;
}

export interface StorylineEdge {
  source: number;
  target: number;
  weight: number;
  relation_type: string;
}

export interface GraphStats {
  total_nodes: number;
  total_edges: number;
  avg_momentum: number;
  communities_count: number;
  avg_edges_per_node: number;
}

export interface GraphNetwork {
  nodes: StorylineNode[];
  links: StorylineEdge[];
  stats: GraphStats;
}

export interface GraphNetworkResponse {
  success: boolean;
  data: GraphNetwork;
  error: string | null;
  generated_at: string;
}

// Detail types

export interface RelatedStoryline {
  id: number;
  title: string;
  weight: number;
  relation_type: string;
}

export interface LinkedArticle {
  id: number;
  title: string;
  source: string | null;
  published_date: string | null;
  bullet_points?: string[];
}

export interface StorylineDetailData {
  storyline: StorylineNode;
  related_storylines: RelatedStoryline[];
  recent_articles: LinkedArticle[];
}

export interface StorylineDetailResponse {
  success: boolean;
  data: StorylineDetailData;
  error: string | null;
  generated_at: string;
}

// Ego network (returned by /stories/{id}/network)
export interface EgoNetworkData {
  center_node: StorylineNode;
  neighbors: StorylineNode[];
  edges: StorylineEdge[];
}

export interface EgoNetworkResponse {
  success: boolean;
  data: EgoNetworkData;
  generated_at: string;
}
