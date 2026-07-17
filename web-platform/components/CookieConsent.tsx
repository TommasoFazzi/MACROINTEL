'use client';

import { useEffect, useState } from 'react';

const STORAGE_KEY = 'mi_cookie_consent';

type ConsentChoice = 'granted' | 'denied';

declare global {
  interface Window {
    dataLayer: unknown[];
    gtag: (...args: unknown[]) => void;
  }
}

function pushConsentUpdate(choice: ConsentChoice) {
  window.dataLayer = window.dataLayer || [];
  window.gtag = window.gtag || function gtag() { window.dataLayer.push(arguments); };
  window.gtag('consent', 'update', {
    analytics_storage: choice,
    ad_storage: choice,
    ad_user_data: choice,
    ad_personalization: choice,
  });
}

export default function CookieConsent() {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'granted' || stored === 'denied') {
      pushConsentUpdate(stored);
    } else {
      setVisible(true);
    }

    const openManager = () => setVisible(true);
    window.addEventListener('open-cookie-preferences', openManager);
    return () => window.removeEventListener('open-cookie-preferences', openManager);
  }, []);

  function choose(choice: ConsentChoice) {
    localStorage.setItem(STORAGE_KEY, choice);
    pushConsentUpdate(choice);
    setVisible(false);
  }

  if (!visible) return null;

  return (
    <div className="fixed inset-x-0 bottom-0 z-[100] p-4 sm:p-6" role="dialog" aria-live="polite" aria-label="Cookie consent">
      <div className="glass mx-auto flex max-w-3xl flex-col gap-4 rounded-xl border border-white/10 p-5 shadow-2xl sm:flex-row sm:items-center sm:gap-6 sm:p-6">
        <p className="flex-1 text-sm leading-relaxed text-muted-foreground">
          We use cookies to measure site usage via Google Analytics. Analytics cookies are only set if you accept.
        </p>
        <div className="flex shrink-0 gap-2">
          <button
            onClick={() => choose('denied')}
            className="btn-ghost"
            type="button"
          >
            Reject
          </button>
          <button
            onClick={() => choose('granted')}
            className="btn-primary orange-glow"
            type="button"
          >
            Accept
          </button>
        </div>
      </div>
    </div>
  );
}
