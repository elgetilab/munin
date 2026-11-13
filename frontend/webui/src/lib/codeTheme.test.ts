import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { codeHighlightStyle, ONE_DARK_TO_VAR } from './codeTheme';
import { contrastRatio } from './colorContrast';

const SRC = resolve(__dirname, '..');
const codeCss = readFileSync(resolve(SRC, 'styles/code.css'), 'utf8');
const indexCss = readFileSync(resolve(SRC, 'index.css'), 'utf8');

// The custom properties declared in the first rule whose selector is `selector`.
function varsOf(css: string, selector: string): Record<string, string> {
  const clean = css.replace(/\/\*[\s\S]*?\*\//g, '');
  const start = clean.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`No ${selector} rule`);
  const body = clean.slice(start, clean.indexOf('}', start));
  const out: Record<string, string> = {};
  for (const m of body.matchAll(/--([\w-]+):\s*([^;]+);/g)) out[m[1]] = m[2].trim();
  return out;
}

const darkCode = varsOf(codeCss, ':root');
const lightCode = varsOf(codeCss, ':root[data-theme="light"]');
const darkTheme = varsOf(indexCss, '@theme');
const lightTheme = varsOf(indexCss, ':root[data-theme="light"]');

const NOT_TEXT = new Set(['code-bg', 'code-text-shadow']);
const tokenVars = (vars: Record<string, string>) =>
  Object.entries(vars).filter(([name]) => name.startsWith('code-') && !NOT_TEXT.has(name));

// Keys react-syntax-highlighter can match: a token's own class names, joined
// with dots. Selector-shaped keys (spaces, brackets, colons, >) never apply.
const appliedKeys = Object.keys(codeHighlightStyle).filter((k) => /^[\w-]+(\.[\w-]+)*$/.test(k));

describe('codeHighlightStyle', () => {
  it('colours every token from a --code-* variable, not a fixed colour', () => {
    const coloured = appliedKeys.filter((k) => codeHighlightStyle[k].color);
    expect(coloured.length).toBeGreaterThan(20);
    for (const key of coloured) {
      expect(codeHighlightStyle[key].color, key).toMatch(/^var\(--code-[\w-]+\)$/);
    }
  });

  it('gives the block its background, text and shadow from variables', () => {
    for (const key of ['pre[class*="language-"]', 'code[class*="language-"]']) {
      expect(codeHighlightStyle[key]).toMatchObject({
        background: 'var(--code-bg)',
        color: 'var(--code-base)',
        textShadow: 'var(--code-text-shadow)',
      });
    }
  });

  it('keeps oneDark layout, such as the font and padding', () => {
    const pre = oneDark['pre[class*="language-"]'];
    expect(codeHighlightStyle['pre[class*="language-"]'].fontFamily).toBe(pre.fontFamily);
    expect(codeHighlightStyle['pre[class*="language-"]'].padding).toBe(pre.padding);
    expect(codeHighlightStyle.comment.fontStyle).toBe('italic');
  });

  it('maps only to variables that code.css defines in both themes', () => {
    for (const ref of new Set(Object.values(ONE_DARK_TO_VAR))) {
      const name = /^var\(--([\w-]+)\)$/.exec(ref)![1];
      expect(darkCode[name], `dark ${name}`).toBeDefined();
      expect(lightCode[name], `light ${name}`).toBeDefined();
    }
  });
});

describe('code.css palette', () => {
  it('defines the same variables for both themes', () => {
    expect(Object.keys(lightCode).sort()).toEqual(Object.keys(darkCode).sort());
  });

  // 2026-11: dark code sits on the ink grey with softer hues that suit the accent,
  // in place of oneDark's own colours and its blue-grey #282c34.
  it('puts dark code artifacts on the ink grey, like Markdown blocks', () => {
    expect(darkCode['code-bg']).toBe('var(--color-bg-secondary)');
    expect(darkCode['code-text-shadow']).toBe('0 1px rgba(0, 0, 0, 0.3)');
  });

  // Dark: code artifacts and Markdown blocks both sit on bg-secondary, inside
  // the page's bg-primary.
  const darkGrounds = { 'bg-secondary': darkTheme['color-bg-secondary'], 'bg-primary': darkTheme['color-bg-primary'] };
  // Light: --code-bg is bg-secondary, and print puts code on white.
  const lightGrounds = { 'bg-secondary': lightTheme['color-bg-secondary'], 'print white': '#ffffff' };

  it.each(tokenVars(darkCode))('dark %s reaches 4.5:1 on each code background', (name, colour) => {
    for (const [ground, bg] of Object.entries(darkGrounds)) {
      expect(contrastRatio(colour, bg), `${name} on ${ground}`).toBeGreaterThanOrEqual(4.5);
    }
  });

  it.each(tokenVars(lightCode))('light %s reaches 4.5:1 on each code background', (name, colour) => {
    expect(lightCode['code-bg']).toBe('var(--color-bg-secondary)');
    for (const [ground, bg] of Object.entries(lightGrounds)) {
      expect(contrastRatio(colour, bg), `${name} on ${ground}`).toBeGreaterThanOrEqual(4.5);
    }
  });
});
