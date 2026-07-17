'use client';

import Link from 'next/link';
import { useState } from 'react';
import { PRODUCTS } from '@/lib/landing/data';
import DemoMap from './DemoMap';
import DemoGraph from './DemoGraph';
import DemoOracle from './DemoOracle';
import DemoBriefing from './DemoBriefing';

const DEMOS = [DemoBriefing, DemoOracle, DemoGraph, DemoMap];

export default function Products() {
  const [active, setActive] = useState(0);
  const p = PRODUCTS[active];
  const Demo = DEMOS[active];

  return (
    <section id="products" className="bg-[#0d1520] py-[100px]">
      <div className="mx-auto max-w-[1200px] px-10">
        <div className="section-label">PLATFORM</div>
        <h2 className="mb-2 text-4xl font-extrabold leading-[1.1] tracking-[-0.02em]">
          Four tools. One mission.
        </h2>
        <p className="mb-12 max-w-[480px] text-[15px] text-[#64748b]">
          From geopolitical mapping to AI-powered Q&amp;A — no manual aggregation required.
        </p>

        <div className="mb-10 flex w-fit flex-wrap gap-1 rounded-lg border border-white/[0.07] bg-background p-1">
          {PRODUCTS.map((pr, i) => (
            <button
              key={pr.id}
              type="button"
              className={`tab-btn${active === i ? ' active' : ''}`}
              onClick={() => setActive(i)}
            >
              {pr.name}
            </button>
          ))}
        </div>

        <div key={p.id} className="grid grid-cols-[repeat(auto-fit,minmax(320px,1fr))] items-center gap-12">
          <div className="animate-[fadeInUp_0.35s_ease-out]">
            <div className="mb-4 flex items-center gap-2">
              <span
                className="rounded font-mono text-[10px] font-bold tracking-[0.12em]"
                style={{
                  color: p.tagColor,
                  background: `${p.tagColor}26`,
                  border: `1px solid ${p.tagColor}4D`,
                  padding: '3px 8px',
                }}
              >
                {p.tag}
              </span>
            </div>
            <h3 className="mb-3.5 text-[26px] font-bold leading-[1.2] tracking-[-0.02em]">
              {p.headline}
            </h3>
            <p className="mb-7 text-sm leading-[1.75] text-muted-foreground">{p.body}</p>
            <Link
              className="btn-primary"
              href={p.href}
              style={{
                background:
                  p.tagColor === '#FF6B35'
                    ? '#FF6B35'
                    : p.tagColor === '#00A8E8'
                      ? '#0086ba'
                      : p.tagColor === '#10b981'
                        ? '#0d9467'
                        : '#7c4dd6',
              }}
            >
              {p.cta}
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <path d="M5 12h14M12 5l7 7-7 7" />
              </svg>
            </Link>
          </div>

          <div className="animate-[fadeIn_0.4s_ease-out]">
            <Demo />
          </div>
        </div>
      </div>
    </section>
  );
}
