import { useSyncExternalStore } from 'react';
import { isSessionEnded, signInAgainUrl, subscribeSession } from '../lib/session';

/**
 * Says that the sign-in session has ended (lib/session.ts) and links to the
 * login page, which brings the user back here. It stays until the page is left,
 * and never navigates on its own: there may be a draft still to copy.
 */
export function SessionNotice() {
  const ended = useSyncExternalStore(subscribeSession, isSessionEnded);
  if (!ended) return null;
  return (
    <div
      role="alert"
      data-testid="session-notice"
      className="fixed top-3 left-1/2 -translate-x-1/2 z-[70] w-[calc(100%-2rem)] max-w-lg px-4 py-3 bg-bg-secondary border border-warning rounded-lg shadow-lg text-sm text-text-primary flex flex-wrap items-center gap-x-3 gap-y-2"
    >
      <span className="flex-1 min-w-0">
        Your session has ended. Copy anything you have not sent, then sign in again.
      </span>
      <a
        href={signInAgainUrl()}
        // The address may have changed since this rendered.
        onClick={(e) => { e.currentTarget.href = signInAgainUrl(); }}
        className="flex-shrink-0 px-3 py-1 bg-accent text-bg-primary rounded-md text-xs font-medium no-underline hover:bg-accent-hover transition-colors"
      >
        Sign in again
      </a>
    </div>
  );
}
