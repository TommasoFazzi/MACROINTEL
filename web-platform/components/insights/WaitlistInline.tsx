'use client';

import { useState } from 'react';
import { ArrowRight, CheckCircle, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';

type FormState = 'idle' | 'loading' | 'success' | 'error';

export default function WaitlistInline() {
  const [email, setEmail] = useState('');
  const [state, setState] = useState<FormState>('idle');
  const [errorMsg, setErrorMsg] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email) return;

    setState('loading');
    setErrorMsg('');

    try {
      const res = await fetch('/api/proxy/waitlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      });

      if (res.ok) {
        setState('success');
        setEmail('');
      } else if (res.status === 409) {
        setErrorMsg("You're already on the list — check your inbox.");
        setState('error');
      } else {
        setErrorMsg('Something went wrong. Please try again.');
        setState('error');
      }
    } catch {
      setErrorMsg('Cannot reach server. Please try again later.');
      setState('error');
    }
  };

  if (state === 'success') {
    return (
      <div className="flex items-center gap-3 text-emerald-400">
        <CheckCircle className="w-5 h-5 shrink-0" />
        <div>
          <p className="font-medium text-sm">You&apos;re on the list.</p>
          <p className="text-gray-500 text-xs mt-0.5">Your access code is on its way.</p>
        </div>
      </div>
    );
  }

  return (
    <div>
      <form onSubmit={handleSubmit} className="flex flex-col sm:flex-row gap-2">
        <input
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="your@email.com"
          className="flex-1 px-4 py-2.5 rounded-lg bg-white/5 border border-white/15 text-white placeholder-gray-600 focus:outline-none focus:border-[#FF6B35]/60 transition-colors text-sm"
        />
        <Button
          type="submit"
          disabled={state === 'loading'}
          className="bg-[#FF6B35] hover:bg-[#F77F00] text-white group shrink-0"
        >
          {state === 'loading' ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <>
              Get Access
              <ArrowRight className="ml-2 h-4 w-4 transition-transform group-hover:translate-x-1" />
            </>
          )}
        </Button>
      </form>
      {state === 'error' && (
        <p className="text-red-400 text-xs mt-2">{errorMsg}</p>
      )}
    </div>
  );
}
