'use client';

import Link from 'next/link';
import { SOURCE_COUNT } from '@/lib/constants';

const PLATFORM_LINKS: Array<[string, string]> = [
  ['Dashboard', '/dashboard'],
  ['Narrative Graph', '/stories'],
  ['Oracle AI', '/oracle'],
];

const RESOURCES_LINKS: Array<[string, string]> = [
  ['Intelligence Briefings', '/insights'],
  ['Features', '#features'],
  ['About', '#about'],
];

export default function Footer() {
  return (
    <footer className="border-t border-white/[0.06] px-5 pb-8 pt-12 sm:px-8 lg:px-10">
      <div className="mx-auto max-w-[1200px]">
        <div className="mb-10 grid grid-cols-[repeat(auto-fit,minmax(min(220px,100%),1fr))] gap-10">
          <div className="sm:col-span-2">
            <div className="mb-3 font-mono text-lg font-bold tracking-[0.05em] text-foreground">
              MACRO<span className="text-primary">INTEL</span>
            </div>
            <p className="max-w-[320px] text-sm leading-[1.65] text-fg-subtle">
              AI-powered OSINT platform monitoring geopolitical risks, cyber threats, and macro-economic signals — {SOURCE_COUNT} sources processed daily into actionable intelligence.
            </p>
          </div>
          <div>
            <div className="mb-3.5 font-mono text-meta font-semibold uppercase tracking-[0.12em] text-fg-subtle">
              Platform
            </div>
            {PLATFORM_LINKS.map(([l, h]) => (
              <Link
                key={l}
                href={h}
                className="mb-2 block text-sm text-muted-foreground no-underline"
              >
                {l}
              </Link>
            ))}
          </div>
          <div>
            <div className="mb-3.5 font-mono text-meta font-semibold uppercase tracking-[0.12em] text-fg-subtle">
              Resources
            </div>
            {RESOURCES_LINKS.map(([l, h]) => (
              <a
                key={l}
                href={h}
                className="mb-2 block text-sm text-muted-foreground no-underline"
              >
                {l}
              </a>
            ))}
            <button
              type="button"
              onClick={() => window.dispatchEvent(new Event('open-cookie-preferences'))}
              className="mb-2 block cursor-pointer bg-transparent p-0 text-sm text-muted-foreground no-underline"
            >
              Manage cookies
            </button>
          </div>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-white/[0.06] pt-5">
          <span className="font-mono text-meta text-fg-subtle">
            © 2026 MACROINTEL. All rights reserved.
          </span>
          <span className="font-mono text-meta text-fg-subtle">
            Powered by Next.js · Gemini AI · pgvector
          </span>
        </div>
      </div>
    </footer>
  );
}
