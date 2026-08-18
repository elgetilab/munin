/**
 * The site-URL contract.
 *
 * The load-bearing assertion is that the DEFAULTS ARE THE PRODUCTION VALUES.
 * These were string literals inlined at ~20 call sites until 2026-08; moving
 * them behind Vite env vars is only safe for the live deployment if a build
 * with no VITE_* set resolves to exactly what was there before. If one of
 * these changes, the production chat UI starts pointing somewhere else.
 */
import { describe, expect, it } from 'vitest';

import {
  API_PUBLIC_URL,
  AUTH_BASE,
  CHAT_URL,
  DOCS_URL,
  SEARCH_URL,
  SITE_HOME,
  UPLOAD_URL,
} from './urls';

describe('production defaults', () => {
  it('resolves every site URL to the live deployment when nothing is set', () => {
    // The test run sets no VITE_* vars, so this is the production build.
    expect(AUTH_BASE).toBe('https://auth.muninai.org');
    expect(SITE_HOME).toBe('https://muninai.org');
    expect(CHAT_URL).toBe('https://chat.muninai.org');
    expect(SEARCH_URL).toBe('https://search.muninai.org');
    expect(DOCS_URL).toBe('https://docs.muninai.org');
    expect(UPLOAD_URL).toBe('https://upload.muninai.org');
    expect(API_PUBLIC_URL).toBe('https://api.muninai.org/v1');
  });

  it('builds the exact auth endpoints the app used to hardcode', () => {
    // These two strings are what the msw handlers mock and what the live auth
    // service serves. Concatenation must not introduce or drop a slash.
    expect(`${AUTH_BASE}/auth/me`).toBe('https://auth.muninai.org/auth/me');
    expect(`${AUTH_BASE}/admin`).toBe('https://auth.muninai.org/admin');
  });

  it('carries no trailing slash, so template concatenation is safe', () => {
    for (const u of [AUTH_BASE, SITE_HOME, CHAT_URL, SEARCH_URL, DOCS_URL, UPLOAD_URL]) {
      expect(u.endsWith('/')).toBe(false);
    }
  });
});
