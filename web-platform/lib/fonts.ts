import { Source_Serif_4 } from 'next/font/google';

/**
 * Editorial serif for long-form report bodies (`/insights/[slug]`, `/romania/[id]`,
 * `/dashboard/report/[id]`). Deliberately NOT registered in `app/layout.tsx`: importing it
 * there would put it on the critical path of every route including `/` and `/dashboard`,
 * which render no long-form prose at all. Because it's imported only by the article routes,
 * Next scopes the `@font-face` and its preload to those routes' CSS.
 *
 * Source Serif 4 over Newsreader: it's the more neutral of the two — larger x-height, low
 * stroke contrast, built as a text face rather than a display one — and it has to sit next
 * to Geist Mono metadata and citations on the same page without competing. Newsreader's
 * extra editorial personality reads "magazine feature", which is the wrong register for an
 * intelligence briefing. Swapping is a one-line change here if that call turns out wrong.
 *
 * next/font self-hosts the files at build time, so they're served from the app's own domain
 * with no request to fonts.gstatic.com.
 */
export const editorialSerif = Source_Serif_4({
  subsets: ['latin'],
  display: 'swap',
  // Distinct from the Tailwind `--font-serif` theme token, which resolves *to* this one —
  // naming both `--font-serif` would make the theme token reference itself (a cyclic custom
  // property, which CSS drops entirely).
  variable: '--font-source-serif',
});
