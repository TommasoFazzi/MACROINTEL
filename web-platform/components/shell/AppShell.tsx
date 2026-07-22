'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useState, type ReactNode } from 'react';
import {
  FileText,
  Globe,
  LayoutDashboard,
  Network,
  Search,
  Sparkles,
} from 'lucide-react';
import { useDashboardStats } from '@/hooks/useDashboard';
import CommandPalette from './CommandPalette';

/**
 * Persistent shell for the application routes (everything that isn't the marketing landing).
 *
 * Replaces the landing `Navbar` on `/dashboard`, `/insights`, `/romania`, `/oracle` and
 * `/stories`: a marketing top-bar with "Open Platform" makes no sense once the reader is
 * already inside the platform, and it cost 60px of vertical space on the two routes that
 * need the full viewport.
 *
 * `/map` is intentionally absent from the rail — it's still marked COMING SOON and
 * deliberately unlinked everywhere else too (see the change's Resolved Q1).
 */

const RAIL_ITEMS: Array<{ href: string; label: string; Icon: typeof LayoutDashboard }> = [
  { href: '/dashboard', label: 'Dashboard', Icon: LayoutDashboard },
  { href: '/insights', label: 'Insights', Icon: FileText },
  { href: '/stories', label: 'Stories', Icon: Network },
  { href: '/oracle', label: 'Oracle', Icon: Sparkles },
  { href: '/romania', label: 'Romania', Icon: Globe },
];

const RAIL_W = 64; // px — the collapsed rail; `--rail-w` mirrors it for consumers

function isActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

/**
 * Pipeline freshness, derived from the same stats endpoint the dashboard already polls (SWR
 * dedupes, so mounting this costs no extra request). Per task 4.2 the indicator is *omitted*
 * rather than shown in an error state when the endpoint fails — a shell chrome element that
 * says "unknown" on every page is worse than no element at all.
 */
function PipelineStatus() {
  const { stats, generatedAt, error, isLoading } = useDashboardStats();
  if (error || isLoading || !stats) return null;

  const today = stats.articles.articles_today ?? 0;
  const fresh = today > 0;
  const syncedAt = generatedAt ? new Date(generatedAt) : null;
  const syncLabel =
    syncedAt && !Number.isNaN(syncedAt.getTime())
      ? syncedAt.toISOString().slice(11, 16)
      : null;

  return (
    <div className="flex items-center gap-2">
      <span
        className={`inline-block h-1.5 w-1.5 rounded-full ${
          fresh ? 'animate-[pulse-dot_2s_ease-in-out_infinite] bg-accent-info' : 'bg-fg-subtle'
        }`}
      />
      <span className="font-mono text-meta uppercase tracking-[0.1em] text-fg-subtle">
        {fresh ? `${today} today` : 'Idle'}
        {syncLabel && ` · ${syncLabel}Z`}
      </span>
    </div>
  );
}

type AppShellProps = {
  children: ReactNode;
  /**
   * Full-bleed routes (`/oracle`, `/stories`) keep their own full-viewport layout: they get
   * the rail for navigation but no status header and no content padding, so the graph and
   * the chat still own the whole height.
   */
  fullBleed?: boolean;
};

export default function AppShell({ children, fullBleed = false }: AppShellProps) {
  const pathname = usePathname();
  const [paletteOpen, setPaletteOpen] = useState(false);

  return (
    <div className="min-h-screen" style={{ ['--rail-w' as string]: `${RAIL_W}px` }}>
      {/* Rail. Expansion is pure CSS `group-hover` rather than React state: it's an overlay,
          so widening it must not reflow the content beside it, and a hover that re-renders
          the whole subtree would be wasted work. */}
      <nav
        aria-label="Main"
        className="group fixed inset-y-0 left-0 z-50 flex w-16 flex-col items-stretch border-r border-white/[0.06] bg-[rgba(8,18,33,0.96)] backdrop-blur-md transition-[width] duration-fast ease-in-out hover:w-56"
      >
        <Link
          href="/"
          className="flex h-16 shrink-0 items-center gap-2 overflow-hidden px-5 no-underline"
          title="MACROINTEL — home"
        >
          <span className="shrink-0 font-mono text-base font-bold text-primary">M</span>
          <span className="whitespace-nowrap font-mono text-sm font-bold tracking-[0.05em] text-foreground opacity-0 transition-opacity duration-fast group-hover:opacity-100">
            MACRO<span className="text-primary">INTEL</span>
          </span>
        </Link>

        <div className="flex flex-1 flex-col gap-1 px-2 py-3">
          {RAIL_ITEMS.map(({ href, label, Icon }) => {
            const active = isActive(pathname, href);
            return (
              <Link
                key={href}
                href={href}
                title={label}
                aria-current={active ? 'page' : undefined}
                className={`flex items-center gap-3 overflow-hidden rounded-lg px-3 py-2.5 no-underline transition-colors duration-fast ${
                  active
                    ? 'bg-accent-info/12 text-accent-info'
                    : 'text-fg-muted hover:bg-white/[0.04] hover:text-foreground'
                }`}
              >
                <Icon className="h-[18px] w-[18px] shrink-0" strokeWidth={active ? 2.2 : 1.8} />
                <span className="whitespace-nowrap text-sm font-medium opacity-0 transition-opacity duration-fast group-hover:opacity-100">
                  {label}
                </span>
              </Link>
            );
          })}
        </div>

        <button
          type="button"
          onClick={() => setPaletteOpen(true)}
          className="m-2 flex items-center gap-3 overflow-hidden rounded-lg px-3 py-2.5 text-fg-muted transition-colors duration-fast hover:bg-white/[0.04] hover:text-foreground"
          title="Search — ⌘K"
        >
          <Search className="h-[18px] w-[18px] shrink-0" strokeWidth={1.8} />
          <span className="whitespace-nowrap text-sm opacity-0 transition-opacity duration-fast group-hover:opacity-100">
            Search
          </span>
          <kbd className="ml-auto whitespace-nowrap rounded border border-white/10 px-1.5 py-0.5 font-mono text-meta text-fg-subtle opacity-0 transition-opacity duration-fast group-hover:opacity-100">
            ⌘K
          </kbd>
        </button>
      </nav>

      <div className="pl-16">
        {!fullBleed && (
          <header className="sticky top-0 z-40 flex h-16 items-center gap-4 border-b border-white/[0.06] bg-[rgba(10,22,40,0.82)] px-6 backdrop-blur-md">
            <PipelineStatus />
            <button
              type="button"
              onClick={() => setPaletteOpen(true)}
              className="ml-auto flex items-center gap-2 rounded-lg border border-white/[0.08] px-3 py-1.5 text-fg-subtle transition-colors duration-fast hover:border-white/20 hover:text-foreground"
            >
              <Search className="h-3.5 w-3.5" strokeWidth={1.8} />
              <span className="text-sm">Search</span>
              <kbd className="rounded border border-white/10 px-1.5 py-0.5 font-mono text-meta">
                ⌘K
              </kbd>
            </button>
          </header>
        )}
        {children}
      </div>

      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </div>
  );
}
