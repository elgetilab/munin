/**
 * Modal behaviour for an element that draws itself: role="dialog", Escape,
 * focus moved in, trapped and returned. <Dialog> is built on it, so use that for
 * an ordinary centred dialog and this hook for a panel with its own layout.
 *
 *   const modal = useModal({ onClose, active: useBelowXl() });
 *   <div {...modal.panelProps}>
 *     <h3 id={modal.titleId}>Title</h3>
 *
 * - onClose: called on Escape. The caller closes by unmounting or by clearing active.
 * - active (default true): false makes the element an ordinary one. Pass
 *   useBelowXl() for a panel that is a full-screen overlay below xl and a side
 *   column from xl up: only the overlay is modal.
 * - initialFocusRef: what to focus on open. Otherwise an element already focused
 *   inside (autoFocus) keeps focus, then the first focusable element, then the panel.
 * - role (default 'dialog'): 'alertdialog' for a question that interrupts, such
 *   as confirming a delete.
 * - returnFocusRef: where focus goes on close instead, when it is set and still
 *   in the page, such as the next row after the one a dialog deleted.
 *
 * Focus returns to whatever had it before, if that element still exists.
 */

import { useEffect, useId, useRef, useState, useSyncExternalStore } from 'react';
import type { RefObject } from 'react';

import { isTopModal, pushLayer } from './overlayLayers';
import type { Layer } from './overlayLayers';

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  'iframe',
  'summary',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

function focusableIn(panel: HTMLElement): HTMLElement[] {
  // checkVisibility skips anything hidden by CSS, such as `xl:hidden` buttons.
  return Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE))
    .filter(el => !el.checkVisibility || el.checkVisibility());
}

export interface UseModalOptions {
  onClose: () => void;
  active?: boolean;
  initialFocusRef?: RefObject<HTMLElement | null>;
  role?: 'dialog' | 'alertdialog';
  returnFocusRef?: RefObject<HTMLElement | null>;
}

export function useModal<T extends HTMLElement = HTMLDivElement>({
  onClose,
  active = true,
  initialFocusRef,
  role = 'dialog',
  returnFocusRef,
}: UseModalOptions) {
  const panelRef = useRef<T>(null);
  const titleId = useId();
  // Read during render because a child's autoFocus has already moved focus by
  // the time any effect runs.
  const [focusedAtMount] = useState(() => document.activeElement);

  // Callers pass inline closures. Keeping the latest here stops a re-render from
  // re-running the effect below, which would pull focus back to the start.
  const onCloseRef = useRef(onClose);
  useEffect(() => { onCloseRef.current = onClose; });

  useEffect(() => {
    const panel = panelRef.current;
    if (!active || !panel) return;

    const current = document.activeElement;
    const focusIsElsewhere = current !== null && current !== document.body && !panel.contains(current);
    const opener = focusIsElsewhere ? current : focusedAtMount;

    const alreadyInside = !focusIsElsewhere && current !== document.body ? current as HTMLElement | null : null;
    (initialFocusRef?.current ?? alreadyInside ?? focusableIn(panel)[0] ?? panel).focus();

    const layer: Layer = { modal: true, onEscape: () => onCloseRef.current() };
    const removeLayer = pushLayer(layer);

    const trapTab = (e: KeyboardEvent) => {
      if (e.key !== 'Tab' || e.defaultPrevented || !isTopModal(layer)) return;
      const items = focusableIn(panel);
      if (items.length === 0) {
        e.preventDefault();
        panel.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const focused = document.activeElement;
      let next: HTMLElement | null = null;
      if (!panel.contains(focused) || focused === panel) next = e.shiftKey ? last : first;
      else if (e.shiftKey && focused === first) next = last;
      else if (!e.shiftKey && focused === last) next = first;
      if (next) {
        e.preventDefault();
        next.focus();
      }
    };
    document.addEventListener('keydown', trapTab);

    return () => {
      document.removeEventListener('keydown', trapTab);
      removeLayer();
      // Read now, not when the dialog opened: the caller sets it just before closing.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      const target = returnFocusRef?.current;
      if (target?.isConnected) target.focus();
      else if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
    };
  }, [active, focusedAtMount, initialFocusRef, returnFocusRef]);

  const panelProps = active
    ? {
        ref: panelRef,
        role,
        'aria-modal': true as const,
        'aria-labelledby': titleId,
        tabIndex: -1,
      }
    : { ref: panelRef };

  return { panelProps, titleId };
}

// Tailwind's xl breakpoint, written the way Tailwind writes it.
const BELOW_XL = '(width < 80rem)';

function subscribeBelowXl(onChange: () => void) {
  if (typeof window.matchMedia !== 'function') return () => {};
  const query = window.matchMedia(BELOW_XL);
  query.addEventListener('change', onChange);
  return () => query.removeEventListener('change', onChange);
}

function isBelowXl() {
  return typeof window.matchMedia === 'function' && window.matchMedia(BELOW_XL).matches;
}

/** True while the viewport is narrower than Tailwind's xl. */
export function useBelowXl(): boolean {
  return useSyncExternalStore(subscribeBelowXl, isBelowXl);
}
