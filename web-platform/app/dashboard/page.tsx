'use client';

import { useState } from 'react';
import { RefreshCw, Clock, Map } from 'lucide-react';
import { useDashboardStats, useReports, useStoriesCount } from '@/hooks/useDashboard';
import {
  StatsGrid,
  ReportsTable,
  StatsGridSkeleton,
  TableSkeleton,
  ErrorState,
} from '@/components/dashboard';
import { Navbar } from '@/components/landing';
import { Button } from '@/components/ui/button';
import Link from 'next/link';
import type { ApiError } from '@/types/dashboard';

function formatTimestamp(timestamp: string | undefined): string {
  if (!timestamp) return '-';
  return new Date(timestamp).toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

export default function DashboardPage() {
  const [page, setPage] = useState(1);

  const storiesCount = useStoriesCount();

  const {
    stats,
    generatedAt: statsGeneratedAt,
    isLoading: statsLoading,
    error: statsError,
    refresh: refreshStats,
  } = useDashboardStats();

  const {
    reports,
    pagination,
    generatedAt: reportsGeneratedAt,
    isLoading: reportsLoading,
    error: reportsError,
    refresh: refreshReports,
  } = useReports(page);

  // Type assertion for error handling
  const statsApiError = statsError as ApiError | undefined;
  const reportsApiError = reportsError as ApiError | undefined;

  // Check if fully offline
  const isOffline = statsApiError?.isOffline || reportsApiError?.isOffline;

  // Handle full refresh
  const handleRefresh = () => {
    refreshStats();
    refreshReports();
  };

  // Full page error state (API completely offline)
  if (isOffline) {
    return (
      <>
        <Navbar />
        <main className="min-h-screen bg-[#0A1628] pt-24 px-6">
          <div className="max-w-7xl mx-auto">
            <ErrorState type="offline" onRetry={handleRefresh} />
          </div>
        </main>
      </>
    );
  }

  // Both endpoints failing (but online)
  if (statsError && reportsError) {
    return (
      <>
        <Navbar />
        <main className="min-h-screen bg-[#0A1628] pt-24 px-6">
          <div className="max-w-7xl mx-auto">
            <ErrorState type="server" onRetry={handleRefresh} />
          </div>
        </main>
      </>
    );
  }

  const lastUpdate = statsGeneratedAt || reportsGeneratedAt;

  return (
    <>
      <Navbar />
      <main className="min-h-screen bg-[#0A1628] pt-24 px-6 pb-12">
        <div className="max-w-7xl mx-auto">
          {/* Header */}
          <header className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-8">
            <div>
              <h1 className="text-3xl font-bold text-white">
                <span className="text-[#FF6B35]">MACRO</span>INTEL Dashboard
              </h1>
              <p className="text-gray-400 mt-1">
                Intelligence platform overview
              </p>
            </div>

            <div className="flex items-center gap-4">
              {/* Last update timestamp */}
              {lastUpdate && (
                <div className="flex items-center gap-2 text-sm text-gray-400">
                  <Clock className="w-4 h-4" />
                  <span>Updated: {formatTimestamp(lastUpdate)}</span>
                </div>
              )}

              {/* Intelligence Map link */}
              <Button asChild variant="outline" size="sm" className="border-[#00A8E8]/30 text-[#00A8E8] hover:bg-[#00A8E8]/10 hover:text-[#00A8E8]">
                <Link href="/map" className="flex items-center gap-2">
                  <Map className="w-4 h-4" />
                  Intelligence Map
                </Link>
              </Button>

              {/* Manual refresh button */}
              <Button
                variant="outline"
                size="sm"
                onClick={handleRefresh}
                disabled={statsLoading || reportsLoading}
                className="border-white/10 text-gray-400 hover:text-white hover:bg-white/5"
              >
                <RefreshCw
                  className={`w-4 h-4 mr-2 ${
                    statsLoading || reportsLoading ? 'animate-spin' : ''
                  }`}
                />
                Refresh
              </Button>
            </div>
          </header>

          {/* Stats Section */}
          <section>
            {statsLoading && !stats ? (
              <StatsGridSkeleton />
            ) : statsError ? (
              <ErrorState
                type="partial"
                message="Failed to load statistics"
                onRetry={() => refreshStats()}
              />
            ) : (
              <StatsGrid stats={stats} storiesCount={storiesCount} />
            )}
          </section>

          {/* Reports Section */}
          <section className="mt-10">
            <h2 className="text-xl font-semibold text-white mb-4">
              Recent Reports
            </h2>

            {reportsLoading && !reports ? (
              <TableSkeleton rows={10} />
            ) : reportsError ? (
              <ErrorState
                type="partial"
                message="Failed to load reports"
                onRetry={() => refreshReports()}
              />
            ) : (
              <ReportsTable
                reports={reports}
                pagination={pagination}
                currentPage={page}
                onPageChange={setPage}
              />
            )}
          </section>
        </div>
      </main>
    </>
  );
}
