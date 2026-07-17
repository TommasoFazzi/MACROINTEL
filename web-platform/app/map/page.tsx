import Link from 'next/link';
import type { Metadata } from 'next';
import { ArrowLeft, Construction } from 'lucide-react';

// Server-side metadata for SEO
export const metadata: Metadata = {
  title: 'Intelligence Map',
  description: 'The Intelligence Map is temporarily offline for maintenance.',
  robots: { index: false },
};

/**
 * Map Page — TEMPORARILY DISABLED (work in progress).
 *
 * The map data is stale and the section is not being maintained right now,
 * so the route renders a WIP banner instead of the map.
 *
 * To restore the map, revert this file to the previous version
 * (Suspense + MapLoader + MapSkeleton from components/IntelligenceMap/).
 */
export default function MapPage() {
  return (
    <main className="min-h-screen bg-[#0A1628] flex items-center justify-center px-6">
      {/* Background grid */}
      <div className="absolute inset-0 grid-overlay opacity-30" />

      {/* Glow orb */}
      <div
        className="absolute w-[400px] h-[400px] rounded-full blur-[100px] opacity-20"
        style={{
          background: 'radial-gradient(circle, #00A8E8 0%, transparent 70%)',
          top: '30%',
          left: '50%',
          transform: 'translate(-50%, -50%)',
        }}
      />

      <div className="relative z-10 text-center max-w-lg">
        {/* Icon */}
        <div className="w-24 h-24 mx-auto mb-8 flex items-center justify-center bg-[#00A8E8]/10 rounded-full">
          <Construction className="w-12 h-12 text-[#00A8E8] animate-pulse" />
        </div>

        {/* Status badge */}
        <div className="inline-flex items-center gap-2 px-4 py-1.5 mb-6 border border-[#FF6B35]/30 bg-[#FF6B35]/10 rounded-full">
          <span className="w-2 h-2 rounded-full bg-[#FF6B35] animate-pulse" />
          <span className="text-xs font-mono tracking-widest text-[#FF6B35]">
            WORK IN PROGRESS
          </span>
        </div>

        {/* Message */}
        <h1 className="text-3xl font-bold text-white mb-3">
          Intelligence Map Offline
        </h1>
        <p className="text-gray-400 mb-8 leading-relaxed">
          This section is undergoing a major upgrade and is temporarily
          unavailable. Geospatial coverage will return in a future release.
        </p>

        {/* Navigation */}
        <div className="flex flex-col sm:flex-row gap-3 justify-center">
          <Link
            href="/dashboard"
            className="inline-flex items-center justify-center gap-2 px-6 py-3 bg-[#FF6B35] text-white font-medium rounded-xl hover:bg-[#FF6B35]/80 transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
            Back to Dashboard
          </Link>
          <Link
            href="/stories"
            className="inline-flex items-center justify-center gap-2 px-6 py-3 border border-white/10 text-gray-300 font-medium rounded-xl hover:bg-white/5 transition-colors"
          >
            Explore Stories
          </Link>
        </div>

        {/* Decorative coordinates */}
        <p className="mt-12 text-xs font-mono text-gray-600 tracking-wider">
          STATUS: MAINTENANCE · MODULE: GEOSPATIAL · ETA: TBD
        </p>
      </div>
    </main>
  );
}
