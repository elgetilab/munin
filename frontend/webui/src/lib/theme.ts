// Light, dark or follow the system. Dark is the default.
//
// The choice lives in a cookie, not localStorage, because chat, auth, docs, search,
// research and upload are separate subdomains in production and localStorage is
// per origin. Every page head carries a small inline script (see
// frontend/static/shared/theme-snippet.html and src/lib/theme.test.ts) that reads the
// same cookie and sets data-theme before any CSS loads, so there is no flash.

import { useSyncExternalStore } from 'react';

export type ThemePref = 'light' | 'dark' | 'system';
export type Theme = 'light' | 'dark';

export const THEME_COOKIE = 'munin_theme';
const ONE_YEAR = 60 * 60 * 24 * 365;

// The subdomains Munin serves. A preference set on any of them must reach the
// others, so the cookie goes on the domain they share.
const SITE_SUBDOMAINS = ['chat', 'auth', 'docs', 'search', 'research', 'upload'];

/** Pure, for tests: the Domain attribute for the cookie, or null for a host-only cookie. */
export function cookieDomain(hostname: string): string | null {
  const h = hostname.toLowerCase();
  // localhost and IP addresses cannot take a Domain attribute. Cookies ignore
  // ports, so the local stack's separate ports still share a host-only cookie.
  if (h === 'localhost' || /^[\d.]+$/.test(h) || h.includes(':')) return null;
  const [first, ...rest] = h.split('.');
  if (SITE_SUBDOMAINS.includes(first) && rest.length >= 2) return rest.join('.');
  return rest.length >= 1 ? h : null;
}

/** Pure, for tests: the preference stored in a cookie string. */
export function readPref(cookie: string): ThemePref {
  const m = cookie.match(/(?:^|;\s*)munin_theme=(light|dark|system)(?:;|$)/);
  return m ? (m[1] as ThemePref) : 'dark';
}

function systemTheme(): Theme {
  try {
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  } catch {
    return 'dark';
  }
}

export function resolveTheme(pref: ThemePref): Theme {
  return pref === 'system' ? systemTheme() : pref;
}

function currentPref(): ThemePref {
  try {
    return readPref(document.cookie);
  } catch {
    return 'dark';
  }
}

/** Sets data-theme on <html>, the same attributes the inline head script sets. */
export function applyTheme(pref: ThemePref = currentPref()): Theme {
  const theme = resolveTheme(pref);
  const root = document.documentElement;
  root.setAttribute('data-theme-pref', pref);
  root.setAttribute('data-theme', theme);
  // The browser's own UI (address bar, PWA title bar) follows the page.
  const bg = getComputedStyle(root).getPropertyValue('--color-bg-primary').trim();
  if (bg) document.querySelector('meta[name="theme-color"]')?.setAttribute('content', bg);
  return theme;
}

const listeners = new Set<() => void>();
function notify() {
  applyTheme();
  listeners.forEach(l => l());
}

export function setThemePref(pref: ThemePref): void {
  const domain = cookieDomain(window.location.hostname);
  const secure = window.location.protocol === 'https:' ? '; Secure' : '';
  document.cookie =
    `${THEME_COOKIE}=${pref}; Path=/; Max-Age=${ONE_YEAR}; SameSite=Lax` +
    (domain ? `; Domain=${domain}` : '') + secure;
  notify();
}

let watching = false;
function watch() {
  if (watching || typeof window === 'undefined') return;
  watching = true;
  // A cookie change fires no event, so another tab or site's choice is picked up
  // when this tab comes back into view.
  window.addEventListener('focus', notify);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') notify();
  });
  try {
    window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', notify);
  } catch {
    /* old browsers: the system choice is read again on focus */
  }
}

function subscribe(listener: () => void) {
  watch();
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

function snapshot(): string {
  return `${currentPref()} ${resolveTheme(currentPref())}`;
}

/** The stored preference and the theme it resolves to, kept current. */
export function useTheme(): { pref: ThemePref; theme: Theme; setPref: (p: ThemePref) => void } {
  const [pref, theme] = useSyncExternalStore(subscribe, snapshot, () => 'dark dark').split(' ') as [ThemePref, Theme];
  return { pref, theme, setPref: setThemePref };
}
