'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';

const NAV_LINKS: Array<[string, string]> = [
  ['Features', '/#features'],
  ['FAQ', '/#faq'],
  ['Romania', '/romania'],
  ['Insights', 'https://macrointel.net/insights'],
  ['About', '/about'],
];

type NavbarProps = { solid?: boolean };

export default function Navbar({ solid = false }: NavbarProps) {
  const [scrolled, setScrolled] = useState(solid);
  const [logoHover, setLogoHover] = useState(false);

  useEffect(() => {
    if (solid) return;
    const onScroll = () => setScrolled(window.scrollY > 40);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, [solid]);

  return (
    <nav
      className={`fixed inset-x-0 top-0 z-[100] flex h-[60px] items-center border-b border-white/[0.06] px-8 transition-[background,backdrop-filter] duration-300 ease-in-out ${scrolled ? 'scrolled' : ''}`}
      style={{
        background: scrolled ? 'rgba(10,22,40,0.95)' : 'transparent',
        backdropFilter: scrolled ? 'blur(12px)' : 'none',
        WebkitBackdropFilter: scrolled ? 'blur(12px)' : 'none',
      }}
    >
      <Link
        href="/"
        className="mr-auto flex cursor-pointer items-center gap-2 no-underline transition-opacity duration-200 ease-in-out"
        style={{ opacity: logoHover ? 0.7 : 1 }}
        onMouseEnter={() => setLogoHover(true)}
        onMouseLeave={() => setLogoHover(false)}
      >
        <span className="font-mono text-base font-bold tracking-[0.05em] text-foreground">
          MACRO<span className="text-primary">INTEL</span>
        </span>
        <span className="mt-0.5 font-mono text-[9px] tracking-[0.1em] text-[#64748b]">
          OSINT PLATFORM
        </span>
      </Link>
      <div className="flex items-center gap-7">
        {NAV_LINKS.map(([label, href]) => (
          <a
            key={label}
            href={href}
            className="text-[13px] font-medium text-muted-foreground no-underline"
          >
            {label}
          </a>
        ))}
      </div>
      <div className="ml-8">
        <Link
          className="btn-primary"
          href="https://macrointel.net/dashboard"
          style={{ padding: '8px 18px', fontSize: 13 }}
        >
          Open Platform
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <path d="M5 12h14M12 5l7 7-7 7" />
          </svg>
        </Link>
      </div>
    </nav>
  );
}
