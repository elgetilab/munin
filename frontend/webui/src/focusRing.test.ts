import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { contrastRatio } from './lib/colorContrast';

// One keyboard focus ring for the whole app and the static pages: one token,
// visible (3:1, WCAG 1.4.11) against every background a ring sits on, and no
// component drawing its own in some other colour.
const read = (path: string) => readFileSync(resolve(process.cwd(), path), 'utf-8');
const appCss = read('src/index.css').replace(/\/\*[\s\S]*?\*\//g, '');
const staticCss = read('../static/shared/style.css').replace(/\/\*[\s\S]*?\*\//g, '');

function block(css: string, selector: string): string {
  const start = css.indexOf(`${selector} {`);
  expect(start, selector).toBeGreaterThanOrEqual(0);
  return css.slice(css.indexOf('{', start) + 1, css.indexOf('}', start));
}
const value = (body: string, name: string) => body.match(new RegExp(`${name}\\s*:\\s*([^;]+);`))?.[1].trim();

const THEMES = {
  dark: block(appCss, '@theme'),
  light: block(appCss, ':root[data-theme="light"]'),
};

describe('the focus ring', () => {
  it.each(Object.entries(THEMES))('%s: is 3:1 or more against every background', (_theme, body) => {
    const ring = value(body, '--color-focus-ring')!;
    expect(ring).toMatch(/^#[0-9a-f]{6}$/i);
    for (const bg of ['--color-bg-primary', '--color-bg-secondary', '--color-bg-tertiary']) {
      expect(contrastRatio(ring, value(body, bg)!), bg).toBeGreaterThanOrEqual(3);
    }
  });

  it('is drawn from the token, in the app and on the static pages', () => {
    expect(appCss).toMatch(/(^|\n):focus-visible \{\s*outline: 2px solid var\(--color-focus-ring\);/);
    expect(staticCss).toMatch(/(^|\n):focus-visible \{\s*outline: 2px solid var\(--focus-ring\);/);
    // And at rest, so transition-colors has no other colour to fade it in from.
    expect(appCss).toMatch(/\*, ::before, ::after \{\s*outline-color: var\(--color-focus-ring\);/);
  });

  it('is not given another colour by any component', () => {
    const files: string[] = [];
    const walk = (dir: string) => {
      for (const name of readdirSync(dir)) {
        const path = join(dir, name);
        if (statSync(path).isDirectory()) walk(path);
        else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) files.push(path);
      }
    };
    walk(resolve(process.cwd(), 'src'));
    for (const path of files) {
      // A focus variant (focus:, focus-visible:, has-[:focus-visible]:, ...) on
      // an outline or ring utility in a colour other than the token.
      const stray = [...readFileSync(path, 'utf-8').matchAll(/[^\s'"`]*focus[^\s'"`]*:(?:outline|ring)-([a-z][-a-z]*)/g)]
        .filter((m) => !/^(?:focus-ring|none|hidden|offset|solid|dashed|dotted|double|inset)$/.test(m[1]))
        .map((m) => m[0]);
      expect(stray, path).toEqual([]);
    }
  });
});
