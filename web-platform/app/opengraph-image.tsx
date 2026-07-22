import { ImageResponse } from 'next/og';
import { SOURCE_COUNT } from '@/lib/constants';

export const runtime = 'edge';

export const size = {
  width: 1200,
  height: 630,
};

export const contentType = 'image/png';

export default function OgImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          background: 'linear-gradient(135deg, #0A1628 0%, #1a2332 50%, #0A1628 100%)',
          fontFamily: 'sans-serif',
        }}
      >
        {/* Grid pattern overlay */}
        <div
          style={{
            position: 'absolute',
            inset: 0,
            backgroundImage:
              'linear-gradient(rgba(255,255,255,0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px)',
            backgroundSize: '60px 60px',
          }}
        />

        {/* Orange glow */}
        <div
          style={{
            position: 'absolute',
            top: '10%',
            right: '15%',
            width: '300px',
            height: '300px',
            borderRadius: '50%',
            background: 'radial-gradient(circle, rgba(255,107,53,0.25) 0%, transparent 70%)',
            filter: 'blur(60px)',
          }}
        />

        {/* Blue glow */}
        <div
          style={{
            position: 'absolute',
            bottom: '15%',
            left: '10%',
            width: '250px',
            height: '250px',
            borderRadius: '50%',
            background: 'radial-gradient(circle, rgba(0,168,232,0.2) 0%, transparent 70%)',
            filter: 'blur(60px)',
          }}
        />

        {/* Logo circles */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            marginBottom: '30px',
          }}
        >
          <svg width="80" height="80" viewBox="0 0 40 40" fill="none">
            <circle cx="20" cy="20" r="18" stroke="#FF6B35" strokeWidth="2" />
            <circle cx="20" cy="20" r="12" stroke="#00A8E8" strokeWidth="1.5" />
            <circle cx="20" cy="20" r="6" stroke="#FF6B35" strokeWidth="1.5" />
            <circle cx="20" cy="20" r="2" fill="#FF6B35" />
          </svg>
        </div>

        {/* Title */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '12px',
            marginBottom: '16px',
          }}
        >
          <span
            style={{
              fontSize: '64px',
              fontWeight: 800,
              color: '#FF6B35',
              letterSpacing: '-2px',
            }}
          >
            INTEL
          </span>
          <span
            style={{
              fontSize: '64px',
              fontWeight: 800,
              color: '#ffffff',
              letterSpacing: '-2px',
            }}
          >
            ITA
          </span>
        </div>

        {/* Subtitle */}
        <div
          style={{
            fontSize: '24px',
            color: '#94a3b8',
            textAlign: 'center',
            maxWidth: '700px',
            lineHeight: 1.4,
          }}
        >
          AI-Powered Geopolitical Intelligence Platform
        </div>

        {/* Stats bar */}
        <div
          style={{
            display: 'flex',
            gap: '40px',
            marginTop: '40px',
            padding: '16px 32px',
            borderRadius: '12px',
            background: 'rgba(255,255,255,0.05)',
            border: '1px solid rgba(255,255,255,0.1)',
          }}
        >
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
            <span style={{ fontSize: '28px', fontWeight: 800, color: '#FF6B35' }}>{SOURCE_COUNT}</span>
            <span style={{ fontSize: '12px', color: '#64748b', textTransform: 'uppercase', letterSpacing: '2px' }}>Sources</span>
          </div>
          <div style={{ width: '1px', background: 'rgba(255,255,255,0.1)' }} />
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
            <span style={{ fontSize: '28px', fontWeight: 800, color: '#00A8E8' }}>24/7</span>
            <span style={{ fontSize: '12px', color: '#64748b', textTransform: 'uppercase', letterSpacing: '2px' }}>Monitoring</span>
          </div>
          <div style={{ width: '1px', background: 'rgba(255,255,255,0.1)' }} />
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
            <span style={{ fontSize: '28px', fontWeight: 800, color: '#FF6B35' }}>RAG</span>
            <span style={{ fontSize: '12px', color: '#64748b', textTransform: 'uppercase', letterSpacing: '2px' }}>AI-Powered</span>
          </div>
        </div>

        {/* Bottom URL */}
        <div
          style={{
            position: 'absolute',
            bottom: '24px',
            fontSize: '14px',
            color: '#475569',
            letterSpacing: '2px',
            fontFamily: 'monospace',
          }}
        >
          macrointel.net
        </div>
      </div>
    ),
    {
      ...size,
    }
  );
}
