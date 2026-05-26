'use client';

import { useCallback, useRef } from 'react';
import { useSigma } from '@react-sigma/core';

/**
 * Coalesces multiple refresh requests into at most one `sigma.refresh()` per
 * animation frame. Shared by GraphEvents and GraphStyle to avoid duplicating
 * the rAF-batching logic (hover/leave can fire many times per second).
 */
export function useScheduledRefresh(): () => void {
  const sigma = useSigma();
  const pendingRef = useRef(false);

  return useCallback(() => {
    if (pendingRef.current) return;
    pendingRef.current = true;
    requestAnimationFrame(() => {
      sigma.refresh();
      pendingRef.current = false;
    });
  }, [sigma]);
}
