import type { Metadata } from 'next';
import {
  Navbar,
  Hero,
  Ticker,
  Synthesis,
  SignalDescentCanvas,
  AskAnythingScene,
  LazyMount,
  Personas,
  Capabilities,
  FAQ,
  FinalCTA,
  Footer,
} from '@/components/landing';
import {
  faqSchema,
  organizationSchema,
  softwareApplicationSchema,
  websiteSchema,
} from '@/lib/landing/schema';
import { getLiveGraphData, getLiveBriefing, topCommunityNames } from '@/lib/landing/live';

export const metadata: Metadata = {
  alternates: { canonical: 'https://macrointel.net' },
};

// Section order/rhythm follows design.md D5: the three scenes are spread across the page
// (opening / center / close) rather than consecutive, each with deliberately contrasting
// vertical rhythm — see tasks.md 3.24.
export default async function LandingPage() {
  const [graph, briefing] = await Promise.all([getLiveGraphData(), getLiveBriefing()]);

  return (
    <>
      <Navbar />
      <main>
        <Hero graph={graph} />
        <Ticker storylines={graph.storylines} />

        {/* Scene 1 — SIGNAL DESCENT. Full-bleed, no section chrome by design (D5): this is
            the page's one deliberate break from the heading+grid pattern. `id="features"`
            carries over from the Pipeline.tsx section it replaces, so Navbar/Footer's
            existing "Features" links keep resolving without changes elsewhere. Mount is
            deferred until the section nears the viewport (3.22) so GSAP/ScrollTrigger and
            the pin-spacer it creates never touch the initial page load.

            The scene's closing act resolves to the same picture as the Hero's graph and
            labels it with the *real* community names from the same fetch — that recognition
            is the point of the whole sequence. With no backend the list is empty and the act
            renders unlabelled rather than inventing names. */}
        <section id="features" className="relative">
          <LazyMount placeholderClassName="h-screen w-full">
            <SignalDescentCanvas communityNames={topCommunityNames(graph)} />
          </LazyMount>
        </section>

        <Synthesis briefing={briefing} />

        {/* Scene 3 — ASK ANYTHING, closing argument before Personas/Capabilities/FAQ. */}
        <section id="oracle" className="py-32">
          <div className="mx-auto max-w-[1200px] px-10">
            <div className="mb-10 text-center">
              <div className="section-label justify-center">ORACLE AI</div>
              <h2 className="text-title font-extrabold tracking-[-0.02em]">
                Ask anything. Watch how it knows.
              </h2>
              <p className="mx-auto mt-3 max-w-xl text-sm text-fg-subtle">
                Nine tools, nine routing paths, one rule: every claim in the answer traces to a source.
              </p>
            </div>
            <LazyMount placeholderClassName="h-screen w-full">
              <AskAnythingScene />
            </LazyMount>
          </div>
        </section>

        <Personas />
        <Capabilities />
        <FAQ />
        <FinalCTA />
      </main>
      <Footer />
      <script
        id="ld-software-application"
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(softwareApplicationSchema) }}
      />
      <script
        id="ld-organization"
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(organizationSchema) }}
      />
      <script
        id="ld-website"
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(websiteSchema) }}
      />
      <script
        id="ld-faq"
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(faqSchema) }}
      />
    </>
  );
}
