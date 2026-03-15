import type { Metadata } from 'next';
import GraphLoader from '@/components/StorylineGraph/GraphLoader';

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
 * 1. Page.tsx (Server) - handles metadata/SEO
 * 2. GraphLoader (Client) - handles dynamic import
 * 3. StorylineGraph (Client) - the force-directed graph
 */
export default function StoriesPage() {
  return (
    <main className="w-full h-screen">
      <GraphLoader />
    </main>
  );
}
