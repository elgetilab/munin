import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { DIALOG_BACKDROP, LOGO_FILTER, ROW_HOVER, SCRIM } from './themeClasses';

// Vitest runs from frontend/webui.
const read = (path: string) => readFileSync(resolve(process.cwd(), path), 'utf-8');
const LIGHT = '[[data-theme=light]_&]:';

const classes = (s: string) => s.split(/\s+/);
const lightOnly = (s: string) => classes(s).filter(c => c.startsWith(LIGHT));
const darkOnly = (s: string) => classes(s).filter(c => !c.startsWith(LIGHT));

describe('theme classes', () => {
  it('keep the original dark classes exactly', () => {
    expect(darkOnly(SCRIM)).toEqual(['bg-black/50']);
    expect(darkOnly(DIALOG_BACKDROP)).toEqual(['bg-bg-primary/70', 'backdrop-blur-sm']);
    expect(darkOnly(ROW_HOVER)).toEqual(['hover:bg-bg-primary/30']);
  });

  it('scope every override to the light theme', () => {
    expect(lightOnly(SCRIM)).toEqual([`${LIGHT}bg-scrim`]);
    expect(lightOnly(DIALOG_BACKDROP)).toEqual([`${LIGHT}bg-scrim`]);
    expect(lightOnly(ROW_HOVER)).toEqual([`${LIGHT}hover:bg-bg-tertiary/60`]);
  });

  it('take the logo filter from the theme', () => {
    expect(LOGO_FILTER).toBe('[filter:var(--logo-filter)]');
  });
});

describe('no hard-coded logo filter or error tint', () => {
  const sources = [
    'src/App.tsx',
    'src/components/Sidebar.tsx',
    '../auth/templates/login.html',
    '../auth/templates/verify.html',
  ];

  it.each(sources)('%s has no fixed white logo filter', (path) => {
    const text = read(path);
    expect(text).not.toMatch(/brightness-0 invert|invert\(1\)/);
  });

  it.each(sources.slice(2))('%s tints its error box from --error and the logo from --logo-filter', (path) => {
    const text = read(path);
    expect(text).toContain('filter: var(--logo-filter);');
    // The rgba line stays first as a fallback for browsers without color-mix.
    expect(text).toMatch(
      /background: rgba\(248, 81, 73, 0\.1\);\s+background: color-mix\(in srgb, var\(--error\) 10%, transparent\);/,
    );
  });
});
