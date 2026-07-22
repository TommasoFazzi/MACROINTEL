'use client';

import { use, useState, useMemo, useCallback } from 'react';
import Link from 'next/link';
import { useReportDetail, useReports, useReportCompare } from '@/hooks/useDashboard';
import { parseReport } from '@/lib/parseReport';
import { AppShell } from '@/components/shell';
import { editorialSerif } from '@/lib/fonts';
import { MarketTickers } from '@/components/report/MarketTickers';
import { ComparisonDelta } from '@/components/report/ComparisonDelta';
import {
  TableOfContents,
  AccordionSection,
  SourcesSidebar,
} from '@/components/report/ReportSections';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import ReactMarkdown from 'react-markdown';
import {
  ArrowLeft,
  Calendar,
  Clock,
  AlertTriangle,
  Cpu,
  BookOpen,
  Link2,
  FileText,
  ExternalLink,
  X,
} from 'lucide-react';
import type { ApiError, ReportType } from '@/types/dashboard';

// ── Constants ──────────────────────────────────────────────────────────

const statusColors: Record<string, string> = {
  draft: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
  reviewed: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  approved: 'bg-green-500/20 text-green-400 border-green-500/30',
};

const typeColors: Record<string, string> = {
  daily: 'bg-[#FF6B35]/20 text-[#FF6B35] border-[#FF6B35]/30',
  weekly: 'bg-[#00A8E8]/20 text-[#00A8E8] border-[#00A8E8]/30',
};

function formatDate(dateString: string | null): string {
  if (!dateString) return '-';
  return new Date(dateString).toLocaleDateString('en-US', {
    day: '2-digit',
    month: 'long',
    year: 'numeric',
  });
}

// ── Skeleton ───────────────────────────────────────────────────────────

