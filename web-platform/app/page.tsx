import { Navbar, Hero, StatsCounter, ICPSection, ProductShowcase, Features, CTASection, Footer } from '@/components/landing';

export default function LandingPage() {
  return (
    <>
      <Navbar />
      <main>
        <Hero />
        <StatsCounter />
        <ICPSection />
        <ProductShowcase />
        <Features />
        <CTASection />
      </main>
      <Footer />
    </>
  );
}
