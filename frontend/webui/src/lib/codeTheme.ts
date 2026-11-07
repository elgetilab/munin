// The syntax highlighter's style object, with oneDark's colours swapped for the
// --code-* variables in src/styles/code.css. react-syntax-highlighter writes
// these as inline styles, so var() values let a theme switch (and the light
// print window) recolour code that is already on screen without a re-render.
import type { CSSProperties } from 'react';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';

export type HighlightStyle = Record<string, CSSProperties>;

// oneDark's palette, by the exact strings it uses, and the variable each maps to.
export const ONE_DARK_TO_VAR: Record<string, string> = {
  'hsl(220, 14%, 71%)': 'var(--code-base)',
  'hsl(220, 10%, 40%)': 'var(--code-comment)',
  'hsl(286, 60%, 67%)': 'var(--code-keyword)',
  'hsl(95, 38%, 62%)': 'var(--code-string)',
  'hsl(207, 82%, 66%)': 'var(--code-function)',
  'hsl(29, 54%, 61%)': 'var(--code-number)',
  'hsl(355, 65%, 65%)': 'var(--code-tag)',
  'hsl(187, 47%, 55%)': 'var(--code-url)',
  // Template string interpolation braces; closest in role to tag.
  'hsl(5, 48%, 51%)': 'var(--code-tag)',
};

const BLOCK_KEYS = ['pre[class*="language-"]', 'code[class*="language-"]'];

function themed(style: CSSProperties): CSSProperties {
  const out: CSSProperties = { ...style };
  if (typeof out.color === 'string' && ONE_DARK_TO_VAR[out.color]) {
    out.color = ONE_DARK_TO_VAR[out.color];
  }
  return out;
}

const base: HighlightStyle = {};
for (const [key, value] of Object.entries(oneDark as HighlightStyle)) {
  base[key] = themed(value);
}
for (const key of BLOCK_KEYS) {
  base[key] = {
    ...base[key],
    background: 'var(--code-bg)',
    textShadow: 'var(--code-text-shadow)',
  };
}

/** oneDark's layout and token rules, coloured from --code-* so it follows the theme. */
export const codeHighlightStyle: HighlightStyle = base;
