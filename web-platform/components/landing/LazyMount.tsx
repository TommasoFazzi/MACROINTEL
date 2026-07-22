'use client';

import { useEffect, useRef, useState, type ReactNode } from 'react';

type LazyMountProps = {
  children: ReactNode;
  /** Rendered in place of `children` until the observer fires — must approximate the real
   *  content's height so mounting doesn't shift anything below it. */
  placeholderClassName?: string;
  /** How far outside the viewport to trigger the mount. Generous by default so a heavy
   *  child (e.g. a GSAP ScrollTrigger pin) is fully wired up before the user scrolls to it,
   *  not popping in mid-scroll. */
  rootMargin?: string;
};

/**
 * Defers mounting `children` until the wrapper is near the viewport (IntersectionObserver),
 * so route-level bundles/effects for below-the-fold scenes don't run on initial load. Used
 * for Scene 1 (SIGNAL DESCENT) — see redesign-frontend-live-surface tasks.md 3.22: gsap is
 * already dynamically imported inside the scene component itself (bundle isolation), this
 * adds the remaining piece (deferred mount) so the component doesn't even attempt that
 * import, or size its ScrollTrigger pin-spacer, until it's actually approaching view.
 */
export default function LazyMount({ children, placeholderClassName = 'h-screen w-full', rootMargin = '100% 0px' }: LazyMountProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [shouldMount, setShouldMount] = useState(false);

  useEffect(() => {
    if (shouldMount) return;
    const el = ref.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) {
          setShouldMount(true);
          observer.disconnect();
        }
      },
      { rootMargin }
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [shouldMount, rootMargin]);

  if (shouldMount) return <>{children}</>;
  return <div ref={ref} className={placeholderClassName} />;
}
