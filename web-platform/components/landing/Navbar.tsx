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

  useEffect(() => {
    if (solid) return;
    const onScroll = () => setScrolled(window.scrollY > 40);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, [solid]);

  return (
    <nav
      className={scrolled ? 'scrolled' : ''}
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        zIndex: 100,
        height: 60,
        display: 'flex',
        alignItems: 'center',
        padding: '0 32px',
        borderBottom: '1px solid rgba(255,255,255,0.06)',
        transition: 'background 0.3s ease, backdrop-filter 0.3s ease',
        background: scrolled ? 'rgba(10,22,40,0.95)' : 'transparent',
        backdropFilter: scrolled ? 'blur(12px)' : 'none',
        WebkitBackdropFilter: scrolled ? 'blur(12px)' : 'none',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginRight: 'auto' }}>
        <span
          style={{
            fontFamily: 'var(--font-geist-mono), monospace',
            fontWeight: 700,
            fontSize: 16,
            letterSpacing: '0.05em',
            color: '#ededed',
          }}
        >
          MACRO<span style={{ color: '#FF6B35' }}>INTEL</span>
        </span>
        <span
          style={{
            fontFamily: 'var(--font-geist-mono), monospace',
            fontSize: 9,
            color: '#64748b',
            letterSpacing: '0.1em',
            marginTop: 2,
          }}
        >
          OSINT PLATFORM
        </span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 28 }}>
        {NAV_LINKS.map(([label, href]) => (
          <a
            key={label}
            href={href}
            style={{ color: '#94a3b8', fontSize: 13, fontWeight: 500, textDecoration: 'none' }}
          >
            {label}
          </a>
        ))}
      </div>
      <div style={{ marginLeft: 32 }}>
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
