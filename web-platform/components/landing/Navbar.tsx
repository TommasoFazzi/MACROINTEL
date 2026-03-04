'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { Button } from '@/components/ui/button';
import { Menu, X, Map, GitBranch } from 'lucide-react';

export default function Navbar() {
  const [scrolled, setScrolled] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  useEffect(() => {
    const handleScroll = () => {
      setScrolled(window.scrollY > 100);
    };

    window.addEventListener('scroll', handleScroll);
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  const scrollToSection = (id: string) => {
    const element = document.getElementById(id);
    if (element) {
      element.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    setMobileMenuOpen(false);
  };

  return (
    <nav
      className={`fixed top-0 left-0 right-0 z-[1000] border-b border-white/5 transition-all duration-300 ${
        scrolled
          ? 'bg-[#0A1628]/95 shadow-lg'
          : 'bg-[#0A1628]/80'
      } backdrop-blur-md`}
    >
      <div className="max-w-7xl mx-auto px-6">
        <div className="flex items-center justify-between h-20">
          {/* Logo */}
          <Link href="/" className="flex items-center gap-3">
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
              <span className="text-[#FF6B35]">INTEL</span>
              <span className="text-white"> ITA</span>
            </span>
          </Link>

          {/* Desktop Navigation */}
          <div className="hidden md:flex items-center gap-8">
            <button
              onClick={() => scrollToSection('features')}
              className="text-sm font-medium text-gray-300 hover:text-[#FF6B35] transition-colors"
            >
              Features
            </button>
            <button
              onClick={() => scrollToSection('about')}
              className="text-sm font-medium text-gray-300 hover:text-[#FF6B35] transition-colors"
            >
              About
            </button>
            <button
              onClick={() => scrollToSection('contact')}
              className="text-sm font-medium text-gray-300 hover:text-[#FF6B35] transition-colors"
            >
              Contact
            </button>
            <Button asChild variant="outline" className="border-[#FF6B35]/30 text-[#FF6B35] hover:bg-[#FF6B35]/10 hover:text-[#FF6B35]">
              <Link href="/stories" className="flex items-center gap-2">
                <GitBranch size={16} />
                Storylines
              </Link>
            </Button>
            <Button asChild variant="outline" className="border-[#00A8E8]/30 text-[#00A8E8] hover:bg-[#00A8E8]/10 hover:text-[#00A8E8]">
              <Link href="/map" className="flex items-center gap-2">
                <Map size={16} />
                Intelligence Map
              </Link>
            </Button>
            <Button asChild>
              <Link href="/dashboard">Access Dashboard</Link>
            </Button>
          </div>

          {/* Mobile Menu Button */}
          <button
            className="md:hidden p-2 text-gray-300 hover:text-white"
            onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
            aria-label="Toggle menu"
          >
            {mobileMenuOpen ? <X size={24} /> : <Menu size={24} />}
          </button>
        </div>

        {/* Mobile Menu */}
        {mobileMenuOpen && (
          <div className="md:hidden py-4 border-t border-white/10">
            <div className="flex flex-col gap-4">
              <button
                onClick={() => scrollToSection('features')}
                className="text-left text-sm font-medium text-gray-300 hover:text-[#FF6B35] transition-colors py-2"
              >
                Features
              </button>
              <button
                onClick={() => scrollToSection('about')}
                className="text-left text-sm font-medium text-gray-300 hover:text-[#FF6B35] transition-colors py-2"
              >
                About
              </button>
              <button
                onClick={() => scrollToSection('contact')}
                className="text-left text-sm font-medium text-gray-300 hover:text-[#FF6B35] transition-colors py-2"
              >
                Contact
              </button>
              <Button asChild variant="outline" className="w-full mt-2 border-[#FF6B35]/30 text-[#FF6B35] hover:bg-[#FF6B35]/10 hover:text-[#FF6B35]">
                <Link href="/stories" className="flex items-center justify-center gap-2">
                  <GitBranch size={16} />
                  Storylines
                </Link>
              </Button>
              <Button asChild variant="outline" className="w-full mt-2 border-[#00A8E8]/30 text-[#00A8E8] hover:bg-[#00A8E8]/10 hover:text-[#00A8E8]">
                <Link href="/map" className="flex items-center justify-center gap-2">
                  <Map size={16} />
                  Intelligence Map
                </Link>
              </Button>
              <Button asChild className="w-full mt-2">
                <Link href="/dashboard">Access Dashboard</Link>
              </Button>
            </div>
          </div>
        )}
      </div>
    </nav>
  );
}
