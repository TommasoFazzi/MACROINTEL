'use client';

import { X, TrendingUp, FileText, GitBranch, Calendar, Tag } from 'lucide-react';
import { useStorylineDetail } from '@/hooks/useStories';
import type { NarrativeStatus } from '@/types/stories';

interface StorylineDossierProps {
  storylineId: number | null;
  onClose: () => void;
  onNavigate: (id: number) => void;
}

const STATUS_COLORS: Record<NarrativeStatus, string> = {
  emerging: 'text-[#FF6B35]',
  active: 'text-[#00A8E8]',
  stabilized: 'text-gray-400',
};

const STATUS_BG: Record<NarrativeStatus, string> = {
  emerging: 'bg-[#FF6B35]/20 border-[#FF6B35]/40',
  active: 'bg-[#00A8E8]/20 border-[#00A8E8]/40',
  stabilized: 'bg-gray-500/20 border-gray-500/40',
};

export default function StorylineDossier({ storylineId, onClose, onNavigate }: StorylineDossierProps) {
  const { detail, isLoading, error } = useStorylineDetail(storylineId);

  if (!storylineId) return null;

  const formatDate = (dateString: string | null) => {
    if (!dateString) return 'N/A';
    return new Date(dateString).toLocaleDateString('it-IT', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  };

  const getMomentumLabel = (score: number) => {
    if (score >= 0.8) return { label: 'HIGH', color: 'text-red-400' };
    if (score >= 0.5) return { label: 'MEDIUM', color: 'text-yellow-400' };
    if (score >= 0.3) return { label: 'LOW', color: 'text-gray-400' };
    return { label: 'MINIMAL', color: 'text-gray-600' };
  };

  return (
    <div className="fixed right-4 top-4 bottom-4 w-[450px] bg-gray-900/95 backdrop-blur-sm border-2 border-[#FF6B35]/30 shadow-2xl z-50 flex flex-col">
      {/* Header */}
      <div className="bg-gradient-to-r from-orange-900/30 to-gray-900/50 border-b-2 border-[#FF6B35]/30 p-4">
        <div className="flex items-start justify-between">
          <div className="flex-1">
            <div className="flex items-center gap-2 mb-2">
              <div className="w-2 h-2 bg-[#FF6B35] rounded-full animate-pulse" />
              <span className="text-xs font-mono text-[#FF6B35] uppercase tracking-wider">
                Storyline Dossier
              </span>
            </div>
            {isLoading ? (
              <div className="h-8 w-64 bg-white/10 animate-pulse rounded" />
            ) : (
              <>
                <h2 className="text-xl font-bold text-white mb-1 leading-tight">
                  {detail?.storyline.title || 'Loading...'}
                </h2>
                {detail && (
                  <div className="flex items-center gap-2 mt-1">
                    <span className={`text-xs font-mono px-2 py-0.5 rounded border ${STATUS_BG[detail.storyline.narrative_status as NarrativeStatus] || STATUS_BG.active}`}>
                      <span className={STATUS_COLORS[detail.storyline.narrative_status as NarrativeStatus] || 'text-white'}>
                        {detail.storyline.narrative_status.toUpperCase()}
                      </span>
                    </span>
                    <span className="text-gray-500">|</span>
                    <span className="text-xs text-gray-400 font-mono">
                      ID: {detail.storyline.id}
                    </span>
                  </div>
                )}
              </>
            )}
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-white transition-colors p-1"
            aria-label="Close dossier"
          >
            <X size={24} />
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {error && (
          <div className="text-red-400 text-sm font-mono p-3 border border-red-500/30 rounded bg-red-900/20">
            Error loading storyline data
          </div>
        )}

        {isLoading && (
          <div className="space-y-3">
            {[1, 2, 3].map((i) => (
              <div key={i} className="h-20 bg-white/5 animate-pulse rounded" />
            ))}
          </div>
        )}

        {detail && (
          <>
            {/* Momentum & Stats */}
            <div className="border border-[#FF6B35]/20 bg-gray-800/50 p-4 rounded">
              <h3 className="text-sm font-mono text-[#FF6B35] uppercase mb-3 flex items-center gap-2">
                <TrendingUp size={14} />
                Momentum Analysis
              </h3>
              <div className="grid grid-cols-3 gap-3 text-sm">
                <div>
                  <div className="text-gray-500 text-xs mb-1">Momentum</div>
                  <div className={`font-mono text-xl font-bold ${getMomentumLabel(detail.storyline.momentum_score).color}`}>
                    {detail.storyline.momentum_score.toFixed(2)}
                  </div>
                  <div className={`text-xs font-mono ${getMomentumLabel(detail.storyline.momentum_score).color}`}>
                    {getMomentumLabel(detail.storyline.momentum_score).label}
                  </div>
                </div>
                <div>
                  <div className="text-gray-500 text-xs mb-1">Articles</div>
                  <div className="font-mono text-white text-xl font-bold">
                    {detail.storyline.article_count}
                  </div>
                </div>
                <div>
                  <div className="text-gray-500 text-xs mb-1">Days Active</div>
                  <div className="font-mono text-white text-xl font-bold">
                    {detail.storyline.days_active ?? 'N/A'}
                  </div>
                </div>
              </div>

              {/* Momentum bar */}
              <div className="mt-3">
                <div className="w-full h-2 bg-gray-700 rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all duration-500"
                    style={{
                      width: `${Math.min(100, detail.storyline.momentum_score * 100)}%`,
                      backgroundColor: detail.storyline.momentum_score >= 0.8
                        ? '#ef4444'
                        : detail.storyline.momentum_score >= 0.5
                        ? '#eab308'
                        : '#6b7280',
                    }}
                  />
                </div>
              </div>
            </div>

            {/* Summary */}
            {detail.storyline.summary && (
              <div className="border border-[#FF6B35]/20 bg-gray-800/50 p-4 rounded">
                <h3 className="text-sm font-mono text-[#FF6B35] uppercase mb-3">
                  Summary
                </h3>
                <p className="text-sm text-gray-300 leading-relaxed">
                  {detail.storyline.summary}
                </p>
              </div>
            )}

            {/* Key Entities */}
            {detail.storyline.key_entities.length > 0 && (
              <div className="border border-[#FF6B35]/20 bg-gray-800/50 p-4 rounded">
                <h3 className="text-sm font-mono text-[#FF6B35] uppercase mb-3 flex items-center gap-2">
                  <Tag size={14} />
                  Key Entities
                </h3>
                <div className="flex flex-wrap gap-2">
                  {detail.storyline.key_entities.map((entity, i) => (
                    <span
                      key={i}
                      className="px-2 py-1 bg-[#FF6B35]/10 border border-[#FF6B35]/30 text-[#FF6B35] text-xs font-mono rounded"
                    >
                      {entity}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Community */}
            {detail.storyline.community_id != null && (
              <div className="border border-purple-500/20 bg-gray-800/50 p-4 rounded">
                <h3 className="text-sm font-mono text-purple-400 uppercase mb-3 flex items-center gap-2">
                  <GitBranch size={14} />
                  Community
                </h3>
                <div className="flex items-center gap-2">
                  <div
                    className="w-4 h-4 rounded-full flex-shrink-0"
                    style={{
                      backgroundColor: [
                        '#FF6B35', '#00A8E8', '#39D353', '#FFD700',
                        '#FF4081', '#7B61FF', '#00E5CC', '#FF7043',
                      ][detail.storyline.community_id % 8],
                    }}
                  />
                  <span className="text-sm text-gray-300 font-mono">
                    Community #{detail.storyline.community_id}
                  </span>
                </div>
              </div>
            )}

            {/* Related Storylines */}
            {detail.related_storylines.length > 0 && (
              <div className="border border-[#00A8E8]/20 bg-gray-800/50 p-4 rounded">
                <h3 className="text-sm font-mono text-[#00A8E8] uppercase mb-3 flex items-center gap-2">
                  <GitBranch size={14} />
                  Connected Storylines ({detail.related_storylines.length})
                </h3>
                <div className="space-y-2">
                  {detail.related_storylines.map((rel) => (
                    <button
                      key={rel.id}
                      onClick={() => onNavigate(rel.id)}
                      className="w-full text-left border-l-2 border-[#00A8E8]/30 pl-3 py-2 hover:border-[#00A8E8] transition-colors group"
                    >
                      <div className="text-sm text-white group-hover:text-[#00A8E8] transition-colors leading-snug">
                        {rel.title}
                      </div>
                      <div className="flex items-center gap-2 mt-1">
                        <span className="text-xs font-mono text-gray-500">
                          Weight: {rel.weight.toFixed(2)}
                        </span>
                        <span className="text-xs font-mono text-gray-600">
                          {rel.relation_type}
                        </span>
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Recent Articles */}
            {detail.recent_articles.length > 0 && (
              <div className="border border-[#FF6B35]/20 bg-gray-800/50 p-4 rounded">
                <h3 className="text-sm font-mono text-[#FF6B35] uppercase mb-3 flex items-center gap-2">
                  <FileText size={14} />
                  Recent Articles ({detail.recent_articles.length})
                </h3>
                <div className="space-y-3 max-h-[300px] overflow-y-auto">
                  {detail.recent_articles.map((article) => (
                    <div
                      key={article.id}
                      className="border-l-2 border-[#FF6B35]/30 pl-3 py-2"
                    >
                      <div className="flex items-center gap-2 mb-1">
                        <Calendar size={12} className="text-gray-500" />
                        <span className="text-xs font-mono text-gray-500">
                          {formatDate(article.published_date)}
                        </span>
                        {article.source && (
                          <>
                            <span className="text-gray-600">|</span>
                            <span className="text-xs font-mono text-gray-500">
                              {article.source}
                            </span>
                          </>
                        )}
                      </div>
                      <h4 className="text-sm text-white leading-snug">
                        {article.title}
                      </h4>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {/* Footer */}
      <div className="border-t-2 border-[#FF6B35]/30 bg-gray-900/50 px-4 py-3">
        <div className="text-xs text-gray-500 font-mono text-center">
          CLASSIFIED | NARRATIVE ENGINE | {new Date().toISOString().split('T')[0]}
        </div>
      </div>
    </div>
  );
}
