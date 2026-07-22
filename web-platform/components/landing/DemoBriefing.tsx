import Image from 'next/image';
import Link from 'next/link';
import AppFrame from './AppFrame';
import type { LiveBriefing } from '@/lib/landing/live';

type DemoBriefingProps = { briefing: LiveBriefing };

function formatBadge(briefing: LiveBriefing): string {
  if (!briefing?.publishedAt) return 'DAILY BRIEFING';
  const date = new Date(briefing.publishedAt);
  if (Number.isNaN(date.getTime())) return 'DAILY BRIEFING';
  const label = (briefing.reportType ?? 'daily').toUpperCase();
  const day = date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }).toUpperCase();
  return `${label} // ${day}`;
}

export default function DemoBriefing({ briefing }: DemoBriefingProps) {
  return (
    <AppFrame label="INTELLIGENCE BRIEFING" labelColor="var(--data-6)" badge={formatBadge(briefing)}>
      <Link
        href={briefing ? `/insights/${briefing.slug}` : '/insights'}
        className="relative block h-[300px] w-full overflow-hidden"
      >
        <Image
          src="/assets/dashboard-screenshot.png"
          alt=""
          fill
          sizes="(max-width: 768px) 100vw, 720px"
          className="object-cover object-top opacity-30"
        />
        <div className="absolute inset-0 bg-gradient-to-t from-[#060e1c] via-[#060e1c]/75 to-transparent" />
        <div className="absolute inset-x-0 bottom-0 p-5">
          <h3 className="mb-2 line-clamp-2 text-body font-bold leading-snug text-foreground">
            {briefing?.title ?? "Today's intelligence briefing"}
          </h3>
          <p className="line-clamp-2 text-sm leading-relaxed text-fg-muted">
            {briefing?.summaryPreview ??
              'Distilled overnight from monitored sources — geopolitical, cyber, and macro signals ready before markets open.'}
          </p>
        </div>
      </Link>
    </AppFrame>
  );
}
