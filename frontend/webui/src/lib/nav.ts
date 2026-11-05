/**
 * Client-side navigation helpers.
 *
 * Sidebar entries are real `<a href>` elements rather than buttons so the
 * browser can treat them like any other link: ctrl/cmd-click opens a new tab,
 * shift-click a new window, and right-click offers both in the context menu.
 * A plain left click is still handled in-app (preventDefault, then a store
 * update), so ordinary use never reloads the SPA.
 *
 * Every href below is a URL that loads correctly in a fresh tab: Caddy serves
 * the chat app with `try_files {path} /index.html`, and App.tsx resolves
 * `/c/<id>`, `/knowledge` and `?project=` on mount.
 */

/** The click-event fields that decide who handles a click. Structurally
 *  satisfied by both React.MouseEvent and a native MouseEvent. */
interface ClickModifiers {
  metaKey: boolean;
  ctrlKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
  button: number;
}

/**
 * True when the browser, not the app, should handle this click: any modifier
 * key held, or a non-primary mouse button. Call sites return early on true so
 * the anchor's default action (open in new tab / window) survives, and so the
 * current tab does NOT also navigate.
 *
 * Middle clicks arrive as `auxclick` in current browsers and never reach a
 * React onClick handler at all; the `button !== 0` arm covers anything that
 * still dispatches `click` for them.
 */
export function isModifiedClick(e: ClickModifiers): boolean {
  return e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0;
}

/** Canonical path for a saved conversation. Kept byte-identical to the
 *  pushState in App.tsx's conversation-URL effect so the address bar reads
 *  the same whether you clicked the row or opened the link directly. */
export function conversationPath(id: string): string {
  return `/c/${id}`;
}

/** Path that starts a fresh chat. With a project id, the new chat is filed
 *  into that project — App.tsx reads the param on mount and strips it. */
export function newChatPath(projectId?: string | null): string {
  return projectId ? `/?project=${encodeURIComponent(projectId)}` : '/';
}

// ── Overlays in the browser history ─────────────────────────────────────────
//
// Settings, Admin, Project settings and the phone drawer cover the page without
// changing the URL. Each still gets a history entry, marked in history.state, so
// Back closes it rather than changing the chat behind it or leaving the app.

const OVERLAY_KEY = 'muninOverlay';

export type OverlayView = 'settings' | 'admin' | 'project';

export interface OverlayState<P extends { id: string } = { id: string }> {
  view: OverlayView | null;
  // The project being edited, when view is 'project'.
  project: P | null;
  drawer: boolean;
}

interface OverlayEntry extends OverlayState {
  // How many history entries above the page these overlays account for.
  depth: number;
}

const NO_OVERLAY: OverlayEntry = { view: null, project: null, drawer: false, depth: 0 };

/** What the current history entry says is open. */
export function readOverlayEntry(): OverlayEntry {
  return (window.history.state as Record<string, OverlayEntry> | null)?.[OVERLAY_KEY] ?? NO_OVERLAY;
}

function overlayUrl(): string {
  const { pathname, search, hash } = window.location;
  return `${pathname}${search}${hash}`;
}

/**
 * Makes the history match what is open: an entry for each layer that opened, and
 * Back over an entry for one that closed some other way.
 */
export function syncOverlayHistory(wanted: OverlayState): void {
  const current = readOverlayEntry();
  const sameView = wanted.view === current.view && wanted.project?.id === current.project?.id;
  if (sameView && wanted.drawer === current.drawer) return;

  const write = (method: 'pushState' | 'replaceState', depth: number) =>
    window.history[method]({ [OVERLAY_KEY]: { ...wanted, depth } }, '', overlayUrl());

  if (!wanted.view && !wanted.drawer) {
    window.history.go(-current.depth);
  } else if (current.drawer && !wanted.drawer) {
    // Only the drawer closed: step back to what was under it. Otherwise an item
    // in the drawer opened a view, which takes over the drawer's entry.
    if (sameView) window.history.back();
    else write('replaceState', current.depth);
  } else if ((wanted.drawer && !current.drawer) || (wanted.view && !current.view)) {
    write('pushState', current.depth + 1);
  } else {
    write('replaceState', current.depth);
  }
}

/** Overlays do not survive a reload, though history.state does. */
export function dropStaleOverlayEntry(): void {
  if (readOverlayEntry().depth > 0) window.history.replaceState(null, '', overlayUrl());
}

/**
 * Goes to a page within the app. Anything open is closing because of this, so its
 * entry is reused. Back then returns to the page before, not to the closed overlay.
 */
export function pushAppPath(path: string): void {
  if (readOverlayEntry().depth > 0) window.history.replaceState(null, '', path);
  else window.history.pushState(null, '', path);
}
