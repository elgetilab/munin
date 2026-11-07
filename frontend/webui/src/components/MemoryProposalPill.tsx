import { useState } from 'react';
import type { MemoryProposal } from '../lib/types';
import { acceptMemoryProposal, rejectMemoryProposal } from '../lib/api';
import { useUiStore } from '../stores/uiStore';

// Shared look for the action buttons on the cards in a conversation (plan,
// memory, clarification, Continue, research). Kept the same in each card file;
// at least 36px tall on touch screens.
const BTN = 'inline-flex items-center justify-center px-3 py-1.5 rounded-lg text-xs font-medium transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-default pointer-coarse:min-h-9';
const BTN_PRIMARY = `${BTN} bg-accent text-bg-primary hover:bg-accent-hover`;
const BTN_SECONDARY = `${BTN} border border-border text-text-secondary hover:bg-bg-tertiary hover:text-text-primary`;

/**
 * Renders a single auto-extracted memory candidate (P2 #25) below
 * an assistant bubble. The user can accept (POST to user_memory) or
 * dismiss (server records the key so the classifier doesn't
 * re-propose it). On either action the parent's
 * ``dismissMemoryProposal(id)`` removes the pill optimistically.
 *
 * If a dismiss fails, the pill is still removed locally. The proposal
 * will re-surface on next page reload, which is an intentionally cheap
 * failure mode for a best-effort feature. A failed save is different:
 * the user would believe Munin remembers something it did not store,
 * so the pill stays and says so.
 */
interface Props {
  proposal: MemoryProposal;
  onDismiss: (id: string) => void;
}

// user_role -> User role
function humaniseKey(key: string): string {
  const words = key.replace(/[_-]+/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function MemoryProposalPill({ proposal, onDismiss }: Props) {
  const [busy, setBusy] = useState<'accept' | 'reject' | null>(null);
  const [saveFailed, setSaveFailed] = useState(false);
  const showToast = useUiStore(s => s.showToast);

  const handleAccept = async () => {
    if (busy) return;
    setBusy('accept');
    setSaveFailed(false);
    try {
      await acceptMemoryProposal(proposal.id);
    } catch {
      setSaveFailed(true);
      setBusy(null);
      return;
    }
    showToast('Saved to memory', 4000);
    onDismiss(proposal.id);
  };
  const handleReject = async () => {
    if (busy) return;
    setBusy('reject');
    try { await rejectMemoryProposal(proposal.id); } catch { /* noop */ }
    onDismiss(proposal.id);
  };

  return (
    <div className="mt-2 flex items-start gap-2 rounded-md border border-border bg-bg-secondary px-3 py-2 text-[13px]">
      <div className="flex-1 min-w-0">
        <div className="text-text-secondary text-[11px] uppercase tracking-wider">
          Remember this for future chats?
        </div>
        <div className="text-text-primary mt-0.5 wrap-anywhere">
          <span className="text-text-secondary" title={proposal.key}>{humaniseKey(proposal.key)}:</span>{' '}
          {proposal.value}
        </div>
        {proposal.reason ? (
          <div className="text-text-tertiary text-[11px] mt-0.5">
            {proposal.reason}
          </div>
        ) : null}
        {saveFailed ? (
          <div role="alert" className="text-error text-[12px] mt-1">
            Could not save. Try again.
          </div>
        ) : null}
      </div>
      <div className="flex items-center gap-1 shrink-0">
        <button
          onClick={handleAccept}
          disabled={!!busy}
          aria-label="Accept memory"
          className={BTN_PRIMARY}
        >
          Save
        </button>
        <button
          onClick={handleReject}
          disabled={!!busy}
          aria-label="Reject memory"
          className={BTN_SECONDARY}
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}
