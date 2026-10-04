/**
 * Site URLs: a Vite env var if one is set, otherwise derived at RUNTIME from
 * the hostname the chat UI is served on.
 *
 * These used to be string literals scattered across the components, then
 * build-time defaults pointing at the reference deployment. Either way a chat
 * UI built for another domain called `https://auth.muninai.org` for
 * `/auth/me` and the whole admin surface, authenticating against someone
 * else's instance.
 *
 * Now nothing is baked in. Served from `chat.lab.example.edu`, the UI uses
 * `auth.lab.example.edu`, `search.lab.example.edu` and so on, so a production
 * build needs no configuration and works on any domain that follows the
 * subdomain layout. Served from `chat.muninai.org` it resolves exactly the
 * URLs the literals used to name (urls.test.ts pins that).
 *
 * The single-host layout (everything on http://localhost, the other UIs on
 * their own ports) does not follow the subdomain layout, so it sets the
 * VITE_* overrides; see .env.example at the repo root.
 *
 * The API itself is NOT here: it is relative (`/api`, `/paper`), same origin.
 */

export interface SiteUrls {
  AUTH_BASE: string;
  SITE_HOME: string;
  CHAT_URL: string;
  SEARCH_URL: string;
  DOCS_URL: string;
  UPLOAD_URL: string;
  API_PUBLIC_URL: string;
}

/** Treat an unset OR empty var as absent, so `VITE_AUTH_BASE=` in a .env file
 *  means "derive it" rather than "use the empty string" (which would turn
 *  every auth call into a same-origin relative path and fail obscurely). */
function pick(value: string | undefined, fallback: string): string {
  const v = (value ?? '').trim();
  return (v === '' ? fallback : v).replace(/\/+$/, '');
}

/** The instance's base domain: the chat host minus its `chat.` label. */
export function baseDomain(hostname: string): string {
  const h = hostname.toLowerCase();
  return h.startsWith('chat.') ? h.slice('chat.'.length) : h;
}

/** Pure, for tests: every site URL for a given location and env. */
export function resolveSiteUrls(
  loc: { protocol: string; hostname: string },
  env: Record<string, string | undefined>,
): SiteUrls {
  const d = baseDomain(loc.hostname);
  const at = (sub: string) => `${loc.protocol}//${sub ? `${sub}.` : ''}${d}`;
  return {
    // The only FUNCTIONAL one: `/auth/me` and `/admin/*` are fetched from it,
    // so a wrong value breaks login and the admin panel.
    AUTH_BASE: pick(env.VITE_AUTH_BASE, at('auth')),
    // Navigation links only: a wrong value is a dead link, not a broken app.
    SITE_HOME: pick(env.VITE_SITE_HOME, at('')),
    CHAT_URL: pick(env.VITE_CHAT_URL, at('chat')),
    SEARCH_URL: pick(env.VITE_SEARCH_URL, at('search')),
    DOCS_URL: pick(env.VITE_DOCS_URL, at('docs')),
    UPLOAD_URL: pick(env.VITE_UPLOAD_URL, at('upload')),
    // Shown in Settings as the base_url for API-key clients; display only.
    API_PUBLIC_URL: pick(env.VITE_API_PUBLIC_URL, `${at('api')}/v1`),
  };
}

const urls = resolveSiteUrls(
  typeof window !== 'undefined' ? window.location : { protocol: 'https:', hostname: '' },
  import.meta.env as Record<string, string | undefined>,
);

export const {
  AUTH_BASE,
  SITE_HOME,
  CHAT_URL,
  SEARCH_URL,
  DOCS_URL,
  UPLOAD_URL,
  API_PUBLIC_URL,
} = urls;
