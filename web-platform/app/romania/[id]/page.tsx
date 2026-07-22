import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import Link from 'next/link';
import { Calendar, ArrowLeft, Globe } from 'lucide-react';
import { AppShell } from '@/components/shell';
import { editorialSerif } from '@/lib/fonts';
import { MarkdownContent } from '@/components/ui/MarkdownContent';

const BASE = process.env.INTELLIGENCE_API_URL || 'http://localhost:8000';

interface BriefingDetail {
  id: number;
  date: string;
  report_type: string;
  status: string;
  content: string;
  metadata: Record<string, unknown>;
}

async function getBriefing(id: string): Promise<BriefingDetail | null> {
  try {
    const res = await fetch(`${BASE}/api/v1/romania/briefings/${id}`, {
      next: { revalidate: 600 },
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
  params: Promise<{ id: string }>;
}): Promise<Metadata> {
  const { id } = await params;
  const briefing = await getBriefing(id);
  if (!briefing) return { title: 'Briefing non trovato | MACROINTEL' };

  const typeLabel = briefing.report_type === 'romania-daily' ? 'Giornaliero' : 'Settimanale';
  const dateStr = new Date(briefing.date).toLocaleDateString('it-IT', {
    day: '2-digit', month: 'long', year: 'numeric',
  });

  return {
    title: `Romania Intelligence ${typeLabel} — ${dateStr} | MACROINTEL`,
    description: `Briefing di intelligence economica sulla Romania del ${dateStr}.`,
    openGraph: {
      title: `Romania Intelligence ${typeLabel} — ${dateStr}`,
      description: `Briefing di intelligence economica sulla Romania del ${dateStr}.`,
      type: 'article',
    },
  };
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('it-IT', {
    day: '2-digit', month: 'long', year: 'numeric',
  });
}

export default async function RomaniaBriefingPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const briefing = await getBriefing(id);

  if (!briefing) notFound();

  const typeLabel = briefing.report_type === 'romania-daily' ? 'Giornaliero' : 'Settimanale';
  const macroHeader = briefing.metadata?.macro_header as string | undefined;

  return (
    <AppShell>
      <main className={`${editorialSerif.variable} min-h-screen bg-[#0A1628] pb-20 pt-8`}>
        <div className="max-w-3xl mx-auto px-6">

          {/* Back link */}
          <Link
            href="/romania"
            className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-blue-400 transition-colors mb-8"
          >
            <ArrowLeft className="w-4 h-4" />
            Romania Intelligence
          </Link>

          {/* Header */}
          <div className="mb-8">
            <div className="flex items-center gap-2 text-blue-400 mb-3">
              <Globe className="w-4 h-4" />
              <span className="text-xs font-semibold uppercase tracking-wider">
                Briefing {typeLabel}
              </span>
            </div>
            <h1 className="text-3xl md:text-4xl font-extrabold text-white mb-3">
              🇷🇴 Romania Intelligence
            </h1>
            <div className="flex items-center gap-2 text-gray-500 text-sm">
              <Calendar className="w-4 h-4" />
              <time dateTime={briefing.date}>{formatDate(briefing.date)}</time>
            </div>
          </div>

          {/* Macro header summary bar */}
          {macroHeader && (
            <div className="mb-8 px-4 py-3 rounded-lg bg-blue-900/20 border border-blue-400/15 text-sm text-blue-300 font-mono">
              {macroHeader}
            </div>
          )}

          {/* Content */}
          <article className="max-w-none text-gray-200">
            {briefing.content
              ? <MarkdownContent content={briefing.content} variant="editorial" className="text-gray-200" />
              : <p className="text-gray-500">Contenuto non disponibile.</p>
            }
          </article>

        </div>
      </main>
    </AppShell>
  );
}
