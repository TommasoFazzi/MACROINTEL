import { NextRequest, NextResponse } from 'next/server';

const API_URL = process.env.INTELLIGENCE_API_URL || 'http://localhost:8000';
const API_KEY = process.env.INTELLIGENCE_API_KEY || '';

// Allowed API path prefixes for GET requests (first segment of the path)
const ALLOWED_GET_PREFIXES = ['dashboard', 'reports', 'stories', 'map'];

// Allowed API path prefixes for POST requests
const ALLOWED_POST_PREFIXES = ['oracle'];

function validatePath(pathStr: string, prefix: string): boolean {
  return !pathStr.includes('..') && !pathStr.startsWith('/') && pathStr.startsWith(prefix);
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  const { path } = await params;
  const pathStr = path.join('/');

  // Reject path traversal attempts
  if (pathStr.includes('..') || pathStr.startsWith('/')) {
    return NextResponse.json(
      { success: false, detail: 'Invalid path' },
      { status: 400 }
    );
  }

  // Validate against whitelist
  const prefix = path[0];
  if (!prefix || !ALLOWED_GET_PREFIXES.includes(prefix)) {
    return NextResponse.json(
      { success: false, detail: 'Not found' },
      { status: 404 }
    );
  }

  const searchParams = request.nextUrl.searchParams.toString();
  const upstream = `${API_URL}/api/v1/${pathStr}${searchParams ? `?${searchParams}` : ''}`;

  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 300000); // 300s timeout

    const response = await fetch(upstream, {
      headers: {
        'Content-Type': 'application/json',
        ...(API_KEY && { 'X-API-Key': API_KEY }),
      },
      signal: controller.signal,
    });

    clearTimeout(timeoutId);

    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') {
      return NextResponse.json(
        { success: false, detail: 'Backend request timeout' },
        { status: 504 }
      );
    }
    return NextResponse.json(
      { success: false, detail: 'Backend unavailable' },
      { status: 502 }
    );
  }
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  const { path } = await params;
  const pathStr = path.join('/');

  // Reject path traversal attempts
  if (pathStr.includes('..') || pathStr.startsWith('/')) {
    return NextResponse.json(
      { success: false, detail: 'Invalid path' },
      { status: 400 }
    );
  }

  // Validate against POST whitelist
  const prefix = path[0];
  if (!prefix || !ALLOWED_POST_PREFIXES.includes(prefix)) {
    return NextResponse.json(
      { success: false, detail: 'Not found' },
      { status: 404 }
    );
  }

  const upstream = `${API_URL}/api/v1/${pathStr}`;

  try {
    const body = await request.text();

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 300000); // 300s timeout

    const response = await fetch(upstream, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(API_KEY && { 'X-API-Key': API_KEY }),
      },
      body,
      signal: controller.signal,
    });

    clearTimeout(timeoutId);

    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') {
      return NextResponse.json(
        { success: false, detail: 'Backend request timeout' },
        { status: 504 }
      );
    }
    return NextResponse.json(
      { success: false, detail: 'Backend unavailable' },
      { status: 502 }
    );
  }
}
