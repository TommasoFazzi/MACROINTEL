import { NextRequest, NextResponse } from 'next/server';

export function middleware(_req: NextRequest) {
  const nonce = Buffer.from(crypto.getRandomValues(new Uint8Array(16))).toString('base64');

  const csp = [
    "default-src 'self'",
    // unsafe-eval required by mapbox-gl (WebGL shader compilation) and sigma/graphology-layout-forceatlas2
    `script-src 'self' 'nonce-${nonce}' 'unsafe-eval' blob: *.mapbox.com *.googletagmanager.com`,
    "style-src 'self' 'unsafe-inline' *.mapbox.com",
    "img-src 'self' data: blob: *.mapbox.com *.google-analytics.com",
    "connect-src 'self' *.mapbox.com api.mapbox.com events.mapbox.com *.google-analytics.com *.analytics.google.com *.googletagmanager.com",
    "worker-src blob:",
    "font-src 'self' data:",
  ].join('; ');

  const response = NextResponse.next();
  response.headers.set('x-nonce', nonce);
  response.headers.set('Content-Security-Policy', csp);
  return response;
}

export const config = {
  matcher: [
    // Apply to all routes except Next.js static assets
    '/((?!_next/static|_next/image|favicon.ico|manifest.json|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico|woff2?|ttf)$).*)',
  ],
};
