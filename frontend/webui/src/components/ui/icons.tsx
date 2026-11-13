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

/** A prompt: code. */
export function CodeIcon(p: IconProps) {
  return <Svg {...p}><polyline points="4 17 10 11 4 5" /><line x1="12" y1="19" x2="20" y2="19" /></Svg>;
}

/** A small chevron pointing right: opens a section. */
export function ChevronRightIcon(p: IconProps) {
  return <Svg {...p}><polyline points="9 18 15 12 9 6" /></Svg>;
}

/** A small chevron pointing down: opens a menu. */
export function ChevronDownIcon(p: IconProps) {
  return <Svg {...p}><polyline points="6 9 12 15 18 9" /></Svg>;
}

/** A circular arrow: try again. */
export function RetryIcon(p: IconProps) {
  return <Svg {...p}><polyline points="1 4 1 10 7 10" /><path d="M3.51 15a9 9 0 102.13-9.36L1 10" /></Svg>;
}

/** A pencil: edit. */
export function PencilIcon(p: IconProps) {
  return <Svg {...p}><path d="M17 3a2.83 2.83 0 114 4L7.5 20.5 2 22l1.5-5.5z" /></Svg>;
}

/** Two overlapping sheets: copy. */
export function CopyIcon(p: IconProps) {
  return <Svg {...p}><rect x="9" y="9" width="13" height="13" rx="2" /><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" /></Svg>;
}

/** A tick: done. */
export function CheckIcon(p: IconProps) {
  return <Svg {...p}><polyline points="20 6 9 17 4 12" /></Svg>;
}

/** A price tag: a topic. */
export function TagIcon(p: IconProps) {
  return <Svg {...p}><path d="M20.59 13.41l-7.17 7.17a2 2 0 01-2.83 0L2 12V2h10l8.59 8.59a2 2 0 010 2.82z" /><line x1="7" y1="7" x2="7.01" y2="7" /></Svg>;
}

/** Two people: a research group. */
export function UsersIcon(p: IconProps) {
  return <Svg {...p}><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M23 21v-2a4 4 0 00-3-3.87" /><path d="M16 3.13a4 4 0 010 7.75" /></Svg>;
}

/** One person: a contributor. */
export function UserIcon(p: IconProps) {
  return <Svg {...p}><path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2" /><circle cx="12" cy="7" r="4" /></Svg>;
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

/** A little ghost: a temporary chat, which leaves nothing behind. */
export function GhostIcon(p: IconProps) {
  return <Svg {...p}><path d="M5 21V10a7 7 0 0 1 14 0v11l-2.33-2-2.34 2L12 19l-2.33 2-2.34-2z" /><path d="M9.5 10.5h.01M14.5 10.5h.01" strokeWidth={3} /></Svg>;
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

export function SunIcon(p: IconProps) {
  return <Svg {...p}><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" /></Svg>;
}

export function MoonIcon(p: IconProps) {
  return <Svg {...p}><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9z" /></Svg>;
}
