'use client';

import { FileText, BookOpen, GitBranch, Radio } from 'lucide-react';
import StatsCard from './StatsCard';
import type { DashboardStats } from '@/types/dashboard';

interface StatsGridProps {
  stats: DashboardStats | undefined;
  storiesCount: number | null;
}

export default function StatsGrid({ stats, storiesCount }: StatsGridProps) {
  if (!stats) return null;

  const { overview, articles } = stats;

  const statsItems = [
    {
      icon: <FileText className="w-6 h-6" />,
      value: overview.total_articles,
      label: 'Total Articles',
      trend: articles.recent_7d > 0 ? { value: articles.recent_7d, isPositive: true } : undefined,
    },
    {
      icon: <BookOpen className="w-6 h-6" />,
      value: overview.total_reports,
      label: 'Intelligence Briefs',
    },
    {
      icon: <GitBranch className="w-6 h-6" />,
      value: storiesCount ?? '—',
      label: 'Active Storylines',
    },
    {
      icon: <Radio className="w-6 h-6" />,
      value: 33,
      label: 'Sources Monitored',
    },
  ];

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      {statsItems.map((item, index) => (
        <StatsCard
          key={index}
          icon={item.icon}
          value={item.value}
          label={item.label}
          trend={item.trend}
        />
      ))}
    </div>
  );
}
