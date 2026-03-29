'use client';

import { Skeleton } from '@/components/ui/skeleton';
import { Card, CardContent } from '@/components/ui/card';

function StatsCardSkeleton() {
  return (
    <Card className="bg-white/[0.02] border-white/5">
      <CardContent className="p-6">
        <div className="flex items-start justify-between">
          <Skeleton className="w-12 h-12 rounded-lg bg-white/5" />
          <Skeleton className="w-12 h-4 bg-white/5" />
        </div>
        <div className="mt-4 space-y-2">
          <Skeleton className="w-24 h-8 bg-white/5" />
          <Skeleton className="w-32 h-4 bg-white/5" />
        </div>
      </CardContent>
    </Card>
  );
}

function TableRowSkeleton() {
  return (
    <div className="flex items-center gap-4 py-4 px-6 border-b border-white/5">
      <Skeleton className="w-20 h-6 bg-white/5 rounded-full" />
      <Skeleton className="flex-1 max-w-[250px] h-5 bg-white/5" />
      <Skeleton className="w-16 h-6 bg-white/5 rounded-full" />
      <Skeleton className="w-24 h-5 bg-white/5" />
      <Skeleton className="w-24 h-5 bg-white/5" />
      <Skeleton className="w-12 h-5 bg-white/5" />
      <Skeleton className="w-8 h-8 bg-white/5 rounded" />
    </div>
  );
}

export function StatsGridSkeleton() {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      {Array.from({ length: 4 }).map((_, i) => (
        <StatsCardSkeleton key={i} />
      ))}
    </div>
  );
}

export function TableSkeleton({ rows = 10 }: { rows?: number }) {
  return (
    <div className="rounded-lg border border-white/5 overflow-hidden bg-white/[0.02]">
      {/* Header */}
      <div className="flex items-center gap-4 py-3 px-6 border-b border-white/5 bg-white/[0.01]">
        <Skeleton className="w-16 h-4 bg-white/5" />
        <Skeleton className="flex-1 max-w-[250px] h-4 bg-white/5" />
        <Skeleton className="w-12 h-4 bg-white/5" />
        <Skeleton className="w-20 h-4 bg-white/5" />
        <Skeleton className="w-16 h-4 bg-white/5" />
        <Skeleton className="w-16 h-4 bg-white/5" />
        <Skeleton className="w-8 h-4 bg-white/5" />
      </div>
      {/* Rows */}
      {Array.from({ length: rows }).map((_, i) => (
        <TableRowSkeleton key={i} />
      ))}
    </div>
  );
}

export default function DashboardSkeleton() {
  return (
    <div className="space-y-8">
      <StatsGridSkeleton />
      <div>
        <Skeleton className="w-40 h-7 bg-white/5 mb-4" />
        <TableSkeleton rows={10} />
      </div>
    </div>
  );
}
