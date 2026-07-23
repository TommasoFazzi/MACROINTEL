'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';

const NAV_LINKS: Array<[string, string]> = [
  ['Features', '/#features'],
  ['FAQ', '/#faq'],
  ['Romania', '/romania'],
  ['Insights', '/insights'],
  ['About', '/about'],
];

type NavbarProps = { solid?: boolean };

export default function Navbar({ solid = false }: NavbarProps) {
  const [scrolled, setScrolled] = useState(solid);
  const [logoHover, setLogoHover] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    if (solid) return;
    const onScroll = () => setScrolled(window.scrollY > 40);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, [solid]);

  // Any interaction beyond the header itself (a link tap, Escape, scrolling the page) closes
  // the mobile menu — an open panel that stays put while the page moves under it reads broken.
  useEffect(() => {
    if (!menuOpen) return;
    const close = () => setMenuOpen(false);
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && close();
    window.addEventListener('scroll', close, { passive: true });
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('scroll', close);
      window.removeEventListener('keydown', onKey);
    };
  }, [menuOpen]);

  // Below md the header is transparent-until-scrolled, but an *open* menu always needs an
  // opaque backdrop or the panel's links overlap the hero content bleeding through.
  const solidBg = scrolled || menuOpen;

  return (
    <nav
      className={`fixed inset-x-0 top-0 z-[100] flex h-[60px] items-center border-b border-white/[0.06] px-5 transition-[background,backdrop-filter] duration-fast ease-in-out sm:px-8 ${scrolled ? 'scrolled' : ''}`}
      style={{
        background: solidBg ? 'rgba(10,22,40,0.95)' : 'transparent',
        backdropFilter: solidBg ? 'blur(12px)' : 'none',
        WebkitBackdropFilter: solidBg ? 'blur(12px)' : 'none',
      }}
    >
      <Link
        href="/"
        className="mr-auto flex cursor-pointer items-center gap-2 no-underline transition-opacity duration-instant ease-in-out"
        style={{ opacity: logoHover ? 0.7 : 1 }}
        onMouseEnter={() => setLogoHover(true)}
        onMouseLeave={() => setLogoHover(false)}
      >
        <span className="font-mono text-base font-bold tracking-[0.05em] text-foreground">
          MACRO<span className="text-primary">INTEL</span>
        </span>
        {/* Secondary tag is decorative — drop it on the narrowest phones so the logo + burger
            never fight for the same row. */}
        <span className="mt-0.5 hidden font-mono text-meta tracking-[0.1em] text-fg-subtle min-[380px]:inline">
          OSINT PLATFORM
        </span>
      </Link>

      {/* Desktop nav — links + CTA inline. Hidden below md, where they'd overflow the bar. */}
      <div className="hidden items-center gap-7 md:flex">
        {NAV_LINKS.map(([label, href]) => (
          <a
            key={label}
            href={href}
            className="text-sm font-medium text-muted-foreground no-underline"
          >
            {label}
          </a>
        ))}
      </div>
      <div className="ml-8 hidden md:block">
        <Link
          className="btn-primary"
          href="/dashboard"
          style={{ padding: '8px 18px', fontSize: 13 }}
        >
          Open Platform
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <path d="M5 12h14M12 5l7 7-7 7" />
          </svg>
        </Link>
      </div>

      {/* Mobile trigger */}
      <button
        type="button"
        onClick={() => setMenuOpen((v) => !v)}
        aria-label={menuOpen ? 'Close menu' : 'Open menu'}
        aria-expanded={menuOpen}
        className="-mr-1 flex h-10 w-10 shrink-0 items-center justify-center rounded text-foreground md:hidden"
      >
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          {menuOpen ? (
            <path d="M6 6l12 12M18 6L6 18" />
          ) : (
            <>
              <path d="M4 7h16" />
              <path d="M4 12h16" />
              <path d="M4 17h16" />
            </>
          )}
        </svg>
      </button>

      {/* Mobile menu panel — drops below the bar, full width, opaque. */}
      {menuOpen && (
        <div className="absolute inset-x-0 top-[60px] flex flex-col gap-1 border-b border-white/[0.06] bg-[rgba(10,22,40,0.98)] px-5 pb-6 pt-2 backdrop-blur-md md:hidden">
          {NAV_LINKS.map(([label, href]) => (
            <a
              key={label}
              href={href}
              onClick={() => setMenuOpen(false)}
              className="border-b border-white/[0.04] py-3 text-base font-medium text-muted-foreground no-underline"
            >
              {label}
            </a>
          ))}
          <Link
            className="btn-primary mt-4 justify-center"
            href="/dashboard"
            onClick={() => setMenuOpen(false)}
            style={{ padding: '12px 18px', fontSize: 14 }}
          >
            Open Platform
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path d="M5 12h14M12 5l7 7-7 7" />
            </svg>
          </Link>
        </div>
      )}
    </nav>
  );
}
