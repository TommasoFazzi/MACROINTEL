import type { Metadata } from 'next';
import { Suspense } from 'react';
import GraphLoader from '@/components/StorylineGraph/GraphLoader';
import GraphSkeleton from '@/components/StorylineGraph/GraphSkeleton';
import { AppShell } from '@/components/shell';

export const metadata: Metadata = {
  title: 'Narrative Graph',
  description: 'Interactive force-directed graph visualization of intelligence storylines, their connections, and narrative evolution over time.',
  openGraph: {
    title: 'Narrative Graph | MACROINTEL',
    description: 'Interactive narrative graph visualization showing intelligence storylines and their connections.',
    type: 'website',
  },
};

/**
 * Stories Page - Server Component wrapper
 *
 * Architecture:
 * 1. Page.tsx (Server) - handles metadata/SEO + Suspense for useSearchParams
 * 2. GraphLoader (Client) - handles dynamic import + URL params
 * 3. StorylineGraph (Client) - the force-directed graph
 */
export default function StoriesPage() {
  return (
    <AppShell fullBleed>
      <main className="w-full h-[100dvh]">
        <Suspense fallback={<GraphSkeleton />}>
          <GraphLoader />
        </Suspense>
      </main>
    </AppShell>
  );
}
