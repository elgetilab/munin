// The line icons that appear in several places, drawn once. They follow the
// TaskLog style (24-unit grid, round stroke in the current text colour) and are
// decorative: whatever they sit beside names the action, so screen readers skip
// them. Give a button that holds only an icon its own aria-label.

import type { ReactNode } from 'react';

export interface IconProps {
  /** Width and height in pixels. */
  size?: number;
  strokeWidth?: number;
  className?: string;
}

function Svg({ size = 14, strokeWidth = 2, className, children }: IconProps & { children: ReactNode }) {
  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      {children}
    </svg>
  );
}

export function SearchIcon(p: IconProps) {
  return <Svg {...p}><circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" /></Svg>;
}

export function PageIcon(p: IconProps) {
  return <Svg {...p}><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" /><polyline points="14 2 14 8 20 8" /></Svg>;
}

export function FlaskIcon(p: IconProps) {
  return <Svg {...p}><path d="M9 3h6M10 3v6.5L5.5 18a2 2 0 0 0 1.8 3h9.4a2 2 0 0 0 1.8-3L14 9.5V3" /></Svg>;
}

export function BookIcon(p: IconProps) {
  return <Svg {...p}><path d="M2 3h6a4 4 0 014 4v14a3 3 0 00-3-3H2z" /><path d="M22 3h-6a4 4 0 00-4 4v14a3 3 0 013-3h7z" /></Svg>;
}

export function FolderIcon(p: IconProps) {
  return <Svg {...p}><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z" /></Svg>;
}

export function ChatIcon(p: IconProps) {
  return <Svg {...p}><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z" /></Svg>;
}

/** An open ring. Spin it with a class such as motion-safe:animate-spin. */
export function SpinnerIcon(p: IconProps) {
  return <Svg {...p}><path d="M21 12a9 9 0 1 1-6.219-8.56" /></Svg>;
}

export function CloseIcon({ size = 12, strokeWidth = 2.5, ...p }: IconProps) {
  return <Svg size={size} strokeWidth={strokeWidth} {...p}><path d="M6 6l12 12M18 6L6 18" /></Svg>;
}

export function MenuIcon(p: IconProps) {
  return <Svg {...p}><path d="M4 6h16M4 12h16M4 18h16" /></Svg>;
}

/** A dashed clock: a chat that will not be kept. */
export function TemporaryIcon(p: IconProps) {
  return <Svg {...p}><path d="M12 7v5l3 2" /><path d="M10.1 2.2a10 10 0 0 1 3.8 0M17.6 3.9a10 10 0 0 1 2.5 2.6M21.8 10.1a10 10 0 0 1 0 3.8M20.1 17.5a10 10 0 0 1-2.6 2.6M13.9 21.8a10 10 0 0 1-3.8 0M6.4 20.1a10 10 0 0 1-2.5-2.6M2.2 13.9a10 10 0 0 1 0-3.8M3.9 6.5a10 10 0 0 1 2.6-2.6" /></Svg>;
}

export function LayersIcon(p: IconProps) {
  return <Svg {...p}><path d="M12 2 2 7l10 5 10-5z" /><path d="m2 17 10 5 10-5" /><path d="m2 12 10 5 10-5" /></Svg>;
}

/** Filled when `filled` is set: the chat has been reported. */
export function FlagIcon({ filled, ...p }: IconProps & { filled?: boolean }) {
  return <Svg {...p}><path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z" fill={filled ? 'currentColor' : 'none'} /><path d="M4 22v-7" /></Svg>;
}

export function PlusIcon(p: IconProps) {
  return <Svg {...p}><path d="M12 5v14M5 12h14" /></Svg>;
}
