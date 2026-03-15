'use client';

import { useState } from 'react';
import { ArrowRight, CheckCircle, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';

type FormState = 'idle' | 'loading' | 'success' | 'error';

export default function CTASection() {
  const [email, setEmail] = useState('');
  const [role, setRole] = useState('');
  const [honeypot, setHoneypot] = useState('');
  const [state, setState] = useState<FormState>('idle');
  const [errorMsg, setErrorMsg] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email) return;
    // Honeypot: silently drop bot submissions
    if (honeypot) return;

    setState('loading');
    setErrorMsg('');

    try {
      const res = await fetch('/api/proxy/waitlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, role: role || undefined }),
      });

      if (res.ok) {
        setState('success');
        setEmail('');
        setRole('');
      } else if (res.status === 409) {
        setErrorMsg("You're already on the list — we'll be in touch soon.");
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

  return (
    <section id="about" className="py-24 relative">
      <div className="max-w-4xl mx-auto px-6">
        <div className="relative p-12 rounded-2xl overflow-hidden">
          {/* Gradient border */}
          <div className="absolute inset-0 bg-gradient-to-br from-[#FF6B35]/20 via-transparent to-[#00A8E8]/20 rounded-2xl" />
          <div className="absolute inset-[1px] bg-[#0A1628] rounded-2xl" />

          <div className="relative z-10">
            {/* Waitlist form — ID #waitlist for navbar anchor scroll */}
            <div id="waitlist" className="text-center">
              <h2 className="text-3xl md:text-4xl font-extrabold mb-3 text-white">
                Ready to get access?
              </h2>
              <p className="text-lg text-gray-400 mb-8 max-w-xl mx-auto">
                MACROINTEL is currently invite-only. Join the waitlist — we&apos;ll send you an
                access code within 24 hours.
              </p>

              {state === 'success' ? (
                <div className="flex flex-col items-center gap-3 text-emerald-400">
                  <CheckCircle className="w-10 h-10" />
                  <p className="text-lg font-medium">You&apos;re on the list.</p>
                  <p className="text-gray-400 text-sm">
                    Check your inbox — your access code is on its way.
                  </p>
                </div>
              ) : (
                <form
                  onSubmit={handleSubmit}
                  className="flex flex-col sm:flex-row gap-3 max-w-xl mx-auto"
                >
                  {/* Honeypot — hidden from humans, filled by bots */}
                  <input
                    type="text"
                    name="website"
                    value={honeypot}
                    onChange={(e) => setHoneypot(e.target.value)}
                    tabIndex={-1}
                    autoComplete="off"
                    aria-hidden="true"
                    className="absolute -left-[9999px] w-px h-px overflow-hidden"
                  />
                  <input
                    type="email"
                    required
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="your@email.com"
                    className="flex-1 px-4 py-3 rounded-lg bg-white/5 border border-white/15 text-white placeholder-gray-500 focus:outline-none focus:border-[#FF6B35]/60 transition-colors text-sm"
                  />
                  <select
                    value={role}
                    onChange={(e) => setRole(e.target.value)}
                    aria-label="Your role"
                    className="px-4 py-3 rounded-lg bg-white/5 border border-white/15 text-gray-400 focus:outline-none focus:border-[#FF6B35]/60 transition-colors text-sm sm:w-44"
                  >
                    <option value="">My role…</option>
                    <option value="analyst">Geopolitical Analyst</option>
                    <option value="security">CISO / Security</option>
                    <option value="finance">Finance / Fund Manager</option>
                    <option value="journalist">Journalist</option>
                    <option value="other">Other</option>
                  </select>
                  <Button
                    type="submit"
                    disabled={state === 'loading'}
                    className="bg-[#FF6B35] hover:bg-[#F77F00] text-white px-6 group shrink-0"
                  >
                    {state === 'loading' ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <>
                        Request Access
                        <ArrowRight className="ml-2 h-4 w-4 transition-transform group-hover:translate-x-1" />
                      </>
                    )}
                  </Button>
                </form>
              )}

              {state === 'error' && (
                <p className="text-red-400 text-sm mt-3">{errorMsg}</p>
              )}

              <p className="text-gray-600 text-xs mt-4">
                Free. No credit card required. We&apos;ll only send you your access code.
              </p>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
