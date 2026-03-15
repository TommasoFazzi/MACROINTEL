import type { Metadata } from 'next';
import Link from 'next/link';
import { BookOpen, Calendar, ArrowRight } from 'lucide-react';
import { Navbar } from '@/components/landing';

export const metadata: Metadata = {
  title: 'Intelligence Briefings | MACROINTEL',
  description:
    'AI-generated geopolitical intelligence briefings covering global risks, cyber threats, and macro-economic signals. Updated daily.',
  openGraph: {
    title: 'Intelligence Briefings | MACROINTEL',
    description:
      'AI-generated geopolitical intelligence briefings covering global risks, cyber threats, and macro-economic signals. Updated daily.',
    type: 'website',
  },
  alternates: {
    canonical: 'https://macrointel.net/insights',
  },
};

interface InsightItem {
  id: number;
  slug: string;
  title: string;
  published_at: string | null;
  category: string | null;
  summary_preview: string;
}

async function getInsights(): Promise<InsightItem[]> {
  const base =
    process.env.INTELLIGENCE_API_URL || 'http://localhost:8000';
  try {
    const res = await fetch(`${base}/api/v1/insights?limit=20`, {
      next: { revalidate: 3600 }, // revalidate every hour
    });
    if (!res.ok) return [];
    const data = await res.json();
    return data.insights ?? [];
  } catch {
    return [];
  }
}

function formatDate(iso: string | null): string {
  if (!iso) return '';
  return new Date(iso).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  });
}

export default async function InsightsPage() {
  const insights = await getInsights();

  return (
    <>
      <Navbar />
      <main className="min-h-screen bg-[#0A1628] pt-28 pb-20">
        <div className="max-w-4xl mx-auto px-6">
          {/* Header */}
          <div className="mb-12">
            <div className="flex items-center gap-2 text-[#FF6B35] mb-4">
              <BookOpen className="w-5 h-5" />
              <span className="text-sm font-semibold uppercase tracking-wider">Intelligence Briefings</span>
            </div>
            <h1 className="text-4xl md:text-5xl font-extrabold text-white mb-4">
              Latest Intelligence
            </h1>
            <p className="text-gray-400 text-lg">
              AI-generated geopolitical briefings from MACROINTEL — updated daily from 33+ sources.
            </p>
          </div>

          {/* Insights list */}
          {insights.length === 0 ? (
            <div className="text-center py-20">
              <p className="text-gray-500">No public briefings available yet.</p>
              <p className="text-gray-600 text-sm mt-2">Check back soon — reports are published daily.</p>
            </div>
          ) : (
            <div className="space-y-6">
              {insights.map((insight) => (
                <article
                  key={insight.id}
                  className="group relative p-6 rounded-xl border border-white/8 bg-[#1a2332]/60 hover:border-[#FF6B35]/30 hover:bg-[#1a2332]/90 transition-all duration-300"
                >
                  <div className="absolute inset-x-0 top-0 h-[1px] rounded-t-xl bg-gradient-to-r from-transparent via-[#FF6B35]/50 to-transparent opacity-0 group-hover:opacity-100 transition-opacity" />

                  {/* Meta */}
                  <div className="flex items-center gap-3 mb-3 flex-wrap">
                    {insight.category && (
                      <span className="text-xs font-medium px-2.5 py-1 rounded-full bg-[#FF6B35]/10 text-[#FF6B35] border border-[#FF6B35]/20">
                        {insight.category}
                      </span>
                    )}
                    {insight.published_at && (
                      <span className="flex items-center gap-1 text-xs text-gray-500">
                        <Calendar className="w-3 h-3" />
                        <time dateTime={insight.published_at}>{formatDate(insight.published_at)}</time>
                      </span>
                    )}
                  </div>

                  {/* Title */}
                  <h2 className="text-xl font-bold text-white mb-2 group-hover:text-[#FF6B35] transition-colors">
                    <Link href={`/insights/${insight.slug}`} className="stretched-link">
                      {insight.title}
                    </Link>
                  </h2>

                  {/* Preview */}
                  <p className="text-gray-400 text-sm leading-relaxed line-clamp-2 mb-4">
                    {insight.summary_preview}
                  </p>

                  {/* CTA */}
                  <Link
                    href={`/insights/${insight.slug}`}
                    className="inline-flex items-center gap-1 text-sm text-[#FF6B35] hover:text-[#F77F00] transition-colors font-medium"
                  >
                    Read analysis
                    <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
                  </Link>
                </article>
              ))}
            </div>
          )}

          {/* CTA to waitlist */}
          <div className="mt-16 text-center p-8 rounded-xl border border-white/8 bg-[#1a2332]/40">
            <p className="text-gray-400 mb-3">Want real-time access to the full intelligence database?</p>
            <Link
              href="/#waitlist"
              className="inline-flex items-center gap-2 px-6 py-3 bg-[#FF6B35] hover:bg-[#F77F00] text-white rounded-lg font-medium transition-colors text-sm"
            >
              Request Access
              <ArrowRight className="w-4 h-4" />
            </Link>
          </div>
        </div>
      </main>
    </>
  );
}
