'use client';

import useSWR from 'swr';
import type { GraphNetworkResponse, StorylineDetailResponse, EgoNetworkResponse } from '@/types/stories';
import type { ApiError } from '@/types/dashboard';

const fetcher = async <T>(url: string): Promise<T> => {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 10000);

  try {
    const res = await fetch(url, {
      signal: controller.signal,
    });

    clearTimeout(timeoutId);

    if (!res.ok) {
      const error = new Error(`API error: ${res.status}`) as ApiError;
      error.status = res.status;
      error.isOffline = false;
      throw error;
    }

    return res.json();
  } catch (err) {
    clearTimeout(timeoutId);

    if (err instanceof Error) {
      if (err.name === 'AbortError') {
        const error = new Error('Request timeout') as ApiError;
        error.isOffline = typeof navigator !== 'undefined' && !navigator.onLine;
        throw error;
      }

      if (err.message === 'Failed to fetch' || err.name === 'TypeError') {
        const error = new Error('API non raggiungibile') as ApiError;
        error.isOffline = typeof navigator !== 'undefined' ? !navigator.onLine : true;
        throw error;
      }
    }

    throw err;
  }
};

/**
 * Hook for fetching the full narrative graph (nodes + links).
 * Polls every 60 seconds.
 */
export function useGraphNetwork() {
  // min_edge_weight raised from the API default (0.10) to 0.18: at 0.10 the graph
  // returns ~18k edges (11.9/node) and collapses into an unreadable hairball.
  // 0.18 keeps the meaningful TF-IDF links; isolated high-momentum nodes are still
  // kept server-side as "lone stars".
  const { data, error, isLoading, mutate } = useSWR<GraphNetworkResponse, ApiError>(
    '/api/proxy/stories/graph?min_edge_weight=0.18',
    fetcher,
    {
      refreshInterval: 60000,
      revalidateOnFocus: true,
      dedupingInterval: 10000,
      errorRetryCount: 3,
      errorRetryInterval: 5000,
      shouldRetryOnError: (err: ApiError) => !err.isOffline,
    }
  );

  return {
    graph: data?.data,
    isLoading,
    error,
    refresh: mutate,
  };
}

/**
 * Hook for fetching ego network for a single storyline (on-demand, no polling).
 * Returns center node + all neighbors + weak/strong edges for that node.
 */
export function useEgoNetwork(storylineId: number | null, minWeight: number = 0.05) {
  const { data, error, isLoading } = useSWR<EgoNetworkResponse, ApiError>(
    storylineId ? `/api/proxy/stories/${storylineId}/network?min_weight=${minWeight}` : null,
    fetcher,
    {
      revalidateOnFocus: false,
      dedupingInterval: 5000,
      errorRetryCount: 2,
      shouldRetryOnError: (err: ApiError) => !err.isOffline,
    }
  );

  return {
    egoNetwork: data?.data ?? null,
    isLoading,
    error,
  };
}

/**
 * Hook for fetching storyline detail (on-demand, no polling).
 */
export function useStorylineDetail(storylineId: number | null) {
  const { data, error, isLoading } = useSWR<StorylineDetailResponse, ApiError>(
    storylineId ? `/api/proxy/stories/${storylineId}` : null,
    fetcher,
    {
      revalidateOnFocus: false,
      dedupingInterval: 10000,
      errorRetryCount: 2,
      shouldRetryOnError: (err: ApiError) => !err.isOffline,
    }
  );

  return {
    detail: data?.data,
    isLoading,
    error,
  };
}

// ─── Ticker-related types ─────────────────────────────────────────────────

interface TickerEntry {
  name: string;
  ticker: string;
  exchange: string;
  aliases: string[];
  category: string;
}

interface TickerListData {
  categories: Record<string, TickerEntry[]>;
  total: number;
}

interface TickerListResponse {
  success: boolean;
  data: TickerListData;
  generated_at: string;
}

interface TickerThemeMatch {
  storyline_id: number;
  title: string;
  momentum_score: number;
  article_count: number;
  community_id: number | null;
}

interface TickerThemesData {
  ticker: string;
  name: string;
  themes: TickerThemeMatch[];
  days: number;
  total_themes: number;
}

interface TickerThemesResponse {
  success: boolean;
  data: TickerThemesData;
  generated_at: string;
}

/**
 * Hook for fetching all available tickers (on-demand, cached, no polling).
 * Returns tickers organized by category.
 */
export function useTickerList() {
  const { data, error, isLoading } = useSWR<TickerListResponse, ApiError>(
    '/api/proxy/stories/tickers',
    fetcher,
    {
      revalidateOnFocus: false,
      dedupingInterval: 300000, // cache for 5 minutes
      errorRetryCount: 2,
      shouldRetryOnError: (err: ApiError) => !err.isOffline,
    }
  );

  return {
    tickers: data?.data ?? null,
    isLoading,
    error,
  };
}

/**
 * Hook for fetching ticker-correlated storylines (on-demand, no polling).
 * Returns the top N storylines for a given ticker.
 * The key is null when ticker is null, which disables the SWR hook.
 */
export function useTickerThemes(ticker: string | null, topN: number = 5, days: number = 30) {
  const { data, error, isLoading } = useSWR<TickerThemesResponse, ApiError>(
    ticker ? `/api/proxy/stories/ticker/${ticker}?top_n=${topN}&days=${days}` : null,
    fetcher,
    {
      revalidateOnFocus: false,
      dedupingInterval: 60000, // cache for 1 minute
      errorRetryCount: 2,
      shouldRetryOnError: (err: ApiError) => !err.isOffline,
    }
  );

  return {
    themes: data?.data ?? null,
    isLoading,
    error,
  };
}
