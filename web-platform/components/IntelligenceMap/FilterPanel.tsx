'use client';

import { useState, useCallback, useEffect, useRef } from 'react';
import { Search, Filter, X, ChevronDown, ChevronUp } from 'lucide-react';
import { ENTITY_TYPE_COLORS, ENTITY_TYPE_LABELS } from '@/types/entities';
import type { EntityFilters } from '@/utils/api';

interface FilterPanelProps {
  onFilterChange: (filters: EntityFilters) => void;
  entityCount?: { filtered: number; total: number };
}

const ENTITY_TYPES = ['GPE', 'ORG', 'PERSON', 'LOC', 'FAC'] as const;

const DAYS_OPTIONS = [
  { value: 0, label: 'ALL' },
  { value: 7, label: '7D' },
  { value: 14, label: '14D' },
  { value: 30, label: '30D' },
  { value: 90, label: '90D' },
];

export default function FilterPanel({ onFilterChange, entityCount }: FilterPanelProps) {
  const [expanded, setExpanded] = useState(false);
  const [activeTypes, setActiveTypes] = useState<Set<string>>(new Set());
  const [days, setDays] = useState(0);
  const [minMentions, setMinMentions] = useState(1);
  const [minScore, setMinScore] = useState(0); // 0–100, divided by 100 before sending
  const [search, setSearch] = useState('');
  const debounceRef = useRef<NodeJS.Timeout | null>(null);

  // Emit filters on change (debounced for search)
  const emitFilters = useCallback(() => {
    const filters: EntityFilters = {};

    if (activeTypes.size > 0 && activeTypes.size < ENTITY_TYPES.length) {
      filters.entity_type = Array.from(activeTypes).join(',');
    }
    if (days > 0) filters.days = days;
    if (minMentions > 1) filters.min_mentions = minMentions;
    if (minScore > 0) filters.min_score = minScore / 100; // slider 0–100 → 0.0–1.0
    if (search.trim()) filters.search = search.trim();

    onFilterChange(filters);
  }, [activeTypes, days, minMentions, minScore, search, onFilterChange]);

  // Trigger immediately for non-search changes
  useEffect(() => {
    emitFilters();
  }, [activeTypes, days, minMentions, minScore]); // eslint-disable-line react-hooks/exhaustive-deps

  // Debounce search input (300ms)
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      emitFilters();
    }, 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [search]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleType = (type: string) => {
    setActiveTypes(prev => {
      const next = new Set(prev);
      if (next.has(type)) {
        next.delete(type);
      } else {
        next.add(type);
      }
      return next;
    });
  };

  const hasActiveFilters = activeTypes.size > 0 || days > 0 || minMentions > 1 || minScore > 0 || search.trim().length > 0;

  const clearAll = () => {
    setActiveTypes(new Set());
    setDays(0);
    setMinMentions(1);
    setMinScore(0);
    setSearch('');
  };

  return (
    <div className="absolute bottom-20 left-1/2 -translate-x-1/2 z-30 pointer-events-auto">
      {/* Collapsed toggle bar */}
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 px-4 py-2 rounded-t-lg font-mono text-xs
                   bg-slate-900/90 backdrop-blur-md border border-cyan-500/20 border-b-0
                   text-cyan-400 hover:text-cyan-300 transition-colors"
      >
        <Filter size={14} />
        <span>FILTERS</span>
        {hasActiveFilters && (
          <span className="ml-1 px-1.5 py-0.5 rounded bg-cyan-500/20 text-cyan-300 text-[10px]">
            ACTIVE
          </span>
        )}
        {expanded ? <ChevronDown size={12} /> : <ChevronUp size={12} />}
      </button>

      {/* Expanded panel */}
      {expanded && (
        <div className="bg-slate-900/95 backdrop-blur-md border border-cyan-500/20
                        rounded-lg rounded-tl-none p-4 font-mono text-xs
                        min-w-[480px] max-w-[600px] shadow-2xl shadow-cyan-500/5">

          {/* Row 1: Entity type toggles */}
          <div className="flex items-center gap-2 mb-3">
            <span className="text-gray-500 w-12 shrink-0">TYPE</span>
            <div className="flex gap-1.5 flex-wrap">
              {ENTITY_TYPES.map(type => {
                const isActive = activeTypes.has(type);
                const color = ENTITY_TYPE_COLORS[type];
                return (
                  <button
                    type="button"
                    key={type}
                    onClick={() => toggleType(type)}
                    className={`flex items-center gap-1.5 px-2.5 py-1 rounded border transition-all ${
                      isActive
                        ? 'border-current bg-current/10'
                        : 'border-gray-700 hover:border-gray-500 text-gray-500 hover:text-gray-300'
                    }`}
                    style={isActive ? { color, borderColor: color } : undefined}
                    title={ENTITY_TYPE_LABELS[type]}
                  >
                    <span
                      className="w-2 h-2 rounded-full shrink-0"
                      style={{ backgroundColor: isActive ? color : '#4b5563' }}
                    />
                    <span>{type}</span>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Row 2: Time window + Min mentions */}
          <div className="flex items-center gap-4 mb-3">
            <div className="flex items-center gap-2">
              <span className="text-gray-500 w-12 shrink-0">TIME</span>
              <div className="flex gap-1">
                {DAYS_OPTIONS.map(opt => (
                  <button
                    type="button"
                    key={opt.value}
                    onClick={() => setDays(opt.value)}
                    className={`px-2 py-1 rounded text-[10px] transition-all ${
                      days === opt.value
                        ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/40'
                        : 'text-gray-500 hover:text-gray-300 border border-transparent'
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="flex items-center gap-2">
              <span className="text-gray-500 shrink-0">MIN</span>
              <input
                type="number"
                min={1}
                max={999}
                value={minMentions}
                onChange={e => setMinMentions(Math.max(1, parseInt(e.target.value) || 1))}
                title="Minimum mentions"
                placeholder="1"
                className="w-14 px-2 py-1 rounded bg-slate-800 border border-gray-700
                           text-cyan-300 text-center focus:border-cyan-500 focus:outline-none"
              />
              <span className="text-gray-600">mentions</span>
            </div>
          </div>

          {/* Row 3: Min intelligence score slider */}
          <div className="flex items-center gap-3 mb-3">
            <span className="text-gray-500 w-12 shrink-0">SCORE</span>
            <input
              type="range"
              min={0}
              max={100}
              step={5}
              value={minScore}
              onChange={e => setMinScore(parseInt(e.target.value))}
              className="flex-1 h-1 accent-cyan-400 cursor-pointer"
              title="Minimum intelligence score"
            />
            <span className={`w-10 text-right tabular-nums ${minScore > 0 ? 'text-cyan-300' : 'text-gray-600'}`}>
              {minScore > 0 ? `≥${(minScore / 100).toFixed(2)}` : 'OFF'}
            </span>
          </div>

          {/* Row 4: Search */}
          <div className="flex items-center gap-2">
            <span className="text-gray-500 w-12 shrink-0">
              <Search size={14} />
            </span>
            <div className="relative flex-1">
              <input
                type="text"
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder="Search entities..."
                className="w-full px-3 py-1.5 rounded bg-slate-800 border border-gray-700
                           text-cyan-300 placeholder-gray-600 focus:border-cyan-500
                           focus:outline-none pr-8"
              />
              {search && (
                <button
                  type="button"
                  onClick={() => setSearch('')}
                  title="Clear search"
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300"
                >
                  <X size={12} />
                </button>
              )}
            </div>

            {/* Clear all button */}
            {hasActiveFilters && (
              <button
                type="button"
                onClick={clearAll}
                className="px-2 py-1 rounded text-red-400/70 hover:text-red-400
                           border border-red-500/20 hover:border-red-500/40 transition-all text-[10px]"
              >
                CLEAR
              </button>
            )}
          </div>

          {/* Filter result count */}
          {entityCount && entityCount.total > 0 && (
            <div className="mt-2 pt-2 border-t border-gray-800 text-gray-500 text-[10px]">
              SHOWING {entityCount.filtered.toLocaleString()} OF {entityCount.total.toLocaleString()} ENTITIES
            </div>
          )}
        </div>
      )}
    </div>
  );
}
