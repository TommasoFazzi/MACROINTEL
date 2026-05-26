'use client';

import { useEffect, useRef } from 'react';
import { useSigma, useRegisterEvents } from '@react-sigma/core';
import { useGraphContext } from './GraphContext';

export default function GraphEvents() {
  const sigma = useSigma();
  const registerEvents = useRegisterEvents();
  const { setSelectedId, hoveredNodeRef } = useGraphContext();
  const pendingRefreshRef = useRef(false);

  // Batched refresh — at most one sigma.refresh() per animation frame
  const scheduleRefresh = () => {
    if (pendingRefreshRef.current) return;
    pendingRefreshRef.current = true;
    requestAnimationFrame(() => {
      sigma.refresh();
      pendingRefreshRef.current = false;
    });
  };

  useEffect(() => {
    registerEvents({
      clickNode: ({ node }) => {
        setSelectedId((prev) => (prev === Number(node) ? null : Number(node)));
      },
      enterNode: ({ node }) => {
        hoveredNodeRef.current = Number(node);
        scheduleRefresh();
      },
      leaveNode: () => {
        hoveredNodeRef.current = null;
        scheduleRefresh();
      },
      clickStage: () => {
        setSelectedId(null);
      },
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [registerEvents]);

  return null;
}
