'use client';

import { LazyMotion, domAnimation, m, useReducedMotion } from 'motion/react';
import type { ReactNode } from 'react';

type RevealProps = {
  children: ReactNode;
  className?: string;
  delay?: number;
};

/**
 * Thin client wrapper around RSC section content. `children` is rendered by
 * the server as usual — only the entrance animation runs on the client, via
 * `LazyMotion` + `m` (not the full `motion` import) to keep the bundle small.
 *
 * Duration/easing mirror --duration-slow (600ms) / --ease-out-expo from
 * globals.css. Motion drives animations via WAAPI, not CSS, so it can't
 * reference CSS custom properties directly — keep these two in sync by hand.
 */
export default function Reveal({ children, className, delay = 0 }: RevealProps) {
  const shouldReduceMotion = useReducedMotion();

  return (
    <LazyMotion features={domAnimation}>
      <m.div
        className={className}
        initial={shouldReduceMotion ? false : { opacity: 0, y: 24 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, margin: '-80px' }}
        transition={{
          duration: shouldReduceMotion ? 0 : 0.6,
          delay: shouldReduceMotion ? 0 : delay,
          ease: [0.16, 1, 0.3, 1],
        }}
      >
        {children}
      </m.div>
    </LazyMotion>
  );
}
