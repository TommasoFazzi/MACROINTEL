'use client';

import Link from 'next/link';

export default function Footer() {
  return (
    <footer id="contact" className="py-16 border-t border-white/5">
      <div className="max-w-7xl mx-auto px-6">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-12 mb-12">
          {/* Brand */}
          <div className="md:col-span-2">
            <Link href="/" className="flex items-center gap-3 mb-4">
              <div className="relative w-10 h-10">
                <svg viewBox="0 0 40 40" fill="none" className="w-full h-full">
                  <circle cx="20" cy="20" r="18" stroke="#FF6B35" strokeWidth="2" />
                  <circle cx="20" cy="20" r="12" stroke="#00A8E8" strokeWidth="1.5" />
                  <circle cx="20" cy="20" r="6" stroke="#FF6B35" strokeWidth="1.5" />
                  <circle cx="20" cy="20" r="2" fill="#FF6B35" />
                  <line x1="20" y1="2" x2="20" y2="10" stroke="#00A8E8" strokeWidth="1" />
                  <line x1="20" y1="30" x2="20" y2="38" stroke="#00A8E8" strokeWidth="1" />
                  <line x1="2" y1="20" x2="10" y2="20" stroke="#00A8E8" strokeWidth="1" />
                  <line x1="30" y1="20" x2="38" y2="20" stroke="#00A8E8" strokeWidth="1" />
                </svg>
              </div>
              <span className="text-xl font-bold tracking-tight">
                <span className="text-[#FF6B35]">MACRO</span>
                <span className="text-white">INTEL</span>
              </span>
            </Link>
            <p className="text-gray-400 max-w-sm">
              AI-powered OSINT platform monitoring geopolitical risks, cyber threats,
              and macro-economic signals — 33+ sources processed daily into actionable intelligence.
            </p>
          </div>

          {/* Platform Links */}
          <div>
            <h4 className="text-white font-semibold mb-4">Platform</h4>
            <ul className="space-y-3">
              <li>
                <Link
                  href="/dashboard"
                  className="text-gray-400 hover:text-[#FF6B35] transition-colors text-sm"
                >
                  Dashboard
                </Link>
              </li>
              <li>
                <Link
                  href="/stories"
                  className="text-gray-400 hover:text-[#FF6B35] transition-colors text-sm"
                >
                  Narrative Graph
                </Link>
              </li>
              <li>
                <Link
                  href="/map"
                  className="text-gray-400 hover:text-[#FF6B35] transition-colors text-sm"
                >
                  Intelligence Map
                </Link>
              </li>
              <li>
                <Link
                  href="/oracle"
                  className="text-gray-400 hover:text-[#FF6B35] transition-colors text-sm"
                >
                  Oracle AI
                </Link>
              </li>
            </ul>
          </div>

          {/* Resources Links */}
          <div>
            <h4 className="text-white font-semibold mb-4">Resources</h4>
            <ul className="space-y-3">
              <li>
                <Link
                  href="/insights"
                  className="text-gray-400 hover:text-[#FF6B35] transition-colors text-sm"
                >
                  Intelligence Briefings
                </Link>
              </li>
              <li>
                <button
                  type="button"
                  onClick={() => document.getElementById('features')?.scrollIntoView({ behavior: 'smooth' })}
                  className="text-gray-400 hover:text-[#FF6B35] transition-colors text-sm"
                >
                  Features
                </button>
              </li>
              <li>
                <button
                  type="button"
                  onClick={() => document.getElementById('about')?.scrollIntoView({ behavior: 'smooth' })}
                  className="text-gray-400 hover:text-[#FF6B35] transition-colors text-sm"
                >
                  About
                </button>
              </li>
            </ul>
          </div>
        </div>

        {/* Bottom Bar */}
        <div className="pt-8 border-t border-white/5 flex flex-col md:flex-row justify-between items-center gap-4">
          <p className="text-gray-500 text-sm">
            &copy; {new Date().getFullYear()} MACROINTEL. All rights reserved.
          </p>
          <p className="text-gray-600 text-xs">
            Powered by Next.js, Gemini AI, and pgvector
          </p>
        </div>
      </div>
    </footer>
  );
}
