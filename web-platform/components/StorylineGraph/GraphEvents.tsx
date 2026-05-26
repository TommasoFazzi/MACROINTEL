'use client';

import { useEffect } from 'react';
import { useRegisterEvents } from '@react-sigma/core';
import { useGraphContext } from './GraphContext';
import { useScheduledRefresh } from './useScheduledRefresh';

export default function GraphEvents() {
  const registerEvents = useRegisterEvents();
  const { setSelectedId, hoveredNodeRef, onHoverNode } = useGraphContext();
  const scheduleRefresh = useScheduledRefresh();

  useEffect(() => {
    registerEvents({
      clickNode: ({ node }) => {
        setSelectedId((prev) => (prev === Number(node) ? null : Number(node)));
      },
      enterNode: ({ node, event }) => {
        const id = Number(node);
        hoveredNodeRef.current = id;
        // event.x / event.y are viewport pixels relative to the sigma canvas
        onHoverNode({ id, x: event.x, y: event.y });
        scheduleRefresh();
      },
      leaveNode: () => {
        hoveredNodeRef.current = null;
        onHoverNode(null);
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
