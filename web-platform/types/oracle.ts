/**
 * Oracle 2.0 TypeScript interfaces
 */

export interface OracleSource {
  type: 'REPORT' | 'ARTICOLO';
  id: number | null;
  title: string;
  date_str?: string;
  similarity: number;
  status?: string;
  preview?: string;
  link?: string;
  source?: string;
}

export interface ExecutionStep {
  tool_name: string;
  parameters: Record<string, unknown>;
  is_critical: boolean;
  description: string;
}

export interface QueryPlan {
  intent: 'factual' | 'analytical' | 'narrative' | 'market' | 'comparative' | 'overview';
  complexity: 'simple' | 'medium' | 'complex';
  tools: string[];
  execution_steps: ExecutionStep[];
  estimated_time: number;
  requires_decomposition: boolean;
  sub_queries?: string[];
}

export interface OracleResponse {
  answer: string;
  sources: OracleSource[];
  query_plan?: QueryPlan;
  mode: string;
  metadata: {
    query: string;
    session_id: string;
    is_follow_up: boolean;
    execution_time: number;
    tools_executed: string[];
    timestamp: string;
  };
}

export interface OracleChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  sources?: OracleSource[];
  query_plan?: QueryPlan;
  metadata?: Record<string, unknown>;
}

export interface OracleChatFilters {
  mode?: 'both' | 'factual' | 'strategic';
  search_type?: 'vector' | 'keyword' | 'hybrid';
  start_date?: string;
  end_date?: string;
  categories?: string[];
  gpe_filter?: string[];
}

export interface OracleActiveFilters {
  mode?: 'both' | 'factual' | 'strategic';
  search_type?: 'vector' | 'keyword' | 'hybrid';
  start_date?: string;
  end_date?: string;
  gpe_filter?: string[];
}
