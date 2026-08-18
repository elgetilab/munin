/**
 * Site URLs, resolved at BUILD time from Vite env vars.
 *
 * These used to be string literals scattered across the components. That was
 * fine for one deployment and fatal for any other: a locally-built chat UI
 * called `https://auth.muninai.org` for `/auth/me` and the whole admin
 * surface, so it authenticated against the production instance instead of the
 * one it was served from.
 *
 * EVERY DEFAULT BELOW IS THE PRODUCTION VALUE. A build with no VITE_* vars set
 * therefore emits byte-identical output to before this module existed, which
 * is what makes it safe to ship to the live deployment without touching its
 * build pipeline. Overriding is opt-in: see frontend/webui/.env.example.
 *
 * Note the API itself is NOT here. It is already relative (`/api`, `/paper`),
 * served from the same origin via Caddy, and needs no configuration.
 */

/** Treat an unset OR empty var as absent, so `VITE_AUTH_BASE=` in a .env file
 *  means "use the default" rather than "use the empty string" (which would
 *  turn every auth call into a same-origin relative path and fail obscurely). */
function pick(value: string | undefined, fallback: string): string {
  const v = (value ?? '').trim();
  return v === '' ? fallback : v.replace(/\/+$/, '');
}

const env = import.meta.env;

/** Auth service origin. The only FUNCTIONAL one: `/auth/me` and `/admin/*`
 *  are fetched from it, so a wrong value breaks login and the admin panel. */
export const AUTH_BASE = pick(env.VITE_AUTH_BASE, 'https://auth.muninai.org');

/** Sibling sites, used for navigation links only. A wrong value here is a
 *  dead link in the header, not a broken app. */
export const SITE_HOME = pick(env.VITE_SITE_HOME, 'https://muninai.org');
export const CHAT_URL = pick(env.VITE_CHAT_URL, 'https://chat.muninai.org');
export const SEARCH_URL = pick(env.VITE_SEARCH_URL, 'https://search.muninai.org');
export const DOCS_URL = pick(env.VITE_DOCS_URL, 'https://docs.muninai.org');
export const UPLOAD_URL = pick(env.VITE_UPLOAD_URL, 'https://upload.muninai.org');

/** Shown to the user in Settings as the base_url for API-key clients. Display
 *  only: nothing in the UI calls it. */
export const API_PUBLIC_URL = pick(env.VITE_API_PUBLIC_URL, 'https://api.muninai.org/v1');
