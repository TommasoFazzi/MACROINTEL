import { SignJWT } from 'jose';
import crypto from 'crypto';
import { NextRequest, NextResponse } from 'next/server';

const COOKIE_NAME = 'macrointel_access';
const COOKIE_MAX_AGE = 60 * 60 * 24 * 30; // 30 days in seconds

function getSecret(): Uint8Array {
  const secret = process.env.JWT_SECRET;
  if (!secret) throw new Error('JWT_SECRET env var is not set');
  return new TextEncoder().encode(secret);
}

function getValidCodes(): string[] {
  const raw = process.env.ACCESS_CODES ?? '';
  return raw
    .split(',')
    .map((c) => c.trim())
    .filter(Boolean);
}

function timingSafeCompare(a: string, b: string): boolean {
  // Pad to same length to avoid length-based timing leaks
  const maxLen = Math.max(a.length, b.length);
  const bufA = Buffer.alloc(maxLen);
  const bufB = Buffer.alloc(maxLen);
  bufA.write(a);
  bufB.write(b);
  return crypto.timingSafeEqual(bufA, bufB);
}

export async function POST(req: NextRequest) {
  let body: { code?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: 'Invalid request body' }, { status: 400 });
  }

  const code = (body.code ?? '').trim();
  if (!code) {
    return NextResponse.json({ error: 'Access code is required' }, { status: 400 });
  }

  const validCodes = getValidCodes();
  if (validCodes.length === 0) {
    // No codes configured — deny access
    return NextResponse.json({ error: 'Access codes not configured' }, { status: 503 });
  }

  const isValid = validCodes.some((validCode) =>
    timingSafeCompare(code, validCode)
  );

  if (!isValid) {
    return NextResponse.json({ error: 'Invalid access code' }, { status: 401 });
  }

  // Sign a JWT valid for 30 days
  const secret = getSecret();
  const token = await new SignJWT({ sub: 'macrointel-access' })
    .setProtectedHeader({ alg: 'HS256' })
    .setIssuedAt()
    .setExpirationTime('30d')
    .sign(secret);

  const res = NextResponse.json({ status: 'ok' });
  res.cookies.set(COOKIE_NAME, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'strict',
    maxAge: COOKIE_MAX_AGE,
    path: '/',
  });

  return res;
}
