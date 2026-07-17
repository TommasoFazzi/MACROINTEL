'use client';

import { SIGNALS } from '@/lib/landing/data';

type TickerProps = { show?: boolean };

export default function Ticker({ show = false }: TickerProps) {
  if (!show) return null;
  const items = [...SIGNALS, ...SIGNALS];
  return (
    <div className="relative border-y border-white/5 bg-[#0d1520] py-2.5">
      <div className="absolute inset-y-0 left-0 z-[3] flex w-[120px] items-center border-r border-white/[0.06] bg-[#0d1520] pl-4">
        <span className="font-mono text-[10px] font-bold tracking-[0.15em] text-primary">
          ● LIVE FEED
        </span>
      </div>
      <div className="ticker-wrap pl-[120px]">
        <div className="ticker-track">
          {items.map((s, i) => (
            <div key={`${s.region}-${i}`} className="ticker-item">
              <span className="dot" style={{ background: s.dot }} />
              <span style={{ color: s.dot }} className="font-semibold">{s.region}</span>
              <span>{s.text}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
