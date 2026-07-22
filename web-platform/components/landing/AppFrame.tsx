import Image from 'next/image';
import type { ReactNode } from 'react';

type AppFrameProps = {
  src?: string;
  label: string;
  labelColor?: string;
  badge?: string;
  alt?: string;
  children?: ReactNode;
};

export default function AppFrame({ src, label, labelColor = 'var(--data-1)', badge, alt, children }: AppFrameProps) {
  return (
    <div className="w-full overflow-hidden rounded-[10px] border border-white/[0.08] shadow-[0_24px_60px_rgba(0,0,0,0.6)]">
      <div className="flex items-center gap-2 border-b border-white/[0.06] bg-background px-3.5 py-2">
        <div className="flex gap-[5px]">
          <div className="h-[9px] w-[9px] rounded-full bg-red-500 opacity-70" />
          <div className="h-[9px] w-[9px] rounded-full bg-amber-500 opacity-70" />
          <div className="h-[9px] w-[9px] rounded-full bg-emerald-500 opacity-70" />
        </div>
        <span className="ml-1 font-mono text-meta font-bold" style={{ color: labelColor }}>
          {label}
        </span>
        {badge && (
          <span
            className="ml-auto rounded-[3px] px-1.5 py-0.5 font-mono text-meta"
            style={{
              color: labelColor,
              background: `color-mix(in srgb, ${labelColor} 15%, transparent)`,
              border: `1px solid color-mix(in srgb, ${labelColor} 30%, transparent)`,
            }}
          >
            {badge}
          </span>
        )}
      </div>
      {src ? (
        <div className="relative h-[300px] w-full">
          <Image
            src={src}
            alt={alt ?? label}
            fill
            sizes="(max-width: 768px) 100vw, 720px"
            className="object-cover object-top"
          />
        </div>
      ) : children ? (
        children
      ) : (
        <div className="flex h-[300px] w-full items-center justify-center bg-[linear-gradient(135deg,rgba(255,107,53,0.12)_0%,rgba(0,168,232,0.12)_100%)] font-mono text-meta tracking-[0.12em] text-fg-subtle">
          PREVIEW UNAVAILABLE
        </div>
      )}
    </div>
  );
}
