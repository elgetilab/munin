import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

// The support widget runs on the static pages and in the chat app, so every
// variable it reads has to exist in both, for light and dark. Its behaviour is
// covered in staticPages.test.ts.
const read = (path: string) => readFileSync(resolve(process.cwd(), path), 'utf-8');
const widget = read('../static/shared/support-widget.js');
const staticCss = read('../static/shared/style.css');
const appCss = read('src/index.css');

// The declarations in the first rule that starts with this selector.
function block(css: string, selector: string): string {
  const start = css.indexOf(`${selector} {`);
  expect(start, selector).toBeGreaterThanOrEqual(0);
  return css.slice(start, css.indexOf('}', start));
}

const defines = (css: string, name: string) => new RegExp(`${name}\\s*:`).test(css);
const used = [...new Set([...widget.matchAll(/var\((--[a-z-]+)/g)].map(m => m[1]))];

describe('support widget theme', () => {
  it('reads theme variables, including the scrim', () => {
    expect(used).toEqual(expect.arrayContaining(['--bg-primary', '--bg-secondary', '--text-primary', '--accent', '--scrim']));
  });

  it.each(used)('finds %s on the static pages in both themes', name => {
    expect(defines(block(staticCss, ':root'), name)).toBe(true);
    expect(defines(block(staticCss, ':root[data-theme="light"]'), name)).toBe(true);
  });

  // The app defines the static names once, as aliases of its --color-* tokens,
  // and each token has a light value.
  it.each(used)('finds %s in the chat app in both themes', name => {
    const aliases = appCss.slice(appCss.indexOf('/* The names static/shared/style.css uses'));
    const target = block(aliases, ':root').match(new RegExp(`${name}:\\s*var\\((--color-[a-z-]+)\\)`));
    expect(target, `${name} has no alias in index.css`).not.toBeNull();
    expect(defines(block(appCss, '@theme'), target![1])).toBe(true);
    expect(defines(block(appCss, ':root[data-theme="light"]'), target![1])).toBe(true);
  });

  it('dims a light page with the light scrim, and keeps the dark backdrop as it was', () => {
    expect(block(widget, ':root[data-theme="light"] #munin-support-backdrop')).toContain('background: var(--scrim)');
    expect(block(widget, '#munin-support-backdrop')).toContain('background: rgba(15, 20, 25, 0.7)');
  });

  it('keeps light placeholders readable, at full strength', () => {
    const rule = widget.slice(widget.indexOf(':root[data-theme="light"] #munin-support-panel input::placeholder'));
    const body = rule.slice(rule.indexOf('{'), rule.indexOf('}'));
    expect(body).toContain('color: var(--text-tertiary)');
    expect(body).toContain('opacity: 1');
  });

  it('only scopes its light rules to a light page', () => {
    const lightRules = [...widget.matchAll(/^\s*(:root\[data-theme="light"\][^{,]*)/gm)].map(m => m[1].trim());
    expect(lightRules.length).toBeGreaterThan(0);
    expect(widget).not.toMatch(/data-theme="dark"/);
  });
});
