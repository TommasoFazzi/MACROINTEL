import { MetadataRoute } from 'next';

export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: '*',
      allow: '/',
      disallow: ['/oracle'],
    },
    sitemap: 'https://macrointel.net/sitemap.xml',
  };
}
