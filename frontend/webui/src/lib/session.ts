/**
 * Notices a sign-in session that ended while the tab was open.
 *
 * On the live site an expired session makes Caddy answer an /api request with a
 * 302 to the auth service's login page. fetch follows it cross-origin and fails
 * with a TypeError, as a network outage would; some routes answer 401 instead.
 * Neither says for sure that the session is over, so a failure only asks
 * `AUTH_BASE/auth/me`, which answers 401 when it is. That check is throttled, so
 * a burst of failed requests or a server that is down for a while costs one
 * request, not one per failure.
 *
 * Nothing navigates: the page may hold a draft the user has not sent. App shows
 * a notice with a link to sign in again (SessionNotice).
 */

import { AUTH_BASE } from './urls';

/** The least time between two checks of /auth/me. */
export const SESSION_CHECK_INTERVAL_MS = 30_000;

export type ApiFailure = 'network' | 'unauthorized';

let ended = false;
let lastCheckAt = Number.NEGATIVE_INFINITY;
let checking = false;
const listeners = new Set<() => void>();

function setEnded() {
  if (ended) return;
  ended = true;
  listeners.forEach(fn => fn());
}

/** True once /auth/me has said the session is over. */
export function isSessionEnded(): boolean {
  return ended;
}

export function subscribeSession(onChange: () => void): () => void {
  listeners.add(onChange);
  return () => { listeners.delete(onChange); };
}

/** The login page, set to come back to this page afterwards. */
export function signInAgainUrl(href: string = window.location.href): string {
  return `${AUTH_BASE}/login?redirect=${encodeURIComponent(href)}`;
}

/**
 * What a request's outcome says about the session, if anything: a TypeError
 * from fetch (a network failure, which is what a redirect to the login page
 * looks like) or a 401 response. An abort and every other status say nothing.
 */
export function classifyApiFailure(outcome: unknown): ApiFailure | null {
  if (outcome instanceof TypeError) return 'network';
  if (outcome && typeof outcome === 'object' && 'status' in outcome && (outcome as { status: unknown }).status === 401) {
    return 'unauthorized';
  }
  return null;
}

/**
 * Whether a failed request should lead to a check of /auth/me now. Pure, so the
 * throttle can be tested on its own.
 */
export function shouldCheckSession(state: { ended: boolean; checking: boolean; lastCheckAt: number; now: number }): boolean {
  if (state.ended || state.checking) return false;
  return state.now - state.lastCheckAt >= SESSION_CHECK_INTERVAL_MS;
}

/** Called for every failed API request; checks the session when that is due. */
export function reportApiFailure(failure: ApiFailure | null): void {
  if (!failure) return;
  const now = Date.now();
  if (!shouldCheckSession({ ended, checking, lastCheckAt, now })) return;
  checking = true;
  lastCheckAt = now;
  void checkSession().finally(() => { checking = false; });
}

async function checkSession(): Promise<void> {
  try {
    const res = await fetch(`${AUTH_BASE}/auth/me`, { credentials: 'include' });
    if (res.status === 401) setEnded();
  } catch {
    // The auth service cannot be reached either, which says nothing about the session.
  }
}

/** Test helper, same pattern as the stores' _reset*ForTests. */
export function _resetSessionForTests(): void {
  ended = false;
  lastCheckAt = Number.NEGATIVE_INFINITY;
  checking = false;
  listeners.clear();
}
