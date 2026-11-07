import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cookieDomain, readPref, resolveTheme, setThemePref, applyTheme } from './theme';

const FRONTEND = resolve(__dirname, '../../..');

describe('cookieDomain', () => {
  it('shares the cookie across the Munin subdomains', () => {
    for (const host of ['chat.muninai.org', 'auth.muninai.org', 'docs.muninai.org', 'search.muninai.org',
      'research.muninai.org', 'upload.muninai.org', 'muninai.org']) {
      expect(cookieDomain(host)).toBe('muninai.org');
    }
  });

  it('keeps a deeper deployment on its own domain, not its parent', () => {
    expect(cookieDomain('chat.munin.uni-example.de')).toBe('munin.uni-example.de');
    expect(cookieDomain('munin.uni-example.de')).toBe('munin.uni-example.de');
  });

  it('uses a host-only cookie on localhost and IP addresses', () => {
    expect(cookieDomain('localhost')).toBeNull();
    expect(cookieDomain('127.0.0.1')).toBeNull();
    expect(cookieDomain('::1')).toBeNull();
  });
});

describe('readPref', () => {
  it('defaults to dark', () => {
    expect(readPref('')).toBe('dark');
    expect(readPref('munin_session=abc')).toBe('dark');
    expect(readPref('munin_theme=purple')).toBe('dark');
    expect(readPref('xmunin_theme=light')).toBe('dark');
  });

  it('reads each value wherever the cookie sits', () => {
    expect(readPref('munin_theme=light')).toBe('light');
    expect(readPref('a=1; munin_theme=system; b=2')).toBe('system');
    expect(readPref('a=1;munin_theme=dark')).toBe('dark');
  });
});

describe('resolveTheme', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('follows the system only when asked to', () => {
    vi.stubGlobal('matchMedia', (q: string) => ({ matches: q.includes('light'), addEventListener() {} }));
    expect(resolveTheme('system')).toBe('light');
    expect(resolveTheme('dark')).toBe('dark');
    expect(resolveTheme('light')).toBe('light');
  });
});

describe('setThemePref', () => {
  afterEach(() => {
    document.cookie = 'munin_theme=; Max-Age=0; Path=/';
    document.documentElement.removeAttribute('data-theme');
    document.documentElement.removeAttribute('data-theme-pref');
  });

  it('stores the choice and applies it to the page', () => {
    setThemePref('light');
    expect(readPref(document.cookie)).toBe('light');
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
    expect(document.documentElement.getAttribute('data-theme-pref')).toBe('light');
    setThemePref('dark');
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
  });

  it('applyTheme with no cookie gives dark', () => {
    expect(applyTheme()).toBe('dark');
  });
});

// Every page has its own head, so each carries a copy of the script that sets the
// theme before CSS loads. They must stay identical, and agree with theme.ts.
describe('the inline head script', () => {
  const canonical = readFileSync(resolve(FRONTEND, 'static/shared/theme-snippet.html'), 'utf-8').trim();
  const pages = [
    'webui/index.html', 'static/landing/index.html', 'static/search/index.html', 'static/research/index.html',
    'static/upload/index.html', 'static/maintenance/index.html', 'static/docs/index.html',
    'static/docs/architecture.html', 'static/docs/guide.html', 'auth/templates/login.html',
    'auth/templates/verify.html',
  ];

  it.each(pages)('%s carries the same copy, before any stylesheet', (page) => {
    const html = readFileSync(resolve(FRONTEND, page), 'utf-8');
    const at = html.indexOf(canonical);
    expect(at, 'identical copy present').toBeGreaterThan(-1);
    const css = html.search(/<link[^>]+stylesheet|<style/);
    if (css > -1) expect(at).toBeLessThan(css);
  });

  it('has nothing Caddy templates or Jinja would read as their own syntax', () => {
    expect(canonical).not.toMatch(/\{\{|\{%|\{#/);
  });

  const run = (cookie: string, prefersLight = false) => {
    const root = document.createElement('html');
    const body = canonical.replace(/^<script[^>]*>|<\/script>$/g, '');
    new Function('document', 'matchMedia', body)(
      { cookie, documentElement: root },
      () => ({ matches: prefersLight }),
    );
    return [root.getAttribute('data-theme-pref'), root.getAttribute('data-theme')];
  };

  it('agrees with theme.ts', () => {
    expect(run('')).toEqual(['dark', 'dark']);
    expect(run('a=1; munin_theme=light')).toEqual(['light', 'light']);
    expect(run('munin_theme=system', true)).toEqual(['system', 'light']);
    expect(run('munin_theme=system', false)).toEqual(['system', 'dark']);
    expect(run('munin_theme=purple')).toEqual(['dark', 'dark']);
  });
});
