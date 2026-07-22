'use client';

import { useEffect, useState, type RefObject } from 'react';

/** Tracks whether an element is in the viewport — used to suspend ambient rAF loops off-screen. */
export function useInView<T extends HTMLElement>(ref: RefObject<T | null>, threshold = 0.2) {
  const [inView, setInView] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new IntersectionObserver(([entry]) => setInView(entry.isIntersecting), { threshold });
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref, threshold]);

  return inView;
}
