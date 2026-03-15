import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import Link from 'next/link';
import { Calendar, ArrowLeft, Lock, ArrowRight } from 'lucide-react';
import { Navbar } from '@/components/landing';
import WaitlistInline from '@/components/insights/WaitlistInline';

const BASE = process.env.INTELLIGENCE_API_URL || 'http://localhost:8000';

interface InsightDetail {
  id: number;
  slug: string;
  title: string;
  published_at: string | null;
  report_type: string;
  category: string | null;
  executive_summary: string;
  content_preview: string;
  is_truncated: boolean;
  summary_preview: string;
}

async function getInsight(slug: string): Promise<InsightDetail | null> {
  try {
    const res = await fetch(`${BASE}/api/v1/insights/${encodeURIComponent(slug)}`, {
      next: { revalidate: 3600 },
    });
    if (res.status === 404) return null;
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  const insight = await getInsight(slug);

  if (!insight) {
    return { title: 'Insight Not Found | MACROINTEL' };
  }

  return {
    title: `${insight.title} | MACROINTEL`,
    description: insight.summary_preview,
    alternates: {
      canonical: `https://macrointel.net/insights/${insight.slug}`,
    },
    openGraph: {
      title: insight.title,
      description: insight.summary_preview,
      type: 'article',
      publishedTime: insight.published_at ?? undefined,
      siteName: 'MACROINTEL',
    },
  };
}

function formatDate(iso: string | null): string {
  if (!iso) return '';
  return new Date(iso).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  });
}

export default async function InsightPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const insight = await getInsight(slug);

  if (!insight) notFound();

  // JSON-LD structured data for Google rich snippets
  const jsonLd = {
    '@context': 'https://schema.org',
    '@type': 'Article',
    headline: insight.title,
    author: { '@type': 'Organization', name: 'MACROINTEL' },
    publisher: {
      '@type': 'Organization',
      name: 'MACROINTEL',
      url: 'https://macrointel.net',
    },
    datePublished: insight.published_at,
    dateModified: insight.published_at,
    description: insight.summary_preview,
    url: `https://macrointel.net/insights/${insight.slug}`,
    mainEntityOfPage: {
      '@type': 'WebPage',
      '@id': `https://macrointel.net/insights/${insight.slug}`,
    },
  };

  return (
    <>
      <Navbar />
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />

      <main className="min-h-screen bg-[#0A1628] pt-28 pb-20">
        <div className="max-w-3xl mx-auto px-6">
          {/* Back link */}
          <Link
            href="/insights"
            className="inline-flex items-center gap-2 text-sm text-gray-500 hover:text-[#FF6B35] transition-colors mb-8"
          >
            <ArrowLeft className="w-4 h-4" />
            All briefings
          </Link>

          {/* Meta */}
          <div className="flex items-center gap-3 mb-4 flex-wrap">
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
          <h1 className="text-3xl md:text-4xl font-extrabold text-white mb-8 leading-tight">
            {insight.title}
          </h1>

          {/* Executive Summary (always visible) */}
          <section className="mb-8">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-[#FF6B35] mb-3">
              Executive Summary
            </h2>
            <div className="prose prose-invert prose-sm max-w-none text-gray-300 leading-relaxed whitespace-pre-wrap">
              {insight.executive_summary}
            </div>
          </section>

          {/* Content preview (first half of body) */}
          {insight.content_preview && (
            <section className="mb-8">
              <div className="prose prose-invert prose-sm max-w-none text-gray-400 leading-relaxed whitespace-pre-wrap">
                {insight.content_preview}
              </div>
            </section>
          )}

          {/* Value First paywall — CTA box with inline email capture */}
          {insight.is_truncated && (
            <div className="relative rounded-xl border border-[#FF6B35]/30 bg-[#1a2332]/80 p-8 my-8">
              {/* Orange accent top */}
              <div className="absolute inset-x-0 top-0 h-[2px] rounded-t-xl bg-gradient-to-r from-transparent via-[#FF6B35] to-transparent" />

              <div className="flex items-center gap-2 text-[#FF6B35] mb-3">
                <Lock className="w-5 h-5" />
                <span className="font-semibold">Read the Full Analysis</span>
              </div>

              <p className="text-gray-400 text-sm mb-5">
                This briefing continues with detailed analysis, trade signal assessment, and narrative tracking data.
                Join the waitlist to receive your access code.
              </p>

              <WaitlistInline />

              <p className="text-gray-600 text-xs mt-4">
                Free. No credit card required. We&apos;ll send you an access code within 24 hours.
              </p>
            </div>
          )}

          {/* Authenticated users CTA */}
          <div className="mt-10 pt-8 border-t border-white/5 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
            <p className="text-gray-500 text-sm">
              Already have access?
            </p>
            <Link
              href="/dashboard"
              className="inline-flex items-center gap-2 px-5 py-2.5 bg-[#FF6B35]/10 hover:bg-[#FF6B35]/20 text-[#FF6B35] border border-[#FF6B35]/30 rounded-lg text-sm font-medium transition-colors"
            >
              Continue in MACROINTEL Dashboard
              <ArrowRight className="w-4 h-4" />
            </Link>
          </div>
        </div>
      </main>
    </>
  );
}
