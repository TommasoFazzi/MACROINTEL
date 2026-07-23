import Link from 'next/link';
import Reveal from '@/components/motion/Reveal';

export default function FinalCTA() {
  return (
    <section id="about" className="relative overflow-hidden py-20 md:py-[120px]">
      <div className="absolute inset-0 bg-[linear-gradient(rgba(255,107,53,0.03)_1px,transparent_1px),linear-gradient(90deg,rgba(255,107,53,0.03)_1px,transparent_1px)] bg-[size:60px_60px]" />
      <div className="pointer-events-none absolute left-1/2 top-1/2 h-[600px] w-[600px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-[radial-gradient(circle,rgba(255,107,53,0.06)_0%,transparent_70%)]" />
      <Reveal className="relative z-[1] mx-auto max-w-[700px] px-5 text-center sm:px-8 lg:px-10">
        <div>
          <div className="mb-7 inline-flex items-center gap-2 rounded-full border border-primary/20 bg-primary/[0.08] px-3.5 py-1.5">
            <span className="inline-block h-1.5 w-1.5 animate-[pulse-dot_2s_ease-in-out_infinite] rounded-full bg-primary" />
            <span className="font-mono text-meta font-bold tracking-[0.12em] text-accent-info">
              NOW FULLY PUBLIC — NO REGISTRATION REQUIRED
            </span>
          </div>
          <h2 className="mb-5 text-title font-extrabold leading-[1.05] tracking-[-0.03em]">
            Start exploring <span className="gradient-text">now.</span>
          </h2>
          <p className="mb-10 text-base leading-[1.7] text-fg-subtle">
            Access the dashboard, narrative graph, intelligence map, and ORACLE AI. No login. No waitlist.
          </p>
          <div className="flex flex-wrap justify-center gap-3">
            <Link className="btn-primary orange-glow" href="/dashboard" style={{ padding: '14px 28px', fontSize: 15 }}>
              Open Dashboard
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <path d="M5 12h14M12 5l7 7-7 7" />
              </svg>
            </Link>
            <Link className="btn-ghost" href="/oracle" style={{ padding: '14px 28px', fontSize: 15 }}>
              Try Oracle AI
            </Link>
          </div>
        </div>
      </Reveal>
    </section>
  );
}
