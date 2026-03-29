'use client';

import useSWR from 'swr';
import type { DashboardStatsResponse, ReportsResponse, ReportDetailResponse, ReportComparisonResponse, ApiError } from '@/types/dashboard';

/**
 * SWR fetcher with error handling and timeout
 */
const fetcher = async <T>(url: string): Promise<T> => {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 10000); // 10s timeout

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

    // Check if it's an abort error or network error
    if (err instanceof Error) {
      if (err.name === 'AbortError') {
        const error = new Error('Request timeout') as ApiError;
        error.isOffline = typeof navigator !== 'undefined' && !navigator.onLine;
        throw error;
      }

      // Network error (API offline)
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
 * Hook for fetching dashboard statistics
 */
export function useDashboardStats() {
  const { data, error, isLoading, mutate } = useSWR<DashboardStatsResponse, ApiError>(
    '/api/proxy/dashboard/stats',
    fetcher,
    {
      revalidateOnFocus: false,         // Don't re-fetch on tab focus
      dedupingInterval: 60000,          // Dedupe requests within 60s
      errorRetryCount: 2,
      errorRetryInterval: 10000,
      shouldRetryOnError: (err: ApiError) => !err.isOffline,
    }
  );

  return {
    stats: data?.data,
    generatedAt: data?.generated_at,
    isLoading,
    error,
    refresh: mutate,
  };
}

/**
 * Hook for fetching reports with pagination
 */
export function useReports(page: number = 1, perPage: number = 10) {
  const { data, error, isLoading, mutate } = useSWR<ReportsResponse, ApiError>(
    `/api/proxy/reports?page=${page}&per_page=${perPage}`,
    fetcher,
    {
      revalidateOnFocus: false,         // Don't re-fetch on tab focus
      dedupingInterval: 60000,          // Dedupe requests within 60s
      errorRetryCount: 2,
      keepPreviousData: true,           // Keep previous data while loading new page
      shouldRetryOnError: (err: ApiError) => !err.isOffline,
    }
  );

  return {
    reports: data?.data?.reports,
    pagination: data?.data?.pagination,
    generatedAt: data?.generated_at,
    isLoading,
    error,
    refresh: mutate,
  };
}

/**
 * Hook for fetching a single report detail
 */
export function useReportDetail(reportId: number | null) {
  const { data, error, isLoading } = useSWR<ReportDetailResponse, ApiError>(
    reportId ? `/api/proxy/reports/${reportId}` : null,
    fetcher,
    {
      revalidateOnFocus: false,
      dedupingInterval: 120000,         // Cache report detail for 2 minutes
      errorRetryCount: 2,
      shouldRetryOnError: (err: ApiError) => !err.isOffline,
    }
  );

  return {
    report: data?.data,
    isLoading,
    error,
  };
}

/**
 * Hook for fetching total count of active storylines
 */
export function useStoriesCount() {
  const { data } = useSWR(
    '/api/proxy/stories?per_page=1',
    fetcher,
    {
      revalidateOnFocus: false,
      dedupingInterval: 300000,         // Cache for 5 minutes
      errorRetryCount: 1,
    }
  );
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return (data as any)?.data?.pagination?.total ?? null;
}

/**
 * Hook for comparing two reports and getting delta analysis
 * Key is null when either ID is null, disabling the SWR hook
 */
export function useReportCompare(reportIdA: number | null, reportIdB: number | null) {
  const key = reportIdA && reportIdB ? `/api/proxy/reports/compare?ids=${reportIdA},${reportIdB}` : null;
  const { data, error, isLoading } = useSWR<ReportComparisonResponse, ApiError>(
    key,
    fetcher,
    {
      revalidateOnFocus: false,
      dedupingInterval: 86400000,       // Cache comparison for 24 hours (reports are static)
      errorRetryCount: 1,               // Minimal retry for expensive LLM call
      shouldRetryOnError: (err: ApiError) => !err.isOffline,
    }
  );

  return {
    comparison: data?.data,
    isLoading,
    error,
  };
}
