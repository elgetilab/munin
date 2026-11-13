/**
 * Ctrl+K (⌘K on a Mac) opens the search-chats overlay from anywhere in the app.
 *
 * Both modifiers are taken on every platform, with two exceptions where the key
 * already means something to the text the user is in:
 * - On a Mac, Ctrl+K in a text field is the system's "delete to end of line",
 *   so there only ⌘K opens search.
 * - A key from inside another dialog belongs to that dialog: search does not
 *   open on top of Settings or the report form.
 */

/** Whether the keyboard has a ⌘ key, which takes the place of Ctrl. */
export function isApplePlatform(): boolean {
  if (typeof navigator === 'undefined') return false;
  return /Mac|iPhone|iPad|iPod/.test(navigator.platform || navigator.userAgent);
}

/** The shortcut for aria-keyshortcuts, in the form that attribute takes. */
export function searchShortcutAria(): string {
  return isApplePlatform() ? 'Meta+K' : 'Control+K';
}

/** The shortcut as the platform writes it, for a tooltip. */
export function searchShortcutLabel(): string {
  return isApplePlatform() ? '⌘K' : 'Ctrl+K';
}

function isEditable(el: Element | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  return el.isContentEditable || el.tagName === 'TEXTAREA'
    || (el.tagName === 'INPUT' && !['button', 'checkbox', 'radio', 'submit', 'reset', 'range', 'color', 'file'].includes((el as HTMLInputElement).type));
}

/** Ctrl+K or ⌘K and nothing else, wherever it was pressed. */
export function isSearchKey(e: KeyboardEvent): boolean {
  if (e.defaultPrevented || e.isComposing) return false;
  if ((e.key ?? '').toLowerCase() !== 'k' || e.shiftKey || e.altKey) return false;
  return e.ctrlKey !== e.metaKey;
}

/**
 * True when this key should open search. `home` is the element that holds the
 * overlay's opener: a dialog that contains it (the phone drawer) does not stop
 * the shortcut, any other dialog does.
 */
export function isSearchShortcut(e: KeyboardEvent, home: Element | null): boolean {
  if (!isSearchKey(e)) return false;
  const focused = document.activeElement;
  if (e.ctrlKey && isApplePlatform() && isEditable(focused)) return false;
  const dialog = focused?.closest('[aria-modal="true"]');
  if (dialog && !(home && dialog.contains(home))) return false;
  return true;
}
