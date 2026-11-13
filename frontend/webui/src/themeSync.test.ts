import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

// The SPA (src/index.css, --color-* names) and the static and auth pages
// (static/shared/style.css, bare names) carry the same palette twice. These
// checks keep the two copies equal in both themes, and keep every page loading
// the light-aware shared scripts in a way a stale cache cannot undo.
const read = (path: string) => readFileSync(resolve(process.cwd(), path), 'utf-8');
const appCss = read('src/index.css');
const staticCss = read('../static/shared/style.css');

// The declarations of the first rule that starts with this selector, as
// name -> value. Comments are dropped first.
function decls(css: string, selector: string): Map<string, string> {
  const clean = css.replace(/\/\*[\s\S]*?\*\//g, '');
  const start = clean.indexOf(`${selector} {`);
  expect(start, selector).toBeGreaterThanOrEqual(0);
  const body = clean.slice(clean.indexOf('{', start) + 1, clean.indexOf('}', start));
  const out = new Map<string, string>();
  for (const m of body.matchAll(/([-a-z0-9]+)\s*:\s*([^;]+);/g)) out.set(m[1], m[2].trim());
  return out;
}

// index.css names, mapped to the style.css name for the same value.
function asStaticNames(map: Map<string, string>): Map<string, string> {
  return new Map([...map].map(([k, v]) => [k.replace(/^--color-/, '--'), v]));
}

// Defined by style.css only: the landing page gradient has no SPA user.
const STATIC_ONLY = ['--accent-2'];

function compare(app: Map<string, string>, stat: Map<string, string>) {
  const appNames = [...app.keys()].sort();
  const staticNames = [...stat.keys()].filter(k => !STATIC_ONLY.includes(k)).sort();
  expect(appNames).toEqual(staticNames);
  for (const name of appNames) expect(app.get(name), name).toBe(stat.get(name));
}

describe('the two palettes stay in step', () => {
  it('light: index.css matches style.css', () => {
    compare(asStaticNames(decls(appCss, ':root[data-theme="light"]')), decls(staticCss, ':root[data-theme="light"]'));
  });

  it('dark: the @theme block and the default root match style.css', () => {
    const app = asStaticNames(decls(appCss, '@theme'));
    // --logo-filter and color-scheme sit in the SPA's own :root rule.
    for (const [k, v] of decls(appCss, ':root')) app.set(k, v);
    compare(app, decls(staticCss, ':root'));
  });
});

describe('shared scripts on the static and auth pages', () => {
  const statics = ['landing/index.html', 'search/index.html', 'upload/index.html',
    'maintenance/index.html', 'docs/index.html', 'docs/architecture.html', 'docs/guide.html'];
  const auth = ['login.html', 'verify.html'];
  const pages = [
    ...statics.map(p => [p, read(`../static/${p}`)] as const),
    ...auth.map(p => [`auth/${p}`, read(`../auth/templates/${p}`)] as const),
  ];

  // Both scripts changed for light mode, so an unversioned tag could serve a
  // cached dark-only copy (near-white feathers on a white page).
  it.each(pages)('%s loads the light-aware scripts with a version', (_path, html) => {
    for (const name of ['feather-vortex.js', 'support-widget.js']) {
      for (const m of html.matchAll(new RegExp(`src="/shared/${name.replace('.', '\\.')}([^"]*)"`, 'g'))) {
        expect(m[1], name).toMatch(/^\?v=\d+$/);
      }
    }
  });

  it.each(auth)('auth/%s loads theme.js, so Match system and other tabs are followed', (path) => {
    expect(read(`../auth/templates/${path}`)).toMatch(/<script src="\/shared\/theme\.js\?v=\d+" defer><\/script>/);
  });
});
