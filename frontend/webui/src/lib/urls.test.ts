/**
 * The site-URL contract.
 *
 * Load-bearing: served from the reference deployment's chat host (vitest runs
 * jsdom at https://chat.muninai.org/), the runtime-derived URLs are EXACTLY the
 * literals the app used to hardcode. If one changes, the production chat UI
 * starts pointing somewhere else. The rest pins the derivation for any other
 * domain and the VITE_* overrides the single-host layout uses.
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
  baseDomain,
  resolveSiteUrls,
} from './urls';

describe('served from the reference deployment', () => {
  it('resolves every site URL to what the literals used to say', () => {
    expect(AUTH_BASE).toBe('https://auth.muninai.org');
    expect(SITE_HOME).toBe('https://muninai.org');
    expect(CHAT_URL).toBe('https://chat.muninai.org');
    expect(SEARCH_URL).toBe('https://search.muninai.org');
    expect(DOCS_URL).toBe('https://docs.muninai.org');
    expect(UPLOAD_URL).toBe('https://upload.muninai.org');
    expect(API_PUBLIC_URL).toBe('https://api.muninai.org/v1');
  });

  it('builds the exact auth endpoints the app used to hardcode', () => {
    expect(`${AUTH_BASE}/auth/me`).toBe('https://auth.muninai.org/auth/me');
    expect(`${AUTH_BASE}/admin`).toBe('https://auth.muninai.org/admin');
  });

  it('carries no trailing slash, so template concatenation is safe', () => {
    for (const u of [AUTH_BASE, SITE_HOME, CHAT_URL, SEARCH_URL, DOCS_URL, UPLOAD_URL]) {
      expect(u.endsWith('/')).toBe(false);
    }
  });
});

describe('any other domain', () => {
  const loc = { protocol: 'https:', hostname: 'chat.lab.example.edu' };

  it('derives every URL from the chat host, with no configuration', () => {
    const u = resolveSiteUrls(loc, {});
    expect(u.AUTH_BASE).toBe('https://auth.lab.example.edu');
    expect(u.SITE_HOME).toBe('https://lab.example.edu');
    expect(u.SEARCH_URL).toBe('https://search.lab.example.edu');
    expect(u.API_PUBLIC_URL).toBe('https://api.lab.example.edu/v1');
    expect(JSON.stringify(u)).not.toContain('muninai');
  });

  it('only strips a leading chat. label', () => {
    expect(baseDomain('chat.lab.example.edu')).toBe('lab.example.edu');
    expect(baseDomain('CHAT.Lab.Example.EDU')).toBe('lab.example.edu');
    expect(baseDomain('lab.example.edu')).toBe('lab.example.edu');
    expect(baseDomain('mychat.example.edu')).toBe('mychat.example.edu');
  });

  it('lets VITE_* override, and treats an empty one as unset', () => {
    const u = resolveSiteUrls(
      { protocol: 'http:', hostname: 'localhost' },
      { VITE_AUTH_BASE: 'http://localhost/', VITE_SEARCH_URL: '', VITE_DOCS_URL: '  ' },
    );
    expect(u.AUTH_BASE).toBe('http://localhost');
    expect(u.SEARCH_URL).toBe('http://search.localhost');
    expect(u.DOCS_URL).toBe('http://docs.localhost');
  });
});
