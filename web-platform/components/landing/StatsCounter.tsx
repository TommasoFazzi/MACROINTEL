'use client';

import { useEffect, useRef, useState } from 'react';
import useSWR from 'swr';
import type { DashboardStatsResponse } from '@/types/dashboard';

const fetcher = (url: string) =>
  fetch(url).then((r) => (r.ok ? r.json() : null)).catch(() => null);

function useCountUp(target: number, duration = 1600) {
  const [value, setValue] = useState(0);
  const frameRef = useRef<number | null>(null);

  useEffect(() => {
    if (target === 0) return;
    const start = performance.now();

    const step = (now: number) => {
      const progress = Math.min((now - start) / duration, 1);
      // ease-out cubic
      const ease = 1 - Math.pow(1 - progress, 3);
      setValue(Math.floor(ease * target));
      if (progress < 1) frameRef.current = requestAnimationFrame(step);
    };

    frameRef.current = requestAnimationFrame(step);
    return () => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    };
  }, [target, duration]);

  return value;
}

function Counter({
  target,
  suffix = '',
  label,
  isLoading,
}: {
  target: number;
  suffix?: string;
  label: string;
  isLoading: boolean;
}) {
  const [inView, setInView] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const count = useCountUp(inView ? target : 0);

  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => { if (entry.isIntersecting) { setInView(true); observer.disconnect(); } },
      { threshold: 0.3 }
    );
    if (ref.current) observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);

  return (
    <div ref={ref} className="text-center">
      {isLoading ? (
        <div className="h-10 w-24 mx-auto bg-white/5 rounded animate-pulse mb-2" />
      ) : (
        <div className="text-4xl md:text-5xl font-extrabold text-[#FF6B35] mb-1 tabular-nums">
          {count.toLocaleString()}{suffix}
        </div>
      )}
      <div className="text-sm text-gray-400 uppercase tracking-wider">{label}</div>
    </div>
  );
}

export default function StatsCounter() {
  const { data, isLoading } = useSWR<DashboardStatsResponse>(
    '/api/proxy/dashboard/stats',
    fetcher,
    { revalidateOnFocus: false, dedupingInterval: 300000 }
  );

  const articlesThisWeek = data?.data?.articles?.recent_7d ?? 0;
  const totalReports = data?.data?.overview?.total_reports ?? 0;
  const totalArticles = data?.data?.overview?.total_articles ?? 0;

  const stats = [
    { target: articlesThisWeek, suffix: '+', label: 'Articles Processed This Week' },
    { target: totalArticles, suffix: '+', label: 'Total Intelligence Items Indexed' },
    { target: totalReports, suffix: '', label: 'AI Intelligence Reports Generated' },
  ];

  return (
    <section className="py-12 border-t border-b border-white/5 bg-[#0f1a2b]/50">
      <div className="max-w-5xl mx-auto px-6">
        <div className="flex flex-col sm:flex-row items-center justify-around gap-10">
          {stats.map((s, i) => (
            <Counter key={i} {...s} isLoading={isLoading && !data} />
          ))}
        </div>
      </div>
    </section>
  );
}
