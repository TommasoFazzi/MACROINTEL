import type { Metadata } from "next";
import { Inter, Geist, Geist_Mono } from "next/font/google";
import Script from "next/script";
import "./globals.css";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  metadataBase: new URL('https://macrointel.net'),
  verification: {
    google: "yYcTCxeGtyPr8lqge6DnoCV5kKSs-p7BGCAibulzoaw",
  },
  title: {
    default: "MACROINTEL | AI-Powered OSINT & Threat Intelligence Platform",
    template: "%s | MACROINTEL",
  },
  description:
    "Monitor geopolitical risks, cyber threats, and macro-economic signals in real time. AI-powered OSINT platform processing 33+ intelligence sources daily into actionable intelligence.",
  keywords: [
    "OSINT automation tool",
    "AI threat intelligence platform",
    "geopolitical risk monitoring",
    "open source intelligence",
    "narrative tracking",
    "threat intelligence",
    "geopolitical intelligence",
    "cybersecurity monitoring",
    "macro economics",
    "RAG intelligence",
  ],
  authors: [{ name: "MACROINTEL" }],
  openGraph: {
    title: "MACROINTEL | AI-Powered OSINT & Threat Intelligence Platform",
    description:
      "Monitor geopolitical risks, cyber threats, and macro-economic signals in real time. AI-powered OSINT platform processing 33+ intelligence sources daily.",
    type: "website",
    locale: "en_US",
    siteName: "MACROINTEL",
  },
  twitter: {
    card: "summary_large_image",
    title: "MACROINTEL | AI-Powered OSINT & Threat Intelligence Platform",
    description:
      "Monitor geopolitical risks, cyber threats, and macro-economic signals in real time. AI-powered OSINT platform processing 33+ intelligence sources daily.",
  },
  robots: {
    index: true,
    follow: true,
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <head>
        <link rel="manifest" href="/manifest.json" />
        <meta name="theme-color" content="#FF6B35" />
      </head>
      <body
        className={`${inter.variable} ${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        <Script
          src="https://www.googletagmanager.com/gtag/js?id=G-MBHW2XG1Q3"
          strategy="afterInteractive"
        />
        <Script id="google-analytics" strategy="afterInteractive">
          {`
            window.dataLayer = window.dataLayer || [];
            function gtag(){dataLayer.push(arguments);}
            gtag('js', new Date());
            gtag('config', 'G-MBHW2XG1Q3');
          `}
        </Script>
        {children}
      </body>
    </html>
  );
}
