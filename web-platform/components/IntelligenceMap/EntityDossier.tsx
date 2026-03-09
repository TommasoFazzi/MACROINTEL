'use client';

import { X, MapPin, Calendar, FileText, ExternalLink, GitBranch, TrendingUp } from 'lucide-react';
import { ENTITY_TYPE_COLORS, ENTITY_TYPE_LABELS } from '@/types/entities';

interface Article {
  id: number;
  title: string;
  link: string;
  published_date: string;
  source: string;
}

interface Storyline {
  id: number;
  title: string;
  narrative_status: string;
  momentum_score: number;
  article_count: number;
  community_id: number | null;
}

interface EntityData {
  id: number;
  name: string;
  entity_type: string;
  latitude: number;
  longitude: number;
  mention_count: number;
  first_seen: string;
  last_seen: string;
  metadata: Record<string, any>;
  related_articles: Article[];
  related_storylines?: Storyline[];
}

interface EntityDossierProps {
  entity: EntityData | null;
  onClose: () => void;
}

const STATUS_COLORS: Record<string, string> = {
  emerging: 'text-yellow-400 bg-yellow-400/10 border-yellow-400/30',
  active: 'text-green-400 bg-green-400/10 border-green-400/30',
  stabilized: 'text-blue-400 bg-blue-400/10 border-blue-400/30',
};

