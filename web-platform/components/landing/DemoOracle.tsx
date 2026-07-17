'use client';

import { useEffect, useState } from 'react';
import AppFrame from './AppFrame';

const FULL_ANSWER =
  'The region connecting Central Asia to the Persian Gulf, the Indian Ocean and Europe via Iran, Pakistan and Afghanistan is one of the most strategically critical — and unstable — logistics corridors on the planet. The simultaneous closure of the Strait of Hormuz and the Pakistan-Afghanistan conflict have paralysed or placed at extreme risk virtually all land and maritime corridors of the region, forcing a complete redefinition of alternative routes.';

const SOURCES = ['TRACECA Report', 'Middle Corridor Analysis', 'ISW Afghanistan'];

const QUESTION =
  'What are the key commercial routes between Iran, Pakistan, Afghanistan and Central Asia? What are the main chokepoints at risk after the Iran war?';

export default function DemoOracle() {
  const [phase, setPhase] = useState(0);
  const [typed, setTyped] = useState('');
  const [charIdx, setCharIdx] = useState(0);

  useEffect(() => {
    if (phase === 0) {
      const t = setTimeout(() => setPhase(1), 900);
      return () => clearTimeout(t);
    }
    if (phase === 1) {
      const t = setTimeout(() => setPhase(2), 700);
      return () => clearTimeout(t);
    }
    if (phase === 2 && charIdx < FULL_ANSWER.length) {
      const t = setTimeout(() => {
        setTyped(FULL_ANSWER.slice(0, charIdx + 1));
        setCharIdx((c) => c + 1);
      }, 18);
      return () => clearTimeout(t);
    }
    if (phase === 2 && charIdx >= FULL_ANSWER.length) {
      const t = setTimeout(() => setPhase(3), 400);
      return () => clearTimeout(t);
    }
  }, [phase, charIdx]);

  return (
    <AppFrame label="ORACLE" labelColor="#10b981" badge="RAG · AI">
      <div className="flex h-[300px] flex-col bg-[#060e1c]">
        <div className="flex flex-1 flex-col gap-3 overflow-y-hidden px-4 py-3.5">
          {phase >= 1 && (
            <div className="animate-fadeInUp self-end rounded-[8px_8px_2px_8px] border border-primary/20 bg-primary/10 px-3 py-2 [max-width:85%]">
              <span className="text-xs leading-[1.5] text-foreground">{QUESTION}</span>
            </div>
          )}
          {phase >= 2 && (
            <div className="animate-fadeInUp self-start [max-width:92%]">
              <div className="rounded-[8px_8px_8px_2px] border border-white/[0.08] bg-[#1a2332] px-[13px] py-2.5">
                <div className="text-xs leading-[1.65] text-foreground">
                  {typed}
                  {phase === 2 && charIdx < FULL_ANSWER.length && (
                    <span className="ml-0.5 inline-block h-[13px] w-[7px] animate-[pulse-dot_0.7s_ease-in-out_infinite] rounded-[1px] bg-emerald-500 align-middle" />
                  )}
                </div>
                {phase >= 3 && (
                  <div className="mt-2.5 flex flex-wrap gap-1.5">
                    {SOURCES.map((s) => (
                      <span
                        key={s}
                        className="rounded-[3px] border border-white/[0.08] bg-white/[0.04] px-[7px] py-0.5 font-mono text-[9px] text-[#64748b]"
                      >
                        ↗ {s}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
        <div className="flex gap-2 border-t border-white/[0.06] px-3 py-2">
          <div className="flex-1 rounded-md border border-white/[0.07] bg-[#1e293b] px-3 py-[7px] text-xs text-[#374151]">
            Ask an intelligence question…
          </div>
          <div className="rounded-md bg-emerald-500 px-3.5 py-[7px] text-xs font-semibold text-white">
            Send
          </div>
        </div>
      </div>
    </AppFrame>
  );
}
