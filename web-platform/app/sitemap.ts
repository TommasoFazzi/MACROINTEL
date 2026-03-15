import { MetadataRoute } from 'next';

const BASE_URL = 'https://macrointel.net';
const API_URL = process.env.INTELLIGENCE_API_URL || 'http://localhost:8000';

interface InsightSlug {
  slug: string;
  published_at: string | null;
}

async function getPublicInsightSlugs(): Promise<InsightSlug[]> {
  try {
    const res = await fetch(`${API_URL}/api/v1/insights?limit=50`, {
      next: { revalidate: 3600 },
    });
    if (!res.ok) return [];
    const data = await res.json();
    return (data.insights ?? []).map((i: InsightSlug) => ({
      slug: i.slug,
      published_at: i.published_at,
    }));
  } catch {
    return [];
  }
}

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const staticRoutes: MetadataRoute.Sitemap = [
    {
      url: BASE_URL,
      lastModified: new Date(),
      changeFrequency: 'daily',
      priority: 1,
    },
    {
      url: `${BASE_URL}/insights`,
      lastModified: new Date(),
      changeFrequency: 'daily',
      priority: 0.9,
    },
    {
      url: `${BASE_URL}/dashboard`,
      lastModified: new Date(),
      changeFrequency: 'daily',
      priority: 0.8,
    },
    {
      url: `${BASE_URL}/stories`,
      lastModified: new Date(),
      changeFrequency: 'daily',
      priority: 0.8,
    },
    {
      url: `${BASE_URL}/map`,
      lastModified: new Date(),
      changeFrequency: 'weekly',
      priority: 0.7,
    },
  ];

  const slugs = await getPublicInsightSlugs();
  const insightRoutes: MetadataRoute.Sitemap = slugs.map((s) => ({
    url: `${BASE_URL}/insights/${s.slug}`,
    lastModified: s.published_at ? new Date(s.published_at) : new Date(),
    changeFrequency: 'monthly', // Evergreen content — accumulates authority
    priority: 0.8,
  }));

  return [...staticRoutes, ...insightRoutes];
}
