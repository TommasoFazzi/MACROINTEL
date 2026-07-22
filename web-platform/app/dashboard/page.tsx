'use client';

import { useState } from 'react';
import { RefreshCw, Clock, HelpCircle } from 'lucide-react';
import { useDashboardStats, useReports, useStoriesCount } from '@/hooks/useDashboard';
import {
  StatsGrid,
  ReportsTable,
  StatsGridSkeleton,
  TableSkeleton,
  ErrorState,
} from '@/components/dashboard';
import { AppShell } from '@/components/shell';
import { Button } from '@/components/ui/button';
import { HelpModal } from '@/components/HelpModal';
import type { HelpSection } from '@/components/HelpModal';
import Link from 'next/link';
import type { ApiError } from '@/types/dashboard';
import { SOURCE_COUNT } from '@/lib/constants';

const DASHBOARD_GUIDE_SECTIONS: HelpSection[] = [
  {
    key: 'kpis',
    label: 'KPI Cards',
    labelColor: 'text-orange-300',
    bgColor: 'bg-orange-500/8 border-orange-500/15',
    content:
      `The four cards at the top show live platform metrics: total articles ingested, intelligence briefs generated, active narrative storylines being tracked, and ${SOURCE_COUNT} sources monitored. The "+N today" badge on articles confirms the daily pipeline ran successfully.`,
  },
  {
    key: 'reports',
    label: 'Reports Table',
    labelColor: 'text-blue-300',
    bgColor: 'bg-blue-500/8 border-blue-500/15',
    content:
      'Each row is a generated intelligence brief. The subtitle below the title is an extract from the opening paragraph. Click any row to open the full report with sections, trade signals, and source citations.',
  },
  {
    key: 'types',
    label: 'Report Types',
    labelColor: 'text-cyan-300',
    bgColor: 'bg-cyan-500/8 border-cyan-500/15',
    content:
      'Daily (orange) — generated every morning at 08:00 UTC covering the last 24h. Weekly (blue) — published on Sundays, synthesising the full week. Recap (purple) — monthly synthesis, generated after 4 weekly reports.',
    tip: 'Weekly and Recap reports are more analytical and suitable for strategic review.',
  },
  {
    key: 'navigation',
    label: 'Navigation',
    labelColor: 'text-green-300',
    bgColor: 'bg-green-500/8 border-green-500/15',
    content:
      'Intelligence Map — geospatial view of all tracked entities, coloured by type or community. Stories — force-directed graph of narrative storylines and their relationships. Oracle — conversational query engine over the full database.',
  },
  {
    key: 'oracle',
    label: 'Oracle',
    labelColor: 'text-purple-300',
    bgColor: 'bg-purple-500/8 border-purple-500/15',
    content:
      'Oracle is an RAG-augmented analysis engine. You can ask factual, analytical, narrative, market, comparative, or overview questions in natural language. Requires a free Gemini API key (configure in Oracle settings).',
    tip: 'E.g. "What happened in Ukraine this week?" or "Show me BULLISH signals on European defence"',
  },
];

function formatLastSync(timestamp: string | undefined): string {
  if (!timestamp) return '';
  const diff = Math.floor((Date.now() - new Date(timestamp).getTime()) / 60000);
  if (diff < 1) return 'Last sync: just now';
  if (diff < 60) return `Last sync: ${diff}m ago`;
  return `Last sync: ${Math.floor(diff / 60)}h ago`;
}

export default function DashboardPage() {
  const [page, setPage] = useState(1);
  const [showHelp, setShowHelp] = useState(false);

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
      <AppShell>
        <main className="min-h-screen bg-[#0A1628] px-6 pt-8">
          <div className="max-w-7xl mx-auto">
            <ErrorState type="offline" onRetry={handleRefresh} />
          </div>
        </main>
      </AppShell>
    );
  }

  // Both endpoints failing (but online)
  if (statsError && reportsError) {
    return (
      <AppShell>
        <main className="min-h-screen bg-[#0A1628] px-6 pt-8">
          <div className="max-w-7xl mx-auto">
            <ErrorState type="server" onRetry={handleRefresh} />
          </div>
        </main>
      </AppShell>
    );
  }

  const lastUpdate = statsGeneratedAt || reportsGeneratedAt;
  const lastSyncLabel = formatLastSync(lastUpdate);

  return (
    <AppShell>
      <main className="min-h-screen bg-[#0A1628] px-6 pb-12 pt-8">
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

            <div className="flex items-center gap-3">
              {/* Last sync timestamp */}
              {lastSyncLabel && (
                <div className="flex items-center gap-2 text-sm text-gray-500">
                  <Clock className="w-3.5 h-3.5" />
                  <span>{lastSyncLabel}</span>
                </div>
              )}

              {/* Guide button */}
              <Button
                variant="outline"
                size="sm"
                onClick={() => setShowHelp(true)}
                className="border-white/10 text-gray-400 hover:text-white hover:bg-white/5"
              >
                <HelpCircle className="w-4 h-4 mr-2" />
                Guide
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

      {/* Dashboard guide modal */}
      <HelpModal
        open={showHelp}
        onClose={() => setShowHelp(false)}
        title="Dashboard Guide"
        subtitle="How to use MacroIntel"
        intro={`The dashboard provides a live overview of the intelligence pipeline — articles ingested from ${SOURCE_COUNT} RSS feeds, reports generated daily, and narrative storylines continuously tracked.`}
        sections={DASHBOARD_GUIDE_SECTIONS}
      />
    </AppShell>
  );
}
