import { useState } from 'react';
import type { MemoryProposal } from '../lib/types';
import { acceptMemoryProposal, rejectMemoryProposal } from '../lib/api';

/**
 * Renders a single auto-extracted memory candidate (P2 #25) below
 * an assistant bubble. The user can accept (POST to user_memory) or
 * dismiss (server records the key so the classifier doesn't
 * re-propose it). On either action the parent's
 * ``dismissMemoryProposal(id)`` removes the pill optimistically.
 *
 * If the REST call fails, the pill is still removed locally. The
 * proposal will re-surface on next page reload if the reject side
 * failed — that's an intentionally cheap failure mode for a
 * best-effort feature.
 */
interface Props {
  proposal: MemoryProposal;
  onDismiss: (id: string) => void;
}

export function MemoryProposalPill({ proposal, onDismiss }: Props) {
  const [busy, setBusy] = useState<'accept' | 'reject' | null>(null);

  const handleAccept = async () => {
    if (busy) return;
    setBusy('accept');
    try { await acceptMemoryProposal(proposal.id); } catch { /* noop */ }
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
          Save memory?
        </div>
        <div className="font-mono text-text-primary mt-0.5">
          <span className="text-text-secondary">{proposal.key}:</span>{' '}
          {proposal.value}
        </div>
        {proposal.reason ? (
          <div className="text-text-tertiary text-[11px] mt-0.5">
            {proposal.reason}
          </div>
        ) : null}
      </div>
      <div className="flex items-center gap-1 shrink-0">
        <button
          onClick={handleAccept}
          disabled={!!busy}
          aria-label="Accept memory"
          className="px-2 py-1 rounded text-[12px] bg-accent text-white hover:opacity-90 disabled:opacity-50 cursor-pointer"
        >
          Save
        </button>
        <button
          onClick={handleReject}
          disabled={!!busy}
          aria-label="Reject memory"
          className="px-2 py-1 rounded text-[12px] border border-border text-text-secondary hover:bg-bg-tertiary disabled:opacity-50 cursor-pointer"
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}
