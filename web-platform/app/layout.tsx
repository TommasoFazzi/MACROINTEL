import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Script from "next/script";
import { headers } from "next/headers";
import CookieConsent from "@/components/CookieConsent";
import { SOURCE_COUNT } from "@/lib/constants";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  viewportFit: 'cover',
  themeColor: '#FF6B35',
};

export const metadata: Metadata = {
  metadataBase: new URL('https://macrointel.net'),
  verification: {
    google: "yYcTCxeGtyPr8lqge6DnoCV5kKSs-p7BGCAibulzoaw",
  },
  title: {
    default: "MACROINTEL | AI-Powered OSINT & Geopolitical Intelligence Platform",
    template: "%s | MACROINTEL",
  },
  description:
    `MACROINTEL is an AI-powered OSINT platform monitoring geopolitical risks, cyber threats, and macro-economic signals in real time. ${SOURCE_COUNT} sources processed daily into structured intelligence briefs, narrative graphs, and trade signal alerts.`,
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
      `Monitor geopolitical risks, cyber threats, and macro-economic signals in real time. AI-powered OSINT platform processing ${SOURCE_COUNT} intelligence sources daily.`,
    type: "website",
    locale: "en_US",
    siteName: "MACROINTEL",
  },
  twitter: {
    card: "summary_large_image",
    title: "MACROINTEL | AI-Powered OSINT & Threat Intelligence Platform",
    description:
      `Monitor geopolitical risks, cyber threats, and macro-economic signals in real time. AI-powered OSINT platform processing ${SOURCE_COUNT} intelligence sources daily.`,
  },
  robots: {
    index: true,
    follow: true,
  },
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const nonce = (await headers()).get('x-nonce') ?? undefined;

  return (
    <html lang="en">
      <head>
        <link rel="manifest" href="/manifest.json" />
      </head>
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        <Script id="consent-default" strategy="beforeInteractive" nonce={nonce}>
          {`
            window.dataLayer = window.dataLayer || [];
            function gtag(){dataLayer.push(arguments);}
            gtag('consent', 'default', {
              analytics_storage: 'denied',
              ad_storage: 'denied',
              ad_user_data: 'denied',
              ad_personalization: 'denied',
              wait_for_update: 500
            });
          `}
        </Script>
        <Script
          src="https://www.googletagmanager.com/gtag/js?id=G-MBHW2XG1Q3"
          strategy="afterInteractive"
          nonce={nonce}
        />
        <Script id="google-analytics" strategy="afterInteractive" nonce={nonce}>
          {`
            window.dataLayer = window.dataLayer || [];
            function gtag(){dataLayer.push(arguments);}
            gtag('js', new Date());
            gtag('config', 'G-MBHW2XG1Q3');
          `}
        </Script>
        <Script id="json-ld-org" type="application/ld+json" strategy="afterInteractive" nonce={nonce}>
          {JSON.stringify({
            "@context": "https://schema.org",
            "@graph": [
              {
                "@type": "Organization",
                "name": "MACROINTEL",
                "url": "https://macrointel.net",
                "description": `AI-powered OSINT & geopolitical intelligence platform processing ${SOURCE_COUNT} sources daily into actionable intelligence.`
              },
              {
                "@type": "WebSite",
                "name": "MACROINTEL",
                "url": "https://macrointel.net"
              }
            ]
          })}
        </Script>
        {children}
        <CookieConsent />
      </body>
    </html>
  );
}