export default function EntityDossier({ entity, onClose }: EntityDossierProps) {
  if (!entity) return null;

  const formatDate = (dateString: string) => {
    if (!dateString) return 'N/A';
    const date = new Date(dateString);
    return date.toLocaleDateString('it-IT', {
      year: 'numeric',
      month: 'short',
      day: 'numeric'
    });
  };

  const typeColor = ENTITY_TYPE_COLORS[entity.entity_type] || '#888';
  const typeLabel = ENTITY_TYPE_LABELS[entity.entity_type] || entity.entity_type;

  return (
    <div className="fixed right-4 top-4 bottom-4 w-[450px] bg-gray-900/95 backdrop-blur-sm border-2 border-cyan-500/30 shadow-2xl z-50 flex flex-col">
      {/* Header */}
      <div className="bg-gradient-to-r from-cyan-900/50 to-gray-900/50 border-b-2 border-cyan-500/30 p-4">
        <div className="flex items-start justify-between">
          <div className="flex-1">
            <div className="flex items-center gap-2 mb-2">
              <div className="w-2 h-2 rounded-full animate-pulse" style={{ backgroundColor: typeColor }}></div>
              <span className="text-xs font-mono text-cyan-400 uppercase tracking-wider">
                Entity Dossier
              </span>
            </div>
            <h2 className="text-2xl font-bold text-white mb-1">{entity.name}</h2>
            <div className="flex items-center gap-2">
              <span className="text-sm font-mono" style={{ color: typeColor }}>
                {typeLabel}
              </span>
              <span className="text-gray-500">•</span>
              <span className="text-sm text-gray-400 font-mono">
                ID: {entity.id.toString().padStart(6, '0')}
              </span>
            </div>
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
        {/* Intelligence Summary */}
        <div className="border border-cyan-500/20 bg-gray-800/50 p-4 rounded">
          <h3 className="text-sm font-mono text-cyan-400 uppercase mb-3 flex items-center gap-2">
            <MapPin size={14} />
            Intelligence Summary
          </h3>
          <div className="grid grid-cols-2 gap-3 text-sm">
            <div>
              <div className="text-gray-500 text-xs mb-1">Coordinates</div>
              <div className="font-mono text-white">
                {entity.latitude?.toFixed(4)}°N<br />
                {entity.longitude?.toFixed(4)}°E
              </div>
            </div>
            <div>
              <div className="text-gray-500 text-xs mb-1">Mention Count</div>
              <div className="font-mono text-xl font-bold" style={{ color: typeColor }}>
                {entity.mention_count}
              </div>
            </div>
            <div>
              <div className="text-gray-500 text-xs mb-1">First Seen</div>
              <div className="font-mono text-white text-xs">
                {formatDate(entity.first_seen)}
              </div>
            </div>
            <div>
              <div className="text-gray-500 text-xs mb-1">Last Seen</div>
              <div className="font-mono text-white text-xs">
                {formatDate(entity.last_seen)}
              </div>
            </div>
          </div>
        </div>

        {/* Related Storylines — the intelligence layer */}
        {entity.related_storylines && entity.related_storylines.length > 0 && (
          <div className="border border-orange-500/20 bg-gray-800/50 p-4 rounded">
            <h3 className="text-sm font-mono text-orange-400 uppercase mb-3 flex items-center gap-2">
              <GitBranch size={14} />
              Active Storylines ({entity.related_storylines.length})
            </h3>
            <div className="space-y-2.5">
              {entity.related_storylines.map((storyline) => (
                <a
                  key={storyline.id}
                  href={`/stories?highlight=${storyline.id}`}
                  className="block border-l-2 border-orange-500/30 pl-3 py-2 hover:border-orange-500 hover:bg-gray-700/30 transition-all group rounded-r"
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded border ${STATUS_COLORS[storyline.narrative_status] || 'text-gray-400'}`}>
                      {storyline.narrative_status.toUpperCase()}
                    </span>
                    <div className="flex items-center gap-1 text-xs text-gray-500">
                      <TrendingUp size={10} />
                      <span className="font-mono">{(storyline.momentum_score * 100).toFixed(0)}%</span>
                    </div>
                  </div>
                  <h4 className="text-sm text-white group-hover:text-orange-400 transition-colors leading-snug">
                    {storyline.title}
                  </h4>
                  <div className="text-[10px] text-gray-500 font-mono mt-1">
                    {storyline.article_count} articles
                    {storyline.community_id != null && ` • Community ${storyline.community_id}`}
                  </div>
                </a>
              ))}
            </div>
          </div>
        )}

        {/* Related Articles */}
        <div className="border border-cyan-500/20 bg-gray-800/50 p-4 rounded">
          <h3 className="text-sm font-mono text-cyan-400 uppercase mb-3 flex items-center gap-2">
            <FileText size={14} />
            Related Intelligence ({entity.related_articles?.length || 0})
          </h3>

          {entity.related_articles && entity.related_articles.length > 0 ? (
            <div className="space-y-3 max-h-[400px] overflow-y-auto">
              {entity.related_articles.map((article) => (
                <div
                  key={article.id}
                  className="border-l-2 border-cyan-500/30 pl-3 py-2 hover:border-cyan-500 transition-colors group"
                >
                  <div className="flex items-start justify-between gap-2 mb-1">
                    <div className="text-xs font-mono text-gray-500 flex items-center gap-2">
                      <Calendar size={12} />
                      {formatDate(article.published_date)}
                    </div>
                    <a
                      href={article.link}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-cyan-400 hover:text-cyan-300 transition-colors"
                      aria-label="Open article"
                    >
                      <ExternalLink size={14} />
                    </a>
                  </div>
                  <h4 className="text-sm text-white group-hover:text-cyan-400 transition-colors leading-snug mb-1">
                    {article.title}
                  </h4>
                  <div className="text-xs text-gray-500 font-mono">
                    Source: {article.source}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-sm text-gray-500 text-center py-4">
              No related articles found
            </div>
          )}
        </div>

        {/* Metadata (if available) */}
        {entity.metadata && Object.keys(entity.metadata).length > 0 && (
          <div className="border border-cyan-500/20 bg-gray-800/50 p-4 rounded">
            <h3 className="text-sm font-mono text-cyan-400 uppercase mb-3">
              Additional Metadata
            </h3>
            <pre className="text-xs text-gray-400 font-mono overflow-x-auto">
              {JSON.stringify(entity.metadata, null, 2)}
            </pre>
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="border-t-2 border-cyan-500/30 bg-gray-900/50 px-4 py-3">
        <div className="text-xs text-gray-500 font-mono text-center">
          CLASSIFIED • INTELLIGENCE_ITA • {new Date().toISOString().split('T')[0]}
        </div>
      </div>
    </div>
  );
}