function ReportDetailSkeleton() {
  return (
    <div className="space-y-6">
      {/* Macro skeleton */}
      <Skeleton className="h-20 w-full bg-white/5 rounded-xl" />
      {/* 3-col skeleton */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        <div className="hidden lg:block lg:col-span-2 space-y-2">
          {[80, 60, 70, 55].map((w, i) => (
            <Skeleton key={i} className="h-4 bg-white/5" style={{ width: `${w}%` }} />
          ))}
        </div>
        <div className="lg:col-span-7 space-y-4">
          <Skeleton className="h-8 w-64 bg-white/5" />
          {[95, 88, 100, 76, 92, 85, 100, 70].map((w, i) => (
            <Skeleton key={i} className="h-4 bg-white/5" style={{ width: `${w}%` }} />
          ))}
        </div>
        <div className="hidden lg:block lg:col-span-3 space-y-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-12 bg-white/5" />
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Mobile Tabs ────────────────────────────────────────────────────────

type MobileTab = 'report' | 'sources';

// ── Page ───────────────────────────────────────────────────────────────

export default function ReportDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const reportId = parseInt(id, 10);
  const { report, isLoading, error } = useReportDetail(
    isNaN(reportId) ? null : reportId
  );

  const [mobileTab, setMobileTab] = useState<MobileTab>('report');
  const [activeSection, setActiveSection] = useState('');
  const [openSections, setOpenSections] = useState<Set<string>>(new Set());
  const [highlightedSource, setHighlightedSource] = useState<number | null>(null);
  const [compareId, setCompareId] = useState<number | null>(null);

  // Fetch reports for dropdown (show recent reports, max 50)
  const { reports: availableReports } = useReports(1, 50);

  // Fetch comparison report detail
  const { report: compareReport } = useReportDetail(compareId);

  // Fetch comparison delta analysis
  const { comparison, isLoading: compareLoading } = useReportCompare(
    report ? report.id : null,
    compareId
  );

  // Parse the markdown
  const parsed = useMemo(() => {
    if (!report?.content.full_text) return null;
    return parseReport(report.content.full_text);
  }, [report?.content.full_text]);

  // Parse compare report markdown
  const parsedCompare = useMemo(() => {
    if (!compareReport?.content.full_text) return null;
    return parseReport(compareReport.content.full_text);
  }, [compareReport?.content.full_text]);

  // Open Executive Summary by default
  useMemo(() => {
    if (parsed && parsed.sections.length > 0) {
      setOpenSections(new Set([parsed.sections[0].id]));
      setActiveSection(parsed.sections[0].id);
    }
  }, [parsed]);

  const toggleSection = useCallback((sectionId: string) => {
    setOpenSections((prev) => {
      const next = new Set(prev);
      if (next.has(sectionId)) {
        next.delete(sectionId);
      } else {
        next.add(sectionId);
      }
      return next;
    });
  }, []);

  const navigateTo = useCallback((id: string) => {
    setActiveSection(id);
    // Open the section if closed
    setOpenSections((prev) => {
      const next = new Set(prev);
      next.add(id);
      return next;
    });
    // Scroll to element
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, []);

  const apiError = error as ApiError | undefined;

  return (
    <AppShell>
      <main className={`${editorialSerif.variable} min-h-screen bg-[#0A1628] px-4 pb-12 pt-8 md:px-6`}>
        <div className="max-w-[1400px] mx-auto">
          {/* Back button */}
          <Button
            variant="ghost"
            size="sm"
            asChild
            className="text-gray-400 hover:text-white mb-4 -ml-2"
          >
            <Link href="/dashboard">
              <ArrowLeft className="w-4 h-4 mr-2" />
              Dashboard
            </Link>
          </Button>

          {/* Loading */}
          {isLoading && <ReportDetailSkeleton />}

          {/* Error */}
          {apiError && (
            <div className="flex flex-col items-center justify-center py-16 text-gray-400">
              <AlertTriangle className="w-12 h-12 mb-4 text-[#FF6B35]/60" />
              <p className="text-lg font-medium text-white mb-2">
                Unable to load report
              </p>
              <p className="text-sm">
                {apiError.status === 404
                  ? 'Report not found.'
                  : 'Server connection error.'}
              </p>
              <Button
                variant="outline"
                size="sm"
                asChild
                className="mt-4 border-white/10 text-gray-400 hover:text-white"
              >
                <Link href="/dashboard">Back to Dashboard</Link>
              </Button>
            </div>
          )}

          {/* Report content */}
          {report && parsed && (
            <>
              {/* ── Header ────────────────────────────────────────── */}
              <header className="mb-4 space-y-3">
                <div className="flex items-center gap-3 flex-wrap">
                  <Badge
                    variant="outline"
                    className={`capitalize ${statusColors[report.status] || ''}`}
                  >
                    {report.status}
                  </Badge>
                  <Badge
                    variant="outline"
                    className={`capitalize ${typeColors[report.report_type] || ''}`}
                  >
                    {report.report_type}
                  </Badge>
                  {report.model_used && (
                    <Badge
                      variant="outline"
                      className="bg-white/5 text-gray-400 border-white/10"
                    >
                      <Cpu className="w-3 h-3 mr-1" />
                      {report.model_used}
                    </Badge>
                  )}

                  {/* Compare with dropdown */}
                  <div className="flex items-center gap-2">
                    <select
                      value={compareId?.toString() ?? ''}
                      onChange={(e) => setCompareId(e.target.value ? Number(e.target.value) : null)}
                      className="bg-black/60 border border-white/10 rounded text-xs text-gray-300 px-2 py-2 min-h-[36px] hover:border-white/20 active:border-white/20 transition-colors"
                      aria-label="Compare with another report"
                    >
                      <option value="">Compare with...</option>
                      {availableReports
                        ?.filter((r) => r.id !== report.id && r.report_type === report.report_type)
                        .map((r) => (
                          <option key={r.id} value={r.id}>
                            {formatDate(r.report_date)} — {r.title || `Report ${r.id}`}
                          </option>
                        ))}
                    </select>
                    {compareId && (
                      <button
                        type="button"
                        onClick={() => setCompareId(null)}
                        className="text-gray-500 hover:text-gray-300 active:text-gray-300 transition-colors p-2 min-h-[36px] min-w-[36px] flex items-center justify-center"
                        title="Close comparison"
                      >
                        <X className="w-4 h-4" />
                      </button>
                    )}
                  </div>

                  <span className="flex items-center gap-2 text-sm text-gray-400 ml-auto">
                    <Calendar className="w-4 h-4" />
                    {formatDate(report.report_date)}
                  </span>
                </div>
                <h1 className="text-2xl md:text-3xl font-bold text-white">
                  {parsed.title || report.content.title || `Report ${formatDate(report.report_date)}`}
                </h1>
              </header>

              {/* ── Macro Dashboard Tickers ───────────────────────── */}
              {parsed.macro && <MarketTickers macro={parsed.macro} />}

              {/* ── Comparison Delta Banner ────────────────────────── */}
              {compareId && (
                <div className="mb-6 rounded-lg border border-blue-900/50 bg-blue-950/20 p-4">
                  <ComparisonDelta delta={comparison?.delta ?? null} isLoading={compareLoading} />
                </div>
              )}

              {/* ── Mobile tab switcher ───────────────────────────── */}
              <div className="flex gap-2 mb-4 lg:hidden">
                <Button
                  variant={mobileTab === 'report' ? 'default' : 'outline'}
                  size="sm"
                  onClick={() => setMobileTab('report')}
                  className={mobileTab === 'report' ? '' : 'border-white/10 text-gray-400'}
                >
                  <BookOpen className="w-4 h-4 mr-2" />
                  Report
                </Button>
                <Button
                  variant={mobileTab === 'sources' ? 'default' : 'outline'}
                  size="sm"
                  onClick={() => setMobileTab('sources')}
                  className={mobileTab === 'sources' ? '' : 'border-white/10 text-gray-400'}
                >
                  <Link2 className="w-4 h-4 mr-2" />
                  Sources ({report.sources.length})
                </Button>
              </div>

              {/* ── Layout: Single or Comparison ──────────────────── */}
              {compareId && parsedCompare ? (
                // COMPARISON LAYOUT: 2-column side-by-side
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                  {/* LEFT: Report A */}
                  <div className="space-y-3 overflow-y-auto h-[calc(100vh-200px)]">
                    <div className="sticky top-0 bg-[#0A1628] z-10 pb-2">
                      <h3 className="text-lg font-semibold text-white">
                        {parsed.title || report.content.title || formatDate(report.report_date)}
                      </h3>
                      <p className="text-xs text-gray-400">{formatDate(report.report_date)}</p>
                    </div>

                    {parsed.toc.length > 0 && (
                      <div className="mb-3">
                        <TableOfContents
                          entries={parsed.toc}
                          activeId={activeSection}
                          onNavigate={navigateTo}
                        />
                      </div>
                    )}

                    {parsed.sections.length > 0 ? (
                      parsed.sections.map((section) => (
                        <AccordionSection
                          key={section.id}
                          section={section}
                          isOpen={openSections.has(section.id)}
                          onToggle={() => toggleSection(section.id)}
                          onHoverArticle={setHighlightedSource}
                        />
                      ))
                    ) : (
                      <div className="prose prose-invert prose-sm max-w-none text-gray-300">
                        <ReactMarkdown>{report.content.full_text}</ReactMarkdown>
                      </div>
                    )}
                  </div>

                  {/* RIGHT: Report B (Compare) */}
                  <div className="space-y-3 overflow-y-auto h-[calc(100vh-200px)]">
                    <div className="sticky top-0 bg-[#0A1628] z-10 pb-2">
                      <h3 className="text-lg font-semibold text-white">
                        {parsedCompare?.title || compareReport?.content?.title || (compareReport && formatDate(compareReport.report_date)) || 'Report'}
                      </h3>
                      <p className="text-xs text-gray-400">{compareReport && formatDate(compareReport.report_date)}</p>
                    </div>

                    {parsedCompare?.toc && parsedCompare.toc.length > 0 && (
                      <div className="mb-3">
                        <TableOfContents
                          entries={parsedCompare.toc}
                          activeId={activeSection}
                          onNavigate={navigateTo}
                        />
                      </div>
                    )}

                    {parsedCompare?.sections && parsedCompare.sections.length > 0 ? (
                      parsedCompare.sections.map((section) => (
                        <AccordionSection
                          key={section.id}
                          section={section}
                          isOpen={openSections.has(section.id)}
                          onToggle={() => toggleSection(section.id)}
                          onHoverArticle={setHighlightedSource}
                        />
                      ))
                    ) : (
                      <div className="prose prose-invert prose-sm max-w-none text-gray-300">
                        <ReactMarkdown>{compareReport?.content?.full_text || ''}</ReactMarkdown>
                      </div>
                    )}
                  </div>
                </div>
              ) : (
                // STANDARD 3-COLUMN LAYOUT
                <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
                  {/* LEFT: Table of Contents (desktop only) */}
                  <div className={`hidden lg:block lg:col-span-2 ${mobileTab !== 'report' ? 'lg:block' : ''}`}>
                    {parsed.toc.length > 0 && (
                      <TableOfContents
                        entries={parsed.toc}
                        activeId={activeSection}
                        onNavigate={navigateTo}
                      />
                    )}
                  </div>

                  {/* CENTER: Accordion Sections */}
                  <div
                    className={`lg:col-span-7 space-y-3 ${mobileTab !== 'report' ? 'hidden lg:block' : ''}`}
                  >
                  {/* Mobile horizontal TOC */}
                  {parsed.toc.length > 0 && (
                    <div className="flex gap-2 overflow-x-auto pb-2 lg:hidden scrollbar-thin scrollbar-thumb-white/10">
                      {parsed.toc.map((entry) => (
                        <button
                          key={entry.id}
                          onClick={() => navigateTo(entry.id)}
                          className={`whitespace-nowrap text-xs px-3 py-1.5 rounded-full border transition-colors ${
                            activeSection === entry.id
                              ? 'border-[#00A8E8]/50 bg-[#00A8E8]/10 text-[#00A8E8]'
                              : 'border-white/10 text-gray-500 hover:text-gray-300'
                          }`}
                        >
                          {entry.title}
                        </button>
                      ))}
                    </div>
                  )}

                  {parsed.sections.length > 0 ? (
                    parsed.sections.map((section) => (
                      <AccordionSection
                        key={section.id}
                        section={section}
                        isOpen={openSections.has(section.id)}
                        onToggle={() => toggleSection(section.id)}
                        onHoverArticle={setHighlightedSource}
                      />
                    ))
                  ) : (
                    <div className="prose prose-invert max-w-[68ch] font-serif text-[1.0625rem] leading-[1.7]
                      prose-p:font-serif prose-li:font-serif
                      prose-headings:text-white prose-headings:font-semibold
                      prose-p:text-gray-300 prose-p:leading-relaxed
                      prose-strong:text-white
                      prose-ul:text-gray-300 prose-ol:text-gray-300 prose-li:text-gray-300
                      prose-a:text-[#00A8E8] prose-a:no-underline hover:prose-a:underline
                      prose-blockquote:border-[#FF6B35]/50 prose-blockquote:text-gray-400
                      prose-code:text-[#00A8E8] prose-code:bg-[#00A8E8]/10 prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded
                    ">
                      <ReactMarkdown>{parsed.bodyMarkdown || report.content.full_text}</ReactMarkdown>
                    </div>
                  )}

                  {/* Metadata footer */}
                  <footer className="flex items-center gap-6 text-xs text-gray-500 mt-4 px-2">
                    <span>ID: {report.id}</span>
                    {report.metadata.token_count != null && (
                      <span>{report.metadata.token_count.toLocaleString()} tokens</span>
                    )}
                    {report.metadata.processing_time_ms != null && (
                      <span className="flex items-center gap-1">
                        <Clock className="w-3 h-3" />
                        {(report.metadata.processing_time_ms / 1000).toFixed(1)}s
                      </span>
                    )}
                  </footer>
                </div>

                {/* RIGHT: Sources sidebar */}
                <div
                  className={`lg:col-span-3 ${mobileTab !== 'sources' ? 'hidden lg:block' : ''}`}
                >
                  {report.sources.length > 0 ? (
                    <SourcesSidebar
                      sources={report.sources}
                      highlightedIdx={highlightedSource}
                    />
                  ) : (
                    <div className="rounded-xl border border-white/5 bg-white/[0.02] p-8 text-center">
                      <FileText className="w-10 h-10 text-gray-600 mx-auto mb-3" />
                      <p className="text-sm text-gray-500">
                        No sources available
                      </p>
                    </div>
                  )}

                  {/* Feedback (below sources) */}
                  {report.feedback.length > 0 && (
                    <div className="rounded-xl border border-white/5 bg-white/[0.02] p-4 mt-4">
                      <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-widest mb-3">
                        Feedback
                      </h3>
                      <div className="space-y-2">
                        {report.feedback.map((fb, i) => (
                          <div
                            key={i}
                            className="p-2.5 rounded-lg bg-white/[0.02] border border-white/5"
                          >
                            <div className="flex items-center justify-between mb-1">
                              <span className="text-xs text-gray-400 capitalize">{fb.section}</span>
                              {fb.rating && (
                                <span className="text-yellow-400 text-xs">
                                  {'★'.repeat(fb.rating)}{'☆'.repeat(5 - fb.rating)}
                                </span>
                              )}
                            </div>
                            {fb.comment && (
                              <p className="text-xs text-gray-300">{fb.comment}</p>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </div>
              )}
            </>
          )}
        </div>
      </main>
    </AppShell>
  );
}
