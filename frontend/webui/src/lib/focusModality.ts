/**
 * Whether the user's last input was the keyboard, for focus a browser always
 * calls :focus-visible. A text field matches :focus-visible on a click too, so
 * a ring drawn from it alone shows for the mouse. Read this when the field takes
 * focus, as the browser's own rule for buttons does:
 *
 *   onFocus={() => setRing(lastInputWasKeyboard())}
 *
 * Keys held with Ctrl, Alt or Meta are shortcuts, not moving around, and do not
 * count. Nothing has happened yet on a fresh page, so focus the app gives the
 * field on load gets no ring.
 */
let keyboard = false;
let listening = false;

function onKeyDown(e: KeyboardEvent) {
  if (e.ctrlKey || e.altKey || e.metaKey) return;
  keyboard = true;
}

function onPointerDown() {
  keyboard = false;
}

/** Starts listening. Safe to call more than once. */
export function trackFocusModality(): void {
  if (listening || typeof document === 'undefined') return;
  listening = true;
  // Capture, so a handler that stops the event cannot hide it from here.
  document.addEventListener('keydown', onKeyDown, true);
  document.addEventListener('pointerdown', onPointerDown, true);
  document.addEventListener('mousedown', onPointerDown, true);
}

export function lastInputWasKeyboard(): boolean {
  return keyboard;
}
