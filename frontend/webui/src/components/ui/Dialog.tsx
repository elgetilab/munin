/**
 * A centred modal dialog. Mounted means open: render it conditionally. It portals
 * to document.body, so an ancestor's stacking context can't cover it.
 *
 *   {open && (
 *     <Dialog title="Add user" onClose={close} footer={<button>Save</button>}>
 *       fields
 *     </Dialog>
 *   )}
 *
 * - title: labels the dialog.
 * - onClose: called on Escape and on a backdrop click.
 * - footer: stays on-screen while the body scrolls. For Enter to submit, put
 *   <form id="x"> in children and <button type="submit" form="x"> in footer.
 * - closeOnBackdrop (default true): pass false while a form has unsaved input.
 * - initialFocusRef: what to focus on open, see useModal.
 *
 * For a panel with its own layout, use useModal directly.
 */

import { useRef } from 'react';
import type { MouseEvent, ReactNode, RefObject } from 'react';
import { createPortal } from 'react-dom';

import { useModal } from '../../hooks/useModal';
import { DIALOG_BACKDROP } from './themeClasses';

export interface DialogProps {
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  closeOnBackdrop?: boolean;
  initialFocusRef?: RefObject<HTMLElement | null>;
}

export function Dialog({
  title,
  onClose,
  children,
  footer,
  closeOnBackdrop = true,
  initialFocusRef,
}: DialogProps) {
  const { panelProps, titleId } = useModal({ onClose, initialFocusRef });

  // A click must also start on the backdrop: dragging a text selection out of a
  // field and letting go over the backdrop would otherwise discard the form.
  const pressedBackdrop = useRef(false);
  const onBackdropMouseDown = (e: MouseEvent) => {
    pressedBackdrop.current = e.target === e.currentTarget;
  };
  const onBackdropClick = (e: MouseEvent) => {
    if (closeOnBackdrop && pressedBackdrop.current && e.target === e.currentTarget) onClose();
    pressedBackdrop.current = false;
  };

  return createPortal(
    <div
      data-testid="dialog-backdrop"
      className={`fixed inset-0 z-50 flex items-center justify-center p-4 ${DIALOG_BACKDROP}`}
      onMouseDown={onBackdropMouseDown}
      onClick={onBackdropClick}
    >
      <div
        {...panelProps}
        className="w-full max-w-md max-h-full flex flex-col bg-bg-secondary border border-border rounded-xl shadow-2xl overflow-hidden"
      >
        <h2 id={titleId} className="flex-shrink-0 px-5 py-4 border-b border-border text-sm font-semibold text-text-primary">
          {title}
        </h2>
        <div className="flex-1 min-h-0 overflow-y-auto px-5 py-4">{children}</div>
        {footer && (
          <div className="flex-shrink-0 flex justify-end gap-2 px-5 py-3 border-t border-border">{footer}</div>
        )}
      </div>
    </div>,
    document.body,
  );
}
