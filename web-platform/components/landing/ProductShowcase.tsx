'use client';

import { useState } from 'react';
import { GitBranch, Map, FileText, Check } from 'lucide-react';
import Image from 'next/image';
import Link from 'next/link';
import { Button } from '@/components/ui/button';

const tabs = [
  {
    id: 'graph',
    label: 'Narrative Graph',
    icon: <GitBranch className="w-4 h-4" />,
    href: '/stories',
    // Drop your screenshot at: public/screenshots/narrative-graph.png
    screenshot: '/screenshots/narrative-graph.png',
    benefits: [
      'Force-directed graph of all active intelligence storylines',
      'Community detection groups related narratives automatically',
      'Ego-network drill-down: click any node to see its connections',
    ],
    description:
      'Watch how events cluster into narratives in real time. The Narrative Graph makes invisible connections visible — before they become news.',
  },
  {
    id: 'map',
    label: 'Intelligence Map',
    icon: <Map className="w-4 h-4" />,
    href: '/map',
    screenshot: '/screenshots/intelligence-map.png',
    benefits: [
      'Geospatial heatmap of extracted entities with intelligence scoring',
      'Arc visualization shows relationships between geographic actors',
      'Filter by entity type, community, and relevance in real time',
    ],
    description:
      'Where in the world is it happening? The tactical map turns abstract intelligence into a geographic picture.',
  },
  {
    id: 'oracle',
    label: 'Oracle AI',
    icon: <FileText className="w-4 h-4" />,
    href: '/oracle',
    screenshot: '/screenshots/oracle-chat.png',
    benefits: [
      'Natural language queries against the full intelligence database',
      'Every answer traces back to real source articles — no hallucinations',
      'Multi-turn conversations with context retention across questions',
    ],
    description:
      'Ask Oracle anything: "What happened in the South China Sea this week?" It answers with sources, not guesses.',
  },
];

function ScreenshotPlaceholder({ tab }: { tab: typeof tabs[0] }) {
  const [imgError, setImgError] = useState(false);

  if (!imgError) {
    return (
      <Image
        src={tab.screenshot}
        alt={`${tab.label} screenshot`}
        width={1200}
        height={700}
        className="w-full h-full object-cover object-top rounded-lg"
        onError={() => setImgError(true)}
      />
    );
  }

  // Fallback placeholder when screenshot isn't available yet
  return (
    <div className="w-full h-full rounded-lg bg-[#0f1a2b] border border-white/10 flex flex-col items-center justify-center gap-4 p-8">
      <div className="text-[#FF6B35] opacity-40">{tab.icon && <span className="scale-[3] block">{tab.icon}</span>}</div>
      <p className="text-gray-500 text-sm text-center">
        Screenshot coming soon — take one from{' '}
        <Link href={tab.href} className="text-[#FF6B35] underline">
          {tab.label}
        </Link>{' '}
        and save it to{' '}
        <code className="text-gray-400">public{tab.screenshot}</code>
      </p>
    </div>
  );
}

export default function ProductShowcase() {
  const [activeTab, setActiveTab] = useState(0);
  const tab = tabs[activeTab];

  return (
    <section id="product-showcase" className="py-24 relative">
      <div className="max-w-7xl mx-auto px-6">
        {/* Section header */}
        <div className="text-center mb-14">
          <h2 className="text-3xl md:text-4xl font-extrabold text-white mb-3">
            The Intelligence Platform in Action
          </h2>
          <p className="text-gray-400 text-lg">
            Three tools, one mission: turn geopolitical complexity into actionable clarity.
          </p>
        </div>

        {/* Tab switcher */}
        <div className="flex justify-center gap-2 mb-10 flex-wrap">
          {tabs.map((t, i) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setActiveTab(i)}
              className={`flex items-center gap-2 px-5 py-2.5 rounded-full text-sm font-medium transition-all duration-200 border ${
                activeTab === i
                  ? 'bg-[#FF6B35]/15 border-[#FF6B35]/60 text-[#FF6B35] shadow-[0_0_12px_rgba(255,107,53,0.15)]'
                  : 'bg-white/5 border-white/10 text-gray-400 hover:text-white hover:border-white/20'
              }`}
            >
              {t.icon}
              {t.label}
            </button>
          ))}
        </div>

        {/* Content: screenshot + benefits */}
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-10 items-center">
          {/* Screenshot — 3/5 width on desktop */}
          <div className="lg:col-span-3 order-2 lg:order-1">
            <div
              className="relative rounded-xl overflow-hidden border border-white/10 bg-[#0f1a2b]"
              style={{ aspectRatio: '16/9' }}
            >
              {/* Orange glow border on top */}
              <div className="absolute inset-x-0 top-0 h-[2px] bg-gradient-to-r from-transparent via-[#FF6B35]/80 to-transparent" />
              <ScreenshotPlaceholder tab={tab} />
            </div>
          </div>

          {/* Text: description + benefits — 2/5 width on desktop */}
          <div className="lg:col-span-2 order-1 lg:order-2 flex flex-col gap-6">
            <div>
              <div className="flex items-center gap-2 text-[#FF6B35] mb-3">
                {tab.icon}
                <span className="text-sm font-semibold uppercase tracking-wider">{tab.label}</span>
              </div>
              <p className="text-gray-300 text-lg leading-relaxed">{tab.description}</p>
            </div>

            <ul className="space-y-3">
              {tab.benefits.map((b) => (
                <li key={b} className="flex items-start gap-3">
                  <Check className="w-5 h-5 text-[#FF6B35] mt-0.5 shrink-0" />
                  <span className="text-gray-400 text-sm">{b}</span>
                </li>
              ))}
            </ul>

            <Button asChild variant="outline" className="self-start border-[#FF6B35]/30 text-[#FF6B35] hover:bg-[#FF6B35]/10 hover:text-[#FF6B35]">
              <Link href={tab.href} className="flex items-center gap-2">
                {tab.icon}
                Explore {tab.label}
              </Link>
            </Button>
          </div>
        </div>
      </div>
    </section>
  );
}
