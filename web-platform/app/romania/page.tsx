import type { Metadata } from 'next';
import { BarChart3, Globe } from 'lucide-react';
import { Navbar } from '@/components/landing';
import { MacroMiniDashboard } from '@/components/romania/MacroMiniDashboard';
import { BriefingList } from '@/components/romania/BriefingList';

export const metadata: Metadata = {
  title: 'Romania Intelligence | MACROINTEL',
  description:
    'Briefing di intelligence economica sulla Romania — macro indicatori, energia, infrastrutture e implicazioni per le imprese italiane.',
  openGraph: {
    title: 'Romania Intelligence | MACROINTEL',
    description:
      'Briefing di intelligence economica sulla Romania — macro indicatori, energia, infrastrutture e implicazioni per le imprese italiane.',
    type: 'website',
  },
};

export default function RomaniaPage() {
  return (
    <>
      <Navbar />
      <main className="min-h-screen bg-[#0A1628] pt-28 pb-20">
        <div className="max-w-5xl mx-auto px-6">

          {/* Header */}
          <div className="mb-10">
            <div className="flex items-center gap-2 text-blue-400 mb-4">
              <Globe className="w-5 h-5" />
              <span className="text-sm font-semibold uppercase tracking-wider">Romania Vertical</span>
            </div>
            <h1 className="text-4xl md:text-5xl font-extrabold text-white mb-4">
              🇷🇴 Intelligence Romania
            </h1>
            <p className="text-gray-400 text-lg max-w-2xl">
              Briefing quotidiani e settimanali sull&apos;economia romena, con focus su implicazioni
              per le imprese italiane — BNR, INSSE, energia e infrastrutture.
            </p>
          </div>

          {/* Macro Dashboard */}
          <section className="mb-12">
            <div className="flex items-center gap-2 mb-5">
              <BarChart3 className="w-4 h-4 text-gray-400" />
              <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
                Indicatori Macro — Romania
              </h2>
            </div>
            <MacroMiniDashboard />
          </section>

          {/* Divider */}
          <div className="border-t border-white/8 mb-10" />

          {/* Briefings */}
          <section>
            <h2 className="text-2xl font-bold text-white mb-6">Briefing Recenti</h2>
            <BriefingList />
          </section>

        </div>
      </main>
    </>
  );
}
