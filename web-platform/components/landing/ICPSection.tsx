'use client';

import { Shield, TrendingUp, Search, Globe } from 'lucide-react';

const profiles = [
  {
    icon: <Globe className="w-6 h-6" />,
    title: 'Geopolitical Analysts',
    pain: 'Stop reading 50 RSS feeds manually. MACROINTEL distills 33+ intelligence sources into daily briefings — so you can focus on analysis, not aggregation.',
  },
  {
    icon: <Shield className="w-6 h-6" />,
    title: 'CISO & Security Teams',
    pain: "Threat actors don\u2019t wait. Monitor geopolitical escalations and cyber incidents in real time, with AI-driven narrative tracking that connects the dots before they become incidents.",
  },
  {
    icon: <TrendingUp className="w-6 h-6" />,
    title: 'Macro Fund Managers',
    pain: 'Geopolitical risk moves markets. Oracle AI surfaces relevant trade signals and macro trends from raw intelligence — so you act on signal, not noise.',
  },
  {
    icon: <Search className="w-6 h-6" />,
    title: 'Investigative Journalists',
    pain: 'Find the story before it breaks. The Narrative Graph tracks how events and actors cluster over time — revealing storylines that traditional tools miss.',
  },
];

export default function ICPSection() {
  return (
    <section className="py-20 relative">
      {/* Subtle grid background */}
      <div className="absolute inset-0 grid-overlay opacity-20 pointer-events-none" />

      <div className="max-w-7xl mx-auto px-6 relative">
        <div className="text-center mb-14">
          <h2 className="text-3xl md:text-4xl font-extrabold text-white mb-3">
            Built for Intelligence Professionals
          </h2>
          <p className="text-gray-400 text-lg">
            Not a generic AI tool — purpose-built for people who work with geopolitical risk every day.
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
          {profiles.map((profile) => (
            <div
              key={profile.title}
              className="group relative p-6 rounded-xl border border-white/8 bg-[#1a2332]/60 hover:border-[#FF6B35]/40 hover:bg-[#1a2332]/90 transition-all duration-300"
            >
              {/* Orange accent top border on hover */}
              <div className="absolute inset-x-0 top-0 h-[2px] rounded-t-xl bg-gradient-to-r from-transparent via-[#FF6B35] to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-300" />

              <div className="w-11 h-11 rounded-lg bg-[#FF6B35]/10 border border-[#FF6B35]/20 flex items-center justify-center text-[#FF6B35] mb-4 group-hover:bg-[#FF6B35]/20 transition-colors">
                {profile.icon}
              </div>

              <h3 className="text-white font-semibold mb-3 text-lg">
                {profile.title}
              </h3>
              <p className="text-gray-400 text-sm leading-relaxed">
                {profile.pain}
              </p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
