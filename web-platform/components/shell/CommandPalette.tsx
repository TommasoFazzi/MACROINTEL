'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { CornerDownLeft, FileText, Globe, LayoutDashboard, Network, Search, Sparkles } from 'lucide-react';
import { useGraphNetwork } from '@/hooks/useStories';

type Command = {
  id: string;
  label: string;
  hint?: string;
  group: 'Navigate' | 'Storylines' | 'Oracle';
  Icon: typeof Search;
  run: () => void;
};

const MAX_STORYLINE_RESULTS = 6;

/**
 * ⌘K / Ctrl+K palette. Three sources, in priority order:
 *   1. routes (always available, works offline)
 *   2. storylines matched by title, from the graph the /stories route already polls
 *   3. a fallthrough "ask Oracle" entry, so any typed text is never a dead end
 *
 * Storyline results degrade silently to nothing when the API is unreachable — routes and the
 * Oracle fallthrough still work, so the palette is never an empty box.
 */
export default function CommandPalette({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const router = useRouter();
  const [query, setQuery] = useState('');
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  // Whatever had focus when the palette opened, so Escape can hand it back (task 4.3).
  const restoreFocusRef = useRef<HTMLElement | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // Only fetched once the palette has been opened: the shell mounts on every app route, and
  // the graph is a heavy payload nobody needs until they actually search.
  const [everOpened, setEverOpened] = useState(false);
  useEffect(() => {
    if (open) setEverOpened(true);
  }, [open]);
  const { graph } = useGraphNetwork();
  const storylines = everOpened ? graph?.nodes ?? [] : [];

  const close = () => onOpenChange(false);

  const commands = useMemo<Command[]>(() => {
    const q = query.trim().toLowerCase();

    const routes: Command[] = (
      [
        { id: 'r-dashboard', label: 'Dashboard', group: 'Navigate', Icon: LayoutDashboard, run: () => router.push('/dashboard') },
        { id: 'r-insights', label: 'Insights', group: 'Navigate', Icon: FileText, run: () => router.push('/insights') },
        { id: 'r-stories', label: 'Narrative Graph', group: 'Navigate', Icon: Network, run: () => router.push('/stories') },
        { id: 'r-oracle', label: 'Oracle', group: 'Navigate', Icon: Sparkles, run: () => router.push('/oracle') },
        { id: 'r-romania', label: 'Romania', group: 'Navigate', Icon: Globe, run: () => router.push('/romania') },
      ] satisfies Command[]
    ).filter((c) => !q || c.label.toLowerCase().includes(q));

    const stories: Command[] = q
      ? storylines
          .filter((n) => n.title?.toLowerCase().includes(q))
          .slice(0, MAX_STORYLINE_RESULTS)
          .map((n) => ({
            id: `s-${n.id}`,
            label: n.title,
            hint: `${n.article_count} articles`,
            group: 'Storylines' as const,
            Icon: Network,
            run: () => router.push(`/stories?storyline=${n.id}`),
          }))
      : [];

    const oracle: Command[] = q
      ? [
          {
            id: 'o-ask',
            label: `Ask Oracle: “${query.trim()}”`,
            group: 'Oracle' as const,
            Icon: Sparkles,
            run: () => router.push(`/oracle?q=${encodeURIComponent(query.trim())}`),
          },
        ]
      : [];

    return [...routes, ...stories, ...oracle];
  }, [query, storylines, router]);

  // Global hotkey. Registered on the window rather than on any input so it fires even when a
  // textarea has focus — `preventDefault` is what stops "k" reaching /oracle's composer
  // (task 4.4).
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        restoreFocusRef.current = document.activeElement as HTMLElement | null;
        onOpenChange(true);
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onOpenChange]);

  useEffect(() => {
    if (!open) {
      setQuery('');
      setCursor(0);
      restoreFocusRef.current?.focus?.();
      return;
    }
    inputRef.current?.focus();
  }, [open]);

  useEffect(() => {
    setCursor(0);
  }, [query]);

  // Keep the highlighted row in view when arrowing past the visible window.
  useEffect(() => {
    listRef.current?.querySelector('[data-active="true"]')?.scrollIntoView({ block: 'nearest' });
  }, [cursor]);

  if (!open) return null;

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Escape') {
      e.preventDefault();
      close();
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      setCursor((c) => (commands.length ? (c + 1) % commands.length : 0));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setCursor((c) => (commands.length ? (c - 1 + commands.length) % commands.length : 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const cmd = commands[cursor];
      if (cmd) {
        cmd.run();
        close();
      }
    }
  }

  let lastGroup = '';

  return (
    <div
      className="fixed inset-0 z-[200] flex items-start justify-center bg-black/55 px-4 pt-[12vh] backdrop-blur-sm"
      onClick={close}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        className="w-full max-w-xl overflow-hidden rounded-xl border border-white/10 bg-[rgba(13,26,45,0.98)] shadow-[0_24px_70px_rgba(0,0,0,0.6)]"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={onKeyDown}
      >
        <div className="flex items-center gap-3 border-b border-white/[0.06] px-4">
          <Search className="h-4 w-4 shrink-0 text-fg-subtle" strokeWidth={1.8} />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Go to a page, find a storyline, or ask Oracle…"
            className="h-14 w-full bg-transparent text-base text-foreground outline-none placeholder:text-fg-subtle"
            aria-label="Search"
          />
          <kbd className="shrink-0 rounded border border-white/10 px-1.5 py-0.5 font-mono text-meta text-fg-subtle">
            ESC
          </kbd>
        </div>

        <div ref={listRef} className="max-h-[52vh] overflow-y-auto py-2">
          {commands.length === 0 && (
            <p className="px-4 py-6 text-center text-sm text-fg-subtle">No matches.</p>
          )}
          {commands.map((cmd, i) => {
            const header = cmd.group !== lastGroup ? cmd.group : null;
            lastGroup = cmd.group;
            const active = i === cursor;
            return (
              <div key={cmd.id}>
                {header && (
                  <div className="px-4 pb-1 pt-3 font-mono text-meta uppercase tracking-[0.12em] text-fg-subtle">
                    {header}
                  </div>
                )}
                <button
                  type="button"
                  data-active={active}
                  onMouseEnter={() => setCursor(i)}
                  onClick={() => {
                    cmd.run();
                    close();
                  }}
                  className={`flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors duration-instant ${
                    active ? 'bg-accent-info/12 text-foreground' : 'text-fg-muted'
                  }`}
                >
                  <cmd.Icon className="h-4 w-4 shrink-0" strokeWidth={1.8} />
                  <span className="truncate text-sm">{cmd.label}</span>
                  {cmd.hint && (
                    <span className="ml-auto shrink-0 font-mono text-meta text-fg-subtle">{cmd.hint}</span>
                  )}
                  {active && !cmd.hint && (
                    <CornerDownLeft className="ml-auto h-3.5 w-3.5 shrink-0 text-fg-subtle" />
                  )}
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
