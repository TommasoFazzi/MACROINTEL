import { CAPS } from '@/lib/landing/data';

export default function Capabilities() {
  return (
    <section className="bg-background py-[100px]">
      <div className="mx-auto max-w-[1200px] px-10">
        <div className="mb-14 text-center">
          <div className="section-label justify-center">CAPABILITIES</div>
          <h2 className="text-[40px] font-extrabold tracking-[-0.02em]">
            A complete intelligence platform.
          </h2>
        </div>
        <div className="grid grid-cols-[repeat(auto-fit,minmax(280px,1fr))] gap-0.5">
          {CAPS.map((c, i) => (
            <div
              key={c.title}
              className="border border-white/[0.06] p-7"
              style={{ background: i % 2 === 0 ? '#0d1520' : '#0A1628' }}
            >
              <span className="mb-3 block text-xl text-primary">{c.icon}</span>
              <h3 className="mb-2 text-[15px] font-bold text-foreground">{c.title}</h3>
              <p className="text-[13px] leading-[1.6] text-[#64748b]">{c.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
