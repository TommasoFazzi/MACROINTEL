import { communityColor } from '@/lib/communityColors';
import { fallbackSignals, type LiveStoryline } from '@/lib/landing/live';

type TickerProps = { storylines: LiveStoryline[] };

const MAX_ITEMS = 10;

export default function Ticker({ storylines }: TickerProps) {
  const live = storylines.slice(0, MAX_ITEMS).map((s) => ({
    key: `story-${s.id}`,
    dot: communityColor(s.communityId),
    region: (s.category ?? 'SIGNAL').toUpperCase(),
    text: s.title,
  }));

  const base =
    live.length > 0
      ? live
      : fallbackSignals().map((s, i) => ({
          key: `fallback-${i}`,
          dot: s.dot,
          region: s.region,
          text: s.text,
        }));

  const items = [...base, ...base];

  return (
    <div className="relative border-y border-white/5 py-2.5">
      <div className="absolute inset-y-0 left-0 z-[3] flex w-[120px] items-center border-r border-white/[0.06] bg-background pl-4">
        <span className="font-mono text-meta font-bold tracking-[0.15em] text-accent-info">
          ● LIVE FEED
        </span>
      </div>
      <div className="ticker-wrap pl-[120px]">
        <div className="ticker-track">
          {items.map((item, i) => (
            <div key={`${item.key}-${i}`} className="ticker-item">
              <span className="dot" style={{ background: item.dot }} />
              <span style={{ color: item.dot }} className="font-semibold">
                {item.region}
              </span>
              <span>{item.text}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
