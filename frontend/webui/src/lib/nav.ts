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
