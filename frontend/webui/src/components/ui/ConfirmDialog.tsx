/**
 * Asks before a destructive action, in place of window.confirm(). Mounted means
 * open: render it conditionally.
 *
 *   {pending && (
 *     <ConfirmDialog
 *       title="Delete this chat?"
 *       confirmLabel="Delete chat"
 *       onConfirm={remove}
 *       onCancel={() => setPending(null)}
 *     >
 *       It cannot be brought back.
 *     </ConfirmDialog>
 *   )}
 *
 * An alertdialog described by its message. Focus starts on Cancel, so Enter
 * straight after opening does nothing harmful, and goes back to what had it when
 * the dialog closes (see useModal).
 *
 * - onConfirm: may return a promise. Until it settles the confirm button reads
 *   busyLabel, and neither button, Escape nor the backdrop does anything. The
 *   caller closes the dialog when it is done, whether the action worked or not.
 * - onCancel: called on Cancel, Escape and a backdrop click.
 * - returnFocusRef: where focus goes on close in place of the opener, for when
 *   the action removed the opener; see useModal.
 */

import { useId, useRef, useState } from 'react';
import type { ReactNode, RefObject } from 'react';

import { Dialog } from './Dialog';

export interface ConfirmDialogProps {
  title: ReactNode;
  children: ReactNode;
  confirmLabel: string;
  busyLabel?: string;
  onConfirm: () => unknown;
  onCancel: () => void;
  returnFocusRef?: RefObject<HTMLElement | null>;
}

export function ConfirmDialog({
  title,
  children,
  confirmLabel,
  busyLabel = 'Deleting...',
  onConfirm,
  onCancel,
  returnFocusRef,
}: ConfirmDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const descriptionId = useId();
  const [busy, setBusy] = useState(false);

  const cancel = () => {
    if (!busy) onCancel();
  };
  const confirm = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await onConfirm();
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      title={title}
      onClose={cancel}
      role="alertdialog"
      descriptionId={descriptionId}
      initialFocusRef={cancelRef}
      returnFocusRef={returnFocusRef}
      footer={(
        <>
          <button
            ref={cancelRef}
            type="button"
            onClick={cancel}
            aria-disabled={busy || undefined}
            className="px-3 py-1.5 text-sm text-text-secondary hover:text-text-primary transition-colors cursor-pointer aria-disabled:opacity-40 aria-disabled:cursor-not-allowed"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={confirm}
            aria-disabled={busy || undefined}
            className="px-3 py-1.5 bg-error text-bg-primary rounded-lg text-sm font-medium hover:opacity-90 transition-opacity cursor-pointer aria-disabled:opacity-60 aria-disabled:cursor-wait"
          >
            {busy ? busyLabel : confirmLabel}
          </button>
        </>
      )}
    >
      <p id={descriptionId} className="text-sm text-text-secondary">{children}</p>
    </Dialog>
  );
}
