'use client';

import { useState } from 'react';
import { FAQS } from '@/lib/landing/schema';

export default function FAQ() {
  const [open, setOpen] = useState<number | null>(null);
  const toggle = (i: number) => setOpen(open === i ? null : i);

  return (
    <section id="faq" className="bg-background py-[100px]">
      <div className="mx-auto max-w-[780px] px-10">
        <div className="mb-14 text-center">
          <div className="section-label justify-center">FAQ</div>
          <h2 className="text-[40px] font-extrabold tracking-[-0.02em]">Common questions.</h2>
        </div>
        <div className="flex flex-col gap-0.5">
          {FAQS.map((faq, i) => {
            const isOpen = open === i;
            return (
              <div key={faq.q} className="overflow-hidden border-b border-white/[0.06]">
                <button
                  type="button"
                  onClick={() => toggle(i)}
                  aria-expanded={isOpen}
                  className="flex w-full cursor-pointer items-center justify-between gap-4 border-none bg-none py-5 text-left"
                >
                  <span
                    className="text-[15px] font-semibold leading-[1.4] transition-colors duration-150"
                    style={{ color: isOpen ? '#FF6B35' : '#ededed' }}
                  >
                    {faq.q}
                  </span>
                  <span
                    className="inline-block shrink-0 text-lg transition-transform duration-[250ms]"
                    style={{ color: isOpen ? '#FF6B35' : '#64748b', transform: isOpen ? 'rotate(45deg)' : 'rotate(0deg)' }}
                  >
                    +
                  </span>
                </button>
                <div
                  className="overflow-hidden transition-[max-height] duration-[350ms] ease-in-out"
                  style={{ maxHeight: isOpen ? 240 : 0 }}
                >
                  <p className="pb-5 text-sm leading-[1.75] text-muted-foreground">
                    {faq.a}
                  </p>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
