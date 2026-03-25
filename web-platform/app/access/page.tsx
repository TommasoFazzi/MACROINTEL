'use client';

import { useState, Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { Loader2, KeyRound, AlertCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import Link from 'next/link';

function AccessForm() {
  const params = useSearchParams();
  const from = params.get('from') || '/dashboard';

  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!code.trim()) return;

    setLoading(true);
    setError('');

    try {
      const res = await fetch('/api/access/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: code.trim() }),
      });

      if (res.ok) {
        window.location.href = from;
      } else {
        const data = await res.json().catch(() => ({}));
        setError(data.error || 'Invalid access code. Please try again.');
      }
    } catch {
      setError('Cannot connect to server. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#0A1628] flex items-center justify-center px-4">
      {/* Grid overlay */}
      <div className="fixed inset-0 grid-overlay opacity-20 pointer-events-none" />

      {/* Glow orb — scaled for mobile */}
      <div
        className="fixed w-[200px] h-[200px] sm:w-[400px] sm:h-[400px] rounded-full blur-[80px] sm:blur-[100px] opacity-15 top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 pointer-events-none"
        style={{ background: 'radial-gradient(circle, #FF6B35 0%, transparent 70%)' }}
      />

      <div className="relative z-10 w-full max-w-[320px] sm:max-w-sm">
        {/* Card */}
        <div className="bg-[#1a2332]/80 border border-white/10 rounded-2xl p-6 sm:p-8 shadow-2xl backdrop-blur-sm">
          {/* Logo */}
          <Link href="/" className="flex items-center justify-center gap-2 mb-8">
            <svg viewBox="0 0 40 40" fill="none" className="w-9 h-9">
              <circle cx="20" cy="20" r="18" stroke="#FF6B35" strokeWidth="2" />
              <circle cx="20" cy="20" r="12" stroke="#00A8E8" strokeWidth="1.5" />
              <circle cx="20" cy="20" r="6" stroke="#FF6B35" strokeWidth="1.5" />
              <circle cx="20" cy="20" r="2" fill="#FF6B35" />
              <line x1="20" y1="2" x2="20" y2="10" stroke="#00A8E8" strokeWidth="1" />
              <line x1="20" y1="30" x2="20" y2="38" stroke="#00A8E8" strokeWidth="1" />
              <line x1="2" y1="20" x2="10" y2="20" stroke="#00A8E8" strokeWidth="1" />
              <line x1="30" y1="20" x2="38" y2="20" stroke="#00A8E8" strokeWidth="1" />
            </svg>
            <span className="text-xl font-bold tracking-tight">
              <span className="text-[#FF6B35]">MACRO</span>
              <span className="text-white">INTEL</span>
            </span>
          </Link>

          {/* Icon */}
          <div className="flex justify-center mb-5">
            <div className="w-14 h-14 rounded-full bg-[#FF6B35]/10 border border-[#FF6B35]/20 flex items-center justify-center">
              <KeyRound className="w-6 h-6 text-[#FF6B35]" />
            </div>
          </div>

          <h1 className="text-xl font-bold text-white text-center mb-1">
            Enter Access Code
          </h1>
          <p className="text-gray-500 text-sm text-center mb-7">
            MACROINTEL is currently invite-only.
          </p>

          <form onSubmit={handleSubmit} className="space-y-4">
            <input
              type="text"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="XXXX-XXXX-XXXX"
              autoFocus
              className="w-full px-4 py-3 rounded-lg bg-white/5 border border-white/15 text-white placeholder-gray-600 focus:outline-none focus:border-[#FF6B35]/60 transition-colors text-sm text-center tracking-widest font-mono"
            />

            {error && (
              <div className="flex items-center gap-2 text-red-400 text-sm">
                <AlertCircle className="w-4 h-4 shrink-0" />
                <span>{error}</span>
              </div>
            )}

            <Button
              type="submit"
              disabled={loading || !code.trim()}
              className="w-full bg-[#FF6B35] hover:bg-[#F77F00] text-white"
            >
              {loading ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                'Enter'
              )}
            </Button>
          </form>

          <p className="text-center mt-5 text-sm text-gray-500">
            Don&apos;t have a code?{' '}
            <Link
              href="/#waitlist"
              className="text-[#FF6B35] hover:text-[#F77F00] transition-colors underline"
            >
              Join the waitlist →
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}

export default function AccessPage() {
  return (
    <Suspense>
      <AccessForm />
    </Suspense>
  );
}
