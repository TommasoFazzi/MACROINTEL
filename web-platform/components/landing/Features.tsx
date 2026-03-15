'use client';

import { useEffect, useRef } from 'react';
import FeatureCard from './FeatureCard';
import { Clock, PieChart, Layers, MapPin, MessageSquare, Download } from 'lucide-react';

const features = [
  {
    icon: <Clock className="w-8 h-8" />,
    title: 'Daily Intelligence Briefs',
    description:
      'Automated daily and weekly reports — geopolitical developments, cybersecurity incidents, and macro signals distilled while you sleep.',
  },
  {
    icon: <PieChart className="w-8 h-8" />,
    title: '3-Layer Signal Filtering',
    description:
      'Irrelevant noise eliminated at ingestion, classification, and clustering stages. Only what matters reaches your desk.',
  },
  {
    icon: <Layers className="w-8 h-8" />,
    title: 'Grounded AI Answers',
    description:
      'Every answer cites real sources from our knowledge base. No hallucinations, full traceability back to the original article.',
  },
  {
    icon: <MapPin className="w-8 h-8" />,
    title: 'Geospatial Intelligence Map',
    description:
      'See where events are happening. Entities plotted on an interactive tactical map with relationship arcs and intelligence scoring.',
  },
  {
    icon: <MessageSquare className="w-8 h-8" />,
    title: 'Oracle AI — Ask Your Data',
    description:
      'Query the full intelligence database in natural language. Geopolitical events, trends, market signals — all in one conversation.',
  },
  {
    icon: <Download className="w-8 h-8" />,
    title: 'Export & Integrate',
    description:
      'Reports in multiple formats. REST API for integration with your existing workflows, security stack, or internal tooling.',
  },
];

export default function Features() {
  const sectionRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add('opacity-100', 'translate-y-0');
            entry.target.classList.remove('opacity-0', 'translate-y-8');
          }
        });
      },
      {
        threshold: 0.1,
        rootMargin: '0px 0px -100px 0px',
      }
    );

    const cards = sectionRef.current?.querySelectorAll('[data-animate]');
    cards?.forEach((card) => observer.observe(card));

    return () => observer.disconnect();
  }, []);

  return (
    <section id="features" ref={sectionRef} className="py-24 relative">
      <div className="max-w-7xl mx-auto px-6">
        {/* Section Header */}
        <div className="text-center max-w-2xl mx-auto mb-16">
          <h2 className="text-4xl font-extrabold mb-4 text-white">
            Advanced Capabilities
          </h2>
          <p className="text-lg text-gray-400">
            A complete platform for modern intelligence operations
          </p>
        </div>

        {/* Features Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {features.map((feature, index) => (
            <div
              key={feature.title}
              data-animate
              className="opacity-0 translate-y-8 transition-all duration-600"
              style={{ transitionDelay: `${index * 100}ms` }}
            >
              <FeatureCard {...feature} />
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
