'use client';

import dynamic from 'next/dynamic';
import Link from 'next/link';
import { Button } from '@/components/ui/button';
import { ArrowRight } from 'lucide-react';

// Dynamic imports to prevent SSR hydration issues with canvas
const ParticleCanvas = dynamic(() => import('./ParticleCanvas'), { ssr: false });
const LiquidGradient = dynamic(() => import('./LiquidGradient'), { ssr: false });

function StatItem({ value, label }: { value: string; label: string }) {
  return (
    <div className="text-center">
      <div className="text-3xl font-extrabold text-[#FF6B35] mb-1">{value}</div>
      <div className="text-sm text-gray-400 uppercase tracking-wider">{label}</div>
    </div>
  );
}

export default function Hero() {
  return (
    <section className="relative min-h-screen flex items-center pt-20 overflow-hidden">
      {/* Background Effects */}
      <div className="absolute inset-0 z-0">
        {/* Grid overlay */}
        <div className="absolute inset-0 grid-overlay opacity-50">
          {/* Scanline effect */}
          <div
            className="absolute inset-0 animate-scanline pointer-events-none"
            style={{
              background: 'linear-gradient(transparent 50%, rgba(255, 107, 53, 0.02) 50%)',
              backgroundSize: '100% 4px',
            }}
          />
        </div>

        {/* Glow orbs */}
        <div
          className="absolute w-[500px] h-[500px] rounded-full blur-[80px] opacity-30 animate-float top-[10%] right-[10%]"
          style={{
            background: 'radial-gradient(circle, #FF6B35 0%, transparent 70%)',
          }}
        />
        <div
          className="absolute w-[400px] h-[400px] rounded-full blur-[80px] opacity-30 animate-float-delayed bottom-[20%] left-[10%]"
          style={{
            background: 'radial-gradient(circle, #00A8E8 0%, transparent 70%)',
          }}
        />
      </div>

      {/* Canvas-based effects (client-only) */}
      <ParticleCanvas />
      <LiquidGradient />

      {/* Content */}
      <div className="container max-w-7xl mx-auto px-6 relative z-10">
        <div className="max-w-3xl mx-auto text-center">
          {/* Badge */}
          <div className="inline-flex items-center gap-2 px-4 py-2 bg-white/5 border border-white/10 rounded-full text-sm text-gray-300 mb-6 animate-fadeInUp">
            <span className="w-2 h-2 bg-[#FF6B35] rounded-full animate-pulse-custom" />
            <span>Real-time Intelligence Platform</span>
          </div>

          {/* Title */}
          <h1 className="text-5xl md:text-6xl lg:text-7xl font-extrabold leading-tight mb-6 tracking-tight animate-fadeInUp animation-delay-100">
            Global Intelligence
            <span className="block gradient-text">
              In Real Time
            </span>
          </h1>

          {/* Description */}
          <p className="text-xl text-gray-400 mb-8 leading-relaxed animate-fadeInUp animation-delay-200">
            Geopolitical analysis, cybersecurity monitoring, and macro-economic trends
            powered by AI. Thousands of sources distilled into actionable intelligence.
          </p>

          {/* CTAs */}
          <div className="flex flex-col sm:flex-row gap-4 justify-center mb-12 animate-fadeInUp animation-delay-300">
            <Button size="lg" className="group bg-[#FF6B35] hover:bg-[#F77F00] text-white" asChild>
              <a href="#waitlist">
                Get Access
                <ArrowRight className="ml-2 h-5 w-5 transition-transform group-hover:translate-x-1" />
              </a>
            </Button>
            <Button variant="outline" size="lg" asChild className="border-white/20 hover:bg-white/5 group">
              <a href="#product-showcase">
                See How It Works
                <ArrowRight className="ml-2 h-5 w-5 opacity-0 group-hover:opacity-100 transition-all group-hover:translate-x-1" />
              </a>
            </Button>
          </div>

          {/* Stats */}
          <div className="flex flex-col sm:flex-row items-center justify-center gap-8 p-8 glass-light rounded-xl animate-fadeInUp animation-delay-400">
            <StatItem value="33+" label="RSS Sources" />
            <div className="hidden sm:block w-px h-10 bg-white/10" />
            <StatItem value="24/7" label="Monitoring" />
            <div className="hidden sm:block w-px h-10 bg-white/10" />
            <StatItem value="AI" label="Powered" />
          </div>
        </div>
      </div>

      {/* Scroll indicator */}
      <div className="absolute bottom-8 left-1/2 -translate-x-1/2 animate-bounce">
        <div className="w-6 h-10 border-2 border-white/20 rounded-full flex justify-center pt-2">
          <div className="w-1 h-3 bg-[#FF6B35] rounded-full animate-pulse" />
        </div>
      </div>
    </section>
  );
}
