/**
 * Behaviour for a menu or popover: Escape closes it and returns focus to the
 * trigger, a click, tap or focus move outside closes it.
 *
 *   const menu = useMenu();
 *   <button aria-label="More actions for X" {...menu.triggerProps}>...</button>
 *   {menu.open && (
 *     <div {...menu.menuProps}>
 *       <button role="menuitem" onClick={() => { menu.close(); rename(); }}>Rename</button>
 *     </div>
 *   )}
 *
 * - kind: 'menu' (default) moves focus to the first [role="menuitem"] on open, and
 *   adds arrow keys, Home and End. Tab closes it and moves on from the trigger.
 *   'popover' is for free content such as a form: role="dialog", no focus move,
 *   and the caller gives menuProps an aria-label.
 * - open, onOpenChange: optional, for when the parent owns the state, such as
 *   "which row's menu is open". Otherwise the hook keeps it.
 *
 * Returns { open, close, triggerProps, menuProps }. close() returns focus to the
 * trigger, so call it first when an item opens a Dialog: the Dialog then gives
 * focus back to the trigger too. A parent that closes by setting open to false
 * returns no focus. To add to the trigger's onClick, call triggerProps.onClick.
 */

import { useEffect, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';

import { pushLayer } from './overlayLayers';

export interface UseMenuOptions {
  kind?: 'menu' | 'popover';
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}

const ITEMS = '[role^="menuitem"]:not([disabled])';

export function useMenu<T extends HTMLElement = HTMLButtonElement>({
  kind = 'menu',
  open: controlledOpen,
  onOpenChange,
}: UseMenuOptions = {}) {
  const [ownOpen, setOwnOpen] = useState(false);
  const open = controlledOpen ?? ownOpen;
  const triggerRef = useRef<T>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const setOpen = (next: boolean) => {
    setOwnOpen(next);
    onOpenChange?.(next);
  };
  const close = () => {
    setOpen(false);
    triggerRef.current?.focus();
  };

  // The listeners below outlive any one render; keeping the latest functions here
  // stops a re-render from re-adding them, which would reorder the Escape stack.
  const latest = useRef({ setOpen, close });
  useEffect(() => { latest.current = { setOpen, close }; });

  useEffect(() => {
    if (!open) return;

    if (kind === 'menu') menuRef.current?.querySelector<HTMLElement>(ITEMS)?.focus();

    const removeLayer = pushLayer({ modal: false, onEscape: () => latest.current.close() });

    const closeIfOutside = (e: Event) => {
      const target = e.target as Node;
      if (menuRef.current?.contains(target) || triggerRef.current?.contains(target)) return;
      latest.current.setOpen(false);
    };
    // Capture, because a row's click handler that stops propagation would
    // otherwise hide the click from the document.
    document.addEventListener('mousedown', closeIfOutside, true);
    document.addEventListener('touchstart', closeIfOutside, true);
    document.addEventListener('focusin', closeIfOutside);

    return () => {
      removeLayer();
      document.removeEventListener('mousedown', closeIfOutside, true);
      document.removeEventListener('touchstart', closeIfOutside, true);
      document.removeEventListener('focusin', closeIfOutside);
    };
  }, [open, kind]);

  const onMenuKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    if (kind !== 'menu') return;
    if (e.key === 'Tab') {
      // Focus goes back to the trigger first, so the browser's Tab then moves on
      // from there rather than to the next item.
      close();
      return;
    }
    const items = Array.from(e.currentTarget.querySelectorAll<HTMLElement>(ITEMS));
    if (items.length === 0) return;
    const index = items.indexOf(document.activeElement as HTMLElement);
    const moves: Record<string, number> = {
      ArrowDown: (index + 1) % items.length,
      ArrowUp: (index - 1 + items.length) % items.length,
      Home: 0,
      End: items.length - 1,
    };
    const next = moves[e.key];
    if (next === undefined) return;
    e.preventDefault();
    items[next].focus();
  };

  return {
    open,
    close,
    triggerProps: {
      ref: triggerRef,
      'aria-haspopup': (kind === 'menu' ? 'menu' : 'dialog') as 'menu' | 'dialog',
      'aria-expanded': open,
      onClick: () => setOpen(!open),
    },
    menuProps: {
      ref: menuRef,
      role: (kind === 'menu' ? 'menu' : 'dialog') as 'menu' | 'dialog',
      onKeyDown: onMenuKeyDown,
    },
  };
}
