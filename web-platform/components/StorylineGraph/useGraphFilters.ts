'use client';

import { useMemo, useState } from 'react';
import type { GraphNetwork } from '@/types/stories';
import { useTickerList, useTickerThemes } from '@/hooks/useStories';
import { COMMUNITY_PALETTE, COMMUNITY_OTHER } from '@/lib/communityColors';
import type { FilterState } from './GraphContext';

const TOP_N = COMMUNITY_PALETTE.length; // 15

export interface CommunityLabel {
  cid: number;
  label: string;
  count: number;
}

/**
 * Owns all derivation + filter state for the narrative graph (business logic),
 * keeping StorylineGraph focused on layout/rendering (UI). Given the raw graph
 * data it returns the community color map, the active highlight sets, the
 * Sigma-facing `filterState`, and the entity-autocomplete data — plus the
 * controlled filter state and its setters.
 */
export function useGraphFilters(graphData: GraphNetwork | null) {
  // ── Filter state ────────────────────────────────────────────────────────
  const [minMomentum, setMinMomentum] = useState(0.2);
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);
  const [legendExpanded, setLegendExpanded] = useState(false);
  const [selectedEntities, setSelectedEntities] = useState<string[]>([]);
  const [entityQuery, setEntityQuery] = useState('');
  const [showEntityDropdown, setShowEntityDropdown] = useState(false);
  const [titleQuery, setTitleQuery] = useState('');
  const [filterIsolate, setFilterIsolate] = useState(false);
  const [showNew, setShowNew] = useState(false);

  const { tickers } = useTickerList();
  const { themes } = useTickerThemes(selectedTicker);

  // ── Community rank-based color map ────────────────────────────────────────
  const { communityColorMap, communityLabels, othersCount, othersNodes } = useMemo(() => {
    if (!graphData) {
      return {
        communityColorMap: new Map<number, string>(),
        communityLabels: [] as CommunityLabel[],
        othersCount: 0,
        othersNodes: 0,
      };
    }
    const communityMap = new Map<number, { count: number; community_name: string | null }>();
    for (const node of graphData.nodes) {
      const cid = node.community_id;
      if (cid == null) continue;
      if (!communityMap.has(cid)) communityMap.set(cid, { count: 0, community_name: null });
      const entry = communityMap.get(cid)!;
      entry.count++;
      if (!entry.community_name && node.community_name) entry.community_name = node.community_name;
    }
    const allSorted = Array.from(communityMap.entries())
      .map(([cid, { count, community_name }]) => ({ cid, label: community_name || `Community ${cid}`, count }))
      .sort((a, b) => b.count - a.count);
    const colorMap = new Map<number, string>();
    allSorted.forEach(({ cid }, idx) => {
      colorMap.set(cid, idx < TOP_N ? COMMUNITY_PALETTE[idx] : COMMUNITY_OTHER);
    });
    return {
      communityColorMap: colorMap,
      communityLabels: allSorted.slice(0, TOP_N),
      othersCount: Math.max(0, allSorted.length - TOP_N),
      othersNodes: allSorted.slice(TOP_N).reduce((sum, c) => sum + c.count, 0),
    };
  }, [graphData]);

  // ── Highlight sets ────────────────────────────────────────────────────────
  const tickerHighlightIds = useMemo<Set<number>>(() => {
    if (!themes?.themes) return new Set();
    return new Set(themes.themes.map((t) => t.storyline_id));
  }, [themes]);

  const entityHighlightIds = useMemo<Set<number>>(() => {
    const hasFilter = selectedEntities.length > 0 || titleQuery.trim();
    if (!hasFilter || filterIsolate || !graphData) return new Set<number>();
    const title = titleQuery.trim().toLowerCase();
    const sels = selectedEntities.map((s) => s.toLowerCase());
    return new Set(
      graphData.nodes
        .filter((n) => {
          const entityMatch =
            sels.length === 0 ||
            sels.some((sel) => n.key_entities?.some((ke) => ke.toLowerCase().includes(sel)));
          const titleMatch = !title || n.title.toLowerCase().includes(title);
          return entityMatch && titleMatch;
        })
        .map((n) => n.id)
    );
  }, [graphData, selectedEntities, titleQuery, filterIsolate]);

  // "What's new" — emerging storylines (data already on each node, no history needed)
  const newHighlightIds = useMemo<Set<number>>(() => {
    if (!showNew || !graphData) return new Set<number>();
    return new Set(
      graphData.nodes.filter((n) => n.narrative_status === 'emerging').map((n) => n.id)
    );
  }, [showNew, graphData]);

  const filterState = useMemo<FilterState>(() => {
    // Priority: explicit ticker/entity filter wins; otherwise the "Novità" toggle
    const highlightIds =
      tickerHighlightIds.size > 0
        ? tickerHighlightIds
        : entityHighlightIds.size > 0
        ? entityHighlightIds
        : newHighlightIds;
    return { momentumMin: minMomentum, isolate: filterIsolate, highlightIds };
  }, [minMomentum, filterIsolate, tickerHighlightIds, entityHighlightIds, newHighlightIds]);

  // ── Entity autocomplete ────────────────────────────────────────────────────
  const allEntities = useMemo(
    () =>
      [...new Set((graphData?.nodes ?? []).flatMap((n) => n.key_entities || []))]
        .sort()
        .filter((e) => e.length > 1),
    [graphData]
  );
  const entitySuggestions = useMemo(() => {
    if (entityQuery.length < 2) return [];
    const q = entityQuery.toLowerCase();
    return allEntities
      .filter((e) => e.toLowerCase().includes(q) && !selectedEntities.includes(e))
      .slice(0, 15);
  }, [allEntities, entityQuery, selectedEntities]);

  return {
    // filter state + setters
    minMomentum, setMinMomentum,
    selectedTicker, setSelectedTicker,
    legendExpanded, setLegendExpanded,
    selectedEntities, setSelectedEntities,
    entityQuery, setEntityQuery,
    showEntityDropdown, setShowEntityDropdown,
    titleQuery, setTitleQuery,
    filterIsolate, setFilterIsolate,
    showNew, setShowNew,
    // data sources
    tickers,
    // derived
    communityColorMap, communityLabels, othersCount, othersNodes,
    tickerHighlightIds,
    filterState,
    entitySuggestions,
  };
}
