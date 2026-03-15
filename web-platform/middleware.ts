import { jwtVerify } from 'jose';
import { NextRequest, NextResponse } from 'next/server';

// Routes that require a valid access token
const PROTECTED_PREFIXES = ['/dashboard', '/map', '/stories', '/oracle'];

const COOKIE_NAME = 'macrointel_access';

function getSecret(): Uint8Array {
  const secret = process.env.JWT_SECRET;
  if (!secret) {
    // If no secret is set, deny access to protected routes
    return new TextEncoder().encode('__no_secret_configured__');
  }
  return new TextEncoder().encode(secret);
}

export async function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;

  const isProtected = PROTECTED_PREFIXES.some((prefix) =>
    pathname.startsWith(prefix)
  );

  if (!isProtected) {
    return NextResponse.next();
  }

  const token = req.cookies.get(COOKIE_NAME)?.value;

  if (!token) {
    const url = req.nextUrl.clone();
    url.pathname = '/access';
    url.searchParams.set('from', pathname);
    return NextResponse.redirect(url);
  }

  try {
    await jwtVerify(token, getSecret());
    return NextResponse.next();
  } catch {
    // Token invalid or expired — redirect to access page
    const url = req.nextUrl.clone();
    url.pathname = '/access';
    url.searchParams.set('from', pathname);
    const res = NextResponse.redirect(url);
    // Clear the invalid cookie
    res.cookies.delete(COOKIE_NAME);
    return res;
  }
}

export const config = {
  matcher: [
    '/dashboard/:path*',
    '/map/:path*',
    '/stories/:path*',
    '/oracle/:path*',
  ],
};
