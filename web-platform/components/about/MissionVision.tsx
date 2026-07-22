import { SOURCE_COUNT } from '@/lib/constants';

export default function MissionVision() {
  return (
    <section className="grid-bg" style={{ padding: '80px 40px', maxWidth: 1100, margin: '0 auto' }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 24 }}>
        <article className="card" style={{ position: 'relative', overflow: 'hidden', padding: 32 }}>
          <div
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              width: 3,
              height: '100%',
              background: '#FF6B35',
              borderRadius: '0 2px 2px 0',
            }}
          />
          <div className="section-label" style={{ marginLeft: 12 }}>MISSION</div>
          <h2
            style={{
              fontSize: 26,
              fontWeight: 800,
              letterSpacing: '-0.02em',
              marginBottom: 16,
              marginLeft: 12,
              lineHeight: 1.2,
            }}
          >
            Make strategic intelligence accessible to everyone who needs it.
          </h2>
          <p style={{ fontSize: 14, color: '#94a3b8', lineHeight: 1.75, marginLeft: 12 }}>
            The gap between raw information and strategic understanding is where decisions go wrong. MACROINTEL exists to close that gap — processing signals from {SOURCE_COUNT} sources and turning them into structured, actionable intelligence that anyone can use, not just those with dedicated research teams.
          </p>
        </article>

        <article className="card" style={{ position: 'relative', overflow: 'hidden', padding: 32 }}>
          <div
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              width: 3,
              height: '100%',
              background: '#00A8E8',
              borderRadius: '0 2px 2px 0',
            }}
          />
          <div
            className="section-label"
            style={{ marginLeft: 12, color: '#00A8E8' }}
          >
            VISION
          </div>
          <h2
            style={{
              fontSize: 26,
              fontWeight: 800,
              letterSpacing: '-0.02em',
              marginBottom: 16,
              marginLeft: 12,
              lineHeight: 1.2,
            }}
          >
            An intelligence layer for the world&apos;s most complex decisions.
          </h2>
          <p style={{ fontSize: 14, color: '#94a3b8', lineHeight: 1.75, marginLeft: 12 }}>
            We believe the next generation of geopolitical and economic analysis will be AI-native — not AI-assisted. MACROINTEL is building the infrastructure for that future: a continuously updated knowledge graph of the world&apos;s strategic landscape, queryable in natural language, navigable visually, and actionable immediately.
          </p>
        </article>
      </div>
    </section>
  );
}
