import { PIPELINE } from '@/lib/landing/data';

const STEP_COLORS = ['#FF6B35', '#00A8E8', '#10b981'];

export default function Pipeline() {
  return (
    <section id="features" className="grid-bg bg-background py-[100px]">
      <div className="mx-auto max-w-[1200px] px-10">
        <div className="mb-16 text-center">
          <div className="section-label justify-center">HOW IT WORKS</div>
          <h2 className="mb-3 text-[40px] font-extrabold tracking-[-0.02em]">
            From raw signal to structured intelligence.
          </h2>
          <p className="mx-auto max-w-[480px] text-[15px] text-[#64748b]">
            Three stages. No manual aggregation. No missed signals.
          </p>
        </div>
        <div className="relative grid grid-cols-[repeat(auto-fit,minmax(260px,1fr))] gap-6">
          <div className="absolute left-[16.6%] right-[16.6%] top-8 z-0 h-px bg-[linear-gradient(90deg,rgba(255,107,53,0.4),rgba(0,168,232,0.4))]" />
          {PIPELINE.map((s, i) => {
            const color = STEP_COLORS[i] ?? '#FF6B35';
            return (
              <div key={s.step} className="card relative z-[1] p-7">
                <div className="mb-5 flex items-center gap-3">
                  <div
                    className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full"
                    style={{ background: `${color}26`, border: `1px solid ${color}4D` }}
                  >
                    <span className="font-mono text-[13px] font-bold" style={{ color }}>
                      {s.step}
                    </span>
                  </div>
                  <span className="font-mono text-[10px] font-bold tracking-[0.12em]" style={{ color }}>
                    {s.label}
                  </span>
                </div>
                <h3 className="mb-2.5 text-[17px] font-bold leading-[1.3] text-foreground">{s.title}</h3>
                <p className="text-[13px] leading-[1.65] text-[#64748b]">{s.body}</p>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
