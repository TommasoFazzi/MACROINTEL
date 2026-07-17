'use client';

import Link from 'next/link';

const PLATFORM_LINKS: Array<[string, string]> = [
  ['Dashboard', 'https://macrointel.net/dashboard'],
  ['Narrative Graph', 'https://macrointel.net/stories'],
  ['Intelligence Map', 'https://macrointel.net/map'],
  ['Oracle AI', 'https://macrointel.net/oracle'],
];

const RESOURCES_LINKS: Array<[string, string]> = [
  ['Intelligence Briefings', 'https://macrointel.net/insights'],
  ['Features', '#features'],
  ['About', '#about'],
];

export default function Footer() {
  return (
    <footer className="border-t border-white/[0.06] bg-[#070e1a] px-10 pb-8 pt-12">
      <div className="mx-auto max-w-[1200px]">
        <div className="mb-10 grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-10">
          <div className="col-span-2">
            <div className="mb-3 font-mono text-lg font-bold tracking-[0.05em] text-foreground">
              MACRO<span className="text-primary">INTEL</span>
            </div>
            <p className="max-w-[320px] text-[13px] leading-[1.65] text-[#64748b]">
              AI-powered OSINT platform monitoring geopolitical risks, cyber threats, and macro-economic signals — 40+ sources processed daily into actionable intelligence.
            </p>
          </div>
          <div>
            <div className="mb-3.5 font-mono text-[10px] font-semibold uppercase tracking-[0.12em] text-[#64748b]">
              Platform
            </div>
            {PLATFORM_LINKS.map(([l, h]) => (
              <Link
                key={l}
                href={h}
                className="mb-2 block text-[13px] text-muted-foreground no-underline"
              >
                {l}
              </Link>
            ))}
          </div>
          <div>
            <div className="mb-3.5 font-mono text-[10px] font-semibold uppercase tracking-[0.12em] text-[#64748b]">
              Resources
            </div>
            {RESOURCES_LINKS.map(([l, h]) => (
              <a
                key={l}
                href={h}
                className="mb-2 block text-[13px] text-muted-foreground no-underline"
              >
                {l}
              </a>
            ))}
            <button
              type="button"
              onClick={() => window.dispatchEvent(new Event('open-cookie-preferences'))}
              className="mb-2 block cursor-pointer bg-transparent p-0 text-[13px] text-muted-foreground no-underline"
            >
              Manage cookies
            </button>
          </div>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-white/[0.06] pt-5">
          <span className="font-mono text-[11px] text-[#374151]">
            © 2026 MACROINTEL. All rights reserved.
          </span>
          <span className="font-mono text-[10px] text-[#374151]">
            Powered by Next.js · Gemini AI · pgvector
          </span>
        </div>
      </div>
    </footer>
  );
}
