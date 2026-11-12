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
    '../static/shared/auth.css',
    '../static/shared/site.css',
  ];

  it.each(sources)('%s has no fixed white logo filter', (path) => {
    const text = read(path);
    expect(text).not.toMatch(/brightness-0 invert|invert\(1\)/);
  });

  // The sign-in pages share /shared/auth.css: the raven is a mask filled with
  // the theme's accent, and the error row takes its red from --danger, which
  // each theme sets.
  it.each(['../auth/templates/login.html', '../auth/templates/verify.html'])('%s loads the shared sign-in styles', (path) => {
    expect(read(path)).toMatch(/<link rel="stylesheet" href="\/shared\/auth\.css\?v=\d+">/);
  });

  it('auth.css fills the logo and tints the error box from the theme', () => {
    const css = read('../static/shared/auth.css');
    expect(css).toMatch(/\.auth-mark \{[^}]*background: var\(--brass\);[^}]*mask: url\(\/shared\/munin_logo_without_script\.webp\)/);
    expect(css).toMatch(/:root \{[^}]*--danger: #[0-9a-f]{6};/);
    expect(css).toMatch(/:root\[data-theme="light"\] \{[^}]*--danger: #[0-9a-f]{6};/);
    // The plain border stays first as a fallback for browsers without color-mix.
    expect(css).toMatch(
      /\.auth-error \{[^}]*border: 1px solid var\(--danger\); border: 1px solid color-mix\(in srgb, var\(--danger\) 45%, transparent\);[^}]*background: var\(--danger-soft\);/,
    );
  });
});
