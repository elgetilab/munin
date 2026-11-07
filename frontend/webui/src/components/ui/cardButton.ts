// Shared look for the action buttons on the cards in a conversation (plan,
// memory, clarification, Continue, research): at least 36px tall on touch
// screens.
export const BTN = 'inline-flex items-center justify-center px-3 py-1.5 rounded-lg text-xs font-medium transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-default pointer-coarse:min-h-9';
export const BTN_PRIMARY = `${BTN} bg-accent text-bg-primary hover:bg-accent-hover`;
export const BTN_SECONDARY = `${BTN} border border-border text-text-secondary hover:bg-bg-tertiary hover:text-text-primary`;
export const BTN_QUIET = `${BTN} text-text-secondary hover:bg-bg-tertiary hover:text-text-primary`;

// Props for the small line icons in the TaskLog style (14x14, stroke), used
// instead of emoji, which differ by platform and are not all the same width.
// The icons are decorative, so they are hidden from screen readers.
export const ICON = { width: 14, height: 14, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, 'aria-hidden': true };
