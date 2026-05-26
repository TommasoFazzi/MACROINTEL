'use client';

import { createContext, useContext, MutableRefObject, Dispatch, SetStateAction } from 'react';

export interface FilterState {
  momentumMin: number;
  isolate: boolean;
  highlightIds: Set<number>;
}

export interface GraphContextValue {
  selectedId: number | null;
  setSelectedId: Dispatch<SetStateAction<number | null>>;
  // useRef, not React state — hover fires 50+/sec; keeping it out of React tree avoids
  // cascading nodeReducer re-runs on every mouse movement
  hoveredNodeRef: MutableRefObject<number | null>;
  // useMemo-stabilised — only changes when selectedId changes
  egoNeighborIds: Set<number>;
  filterState: FilterState;
  communityColorMap: Map<number, string>;
}

export const GraphContext = createContext<GraphContextValue | null>(null);

export function useGraphContext(): GraphContextValue {
  const ctx = useContext(GraphContext);
  if (!ctx) throw new Error('useGraphContext must be used inside GraphContext.Provider');
  return ctx;
}
