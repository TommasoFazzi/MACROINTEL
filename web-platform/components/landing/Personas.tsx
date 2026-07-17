import Link from 'next/link';
import { PERSONAS } from '@/lib/landing/data';

export default function Personas() {
  return (
    <section className="bg-[#0d1520] py-[100px]">
      <div className="mx-auto max-w-[1200px] px-10">
        <div className="grid grid-cols-[repeat(auto-fit,minmax(360px,1fr))] items-center gap-16">
          <div>
            <div className="section-label">WHO IT&apos;S FOR</div>
            <h2 className="mb-5 text-4xl font-extrabold leading-[1.1] tracking-[-0.02em]">
              Built for people who cannot afford to miss signals.
            </h2>
            <p className="mb-8 text-[15px] leading-[1.7] text-[#64748b]">
              Not a generic AI news aggregator. MACROINTEL is purpose-built for high-stakes intelligence work.
            </p>
            <Link className="btn-primary" href="https://macrointel.net/dashboard">
              Open the Platform
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <path d="M5 12h14M12 5l7 7-7 7" />
              </svg>
            </Link>
          </div>
          <div className="flex flex-col gap-4">
            {PERSONAS.map((p) => (
              <div key={p.role} className="card flex items-start gap-4 px-[22px] py-5">
                <span className="mt-px shrink-0 text-lg text-primary">{p.icon}</span>
                <div>
                  <div className="mb-1.5 text-sm font-semibold text-foreground">{p.role}</div>
                  <div className="text-[13px] leading-[1.55] text-[#64748b]">{p.desc}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
