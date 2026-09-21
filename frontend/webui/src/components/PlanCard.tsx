import { useState } from 'react';
import type { Plan, PlanItem, PlanItemStatus } from '../lib/types';
import { approvePlan, rejectPlan, editPlan } from '../lib/api';

/**
 * Inline plan card (P2 #24).
 *
 * Phase 1: read-only checkbox list, status icons, progress count.
 * Phase 2: when `requires_approval && !approved_at`, the card shows
 *   Approve / Approve-all / Edit / Reject buttons. On Approve, the
 *   parent's `onAfterApprove` callback is invoked AFTER the REST
 *   call resolves so the chat can send a synthetic
 *   `"I've approved the plan, please continue."` user message and
 *   the model resumes. Auto-mode renders an indicator + revoke
 *   button so the user can flip back to per-call gating.
 */
interface Props {
  plan: Plan;
  // Phase 2 wiring; both optional so Phase 1 read-only render still
  // works in tests + on conversations where the persona doesn't gate.
  conversationId?: string | null;
  onAfterApprove?: () => void;
  onAfterReject?: () => void;
  onAfterEdit?: () => void;
}

const STATUS_ICON: Record<PlanItemStatus, string> = {
  pending: '⬜',
  in_progress: '🔄',
  done: '✅',
  cancelled: '✖',
};

const STATUS_LABEL: Record<PlanItemStatus, string> = {
  pending: 'pending',
  in_progress: 'in progress',
  done: 'done',
  cancelled: 'cancelled',
};

function progressSummary(plan: Plan): string {
  const done = plan.items.filter(i => i.status === 'done').length;
  return `${done} / ${plan.items.length} done`;
}

interface EditDraft {
  id: string;
  title: string;
  status: PlanItemStatus;
  notes: string;
}

function toDraft(items: PlanItem[]): EditDraft[] {
  return items.map(it => ({
    id: it.id,
    title: it.title,
    status: it.status,
    notes: it.notes || '',
  }));
}

export function PlanCard({
  plan,
  conversationId,
  onAfterApprove,
  onAfterReject,
  onAfterEdit,
}: Props) {
  const [busy, setBusy] = useState<'approve' | 'auto' | 'reject' | 'edit' | 'revoke' | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<EditDraft[]>(() => toDraft(plan.items));

  if (!plan.items.length) return null;

  const needsApproval = plan.requires_approval && !plan.approved_at;
  const autoMode = plan.requires_approval && plan.approved_at && plan.approval_mode === 'auto';
  const canAct = !!conversationId;

  const handleApprove = async (mode: 'each' | 'auto') => {
    if (!conversationId || busy) return;
    setBusy(mode === 'auto' ? 'auto' : 'approve');
    try {
      await approvePlan(conversationId, mode);
      onAfterApprove?.();
    } catch {
      // best-effort; the parent's afterApprove typically retries
    } finally {
      setBusy(null);
    }
  };

  const handleReject = async () => {
    if (!conversationId || busy) return;
    setBusy('reject');
    try {
      await rejectPlan(conversationId);
      onAfterReject?.();
    } catch {
      /* noop */
    } finally {
      setBusy(null);
    }
  };

  const handleSaveEdit = async () => {
    if (!conversationId || busy) return;
    // Built field by field rather than spreading the draft: the spread
    // produced a fresh object literal, which excess-property checking
    // rejected against editPlan's narrower parameter, and the `as any`
    // that silenced it also switched off every other check on this call.
    const items = draft
      .map(d => ({
        id: d.id,
        title: d.title.trim(),
        status: d.status,
        notes: d.notes.trim() || null,
      }))
      .filter(d => d.title.length > 0);
    if (items.length === 0) {
      setEditing(false);
      return;
    }
    setBusy('edit');
    try {
      await editPlan(conversationId, items, 'each');
      onAfterEdit?.();
      setEditing(false);
    } catch {
      /* leave the editor open so the user can retry */
    } finally {
      setBusy(null);
    }
  };

  const handleRevoke = async () => {
    if (!conversationId || busy) return;
    setBusy('revoke');
    try {
      await approvePlan(conversationId, 'each');
      onAfterEdit?.();
    } catch {
      /* noop */
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="my-3 rounded-md border border-border bg-bg-secondary px-3 py-2 text-[13px]">
      <div className="flex items-center justify-between mb-1.5">
        <div className="text-[11px] uppercase tracking-wider text-text-secondary">
          {needsApproval ? '⚠ Plan — approval required' : 'Plan'}
        </div>
        <div className="text-[11px] text-text-tertiary">
          {autoMode ? 'auto-approve on · ' : ''}{progressSummary(plan)}
        </div>
      </div>

      {editing ? (
        <div className="space-y-2">
          {draft.map((d, idx) => (
            <div key={d.id} className="flex items-start gap-2">
              <span className="text-text-tertiary text-[12px] pt-1 shrink-0 font-mono">
                {d.id}:
              </span>
              <input
                type="text"
                value={d.title}
                onChange={e => {
                  const next = draft.slice();
                  next[idx] = { ...next[idx], title: e.target.value };
                  setDraft(next);
                }}
                className="flex-1 px-2 py-1 rounded bg-bg-tertiary border border-border text-[13px]"
                placeholder="Item title"
              />
            </div>
          ))}
          <div className="flex items-center gap-2 pt-1">
            <button
              type="button"
              onClick={handleSaveEdit}
              disabled={!!busy}
              className="px-2 py-1 rounded text-[12px] bg-accent text-white hover:opacity-90 disabled:opacity-50 cursor-pointer"
            >
              {busy === 'edit' ? 'Saving…' : 'Save & approve'}
            </button>
            <button
              type="button"
              onClick={() => { setDraft(toDraft(plan.items)); setEditing(false); }}
              disabled={!!busy}
              className="px-2 py-1 rounded text-[12px] border border-border text-text-secondary hover:bg-bg-tertiary disabled:opacity-50 cursor-pointer"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <ul className="space-y-1">
          {plan.items.map(item => (
            <li
              key={item.id}
              className={
                item.status === 'done'
                  ? 'text-text-tertiary line-through'
                  : item.status === 'in_progress'
                  ? 'text-text-primary'
                  : item.status === 'cancelled'
                  ? 'text-text-tertiary line-through opacity-60'
                  : 'text-text-secondary'
              }
              title={item.notes || STATUS_LABEL[item.status]}
              aria-label={`${STATUS_LABEL[item.status]}: ${item.title}`}
            >
              <span className="mr-1.5" aria-hidden="true">
                {STATUS_ICON[item.status]}
              </span>
              <span className="text-text-tertiary mr-1.5">{item.id}:</span>
              {item.title}
            </li>
          ))}
        </ul>
      )}

      {needsApproval && !editing && canAct && (
        <div className="mt-2 pt-2 border-t border-border flex items-center gap-1.5 flex-wrap">
          <button
            type="button"
            onClick={() => handleApprove('each')}
            disabled={!!busy}
            aria-label="Approve plan"
            className="px-2 py-1 rounded text-[12px] bg-accent text-white hover:opacity-90 disabled:opacity-50 cursor-pointer"
          >
            {busy === 'approve' ? 'Approving…' : 'Approve'}
          </button>
          <button
            type="button"
            onClick={() => handleApprove('auto')}
            disabled={!!busy}
            aria-label="Approve all subsequent calls"
            title="Skip future approval prompts for this conversation"
            className="px-2 py-1 rounded text-[12px] border border-border text-text-secondary hover:bg-bg-tertiary disabled:opacity-50 cursor-pointer"
          >
            {busy === 'auto' ? '…' : 'Approve all'}
          </button>
          <button
            type="button"
            onClick={() => setEditing(true)}
            disabled={!!busy}
            aria-label="Edit plan"
            className="px-2 py-1 rounded text-[12px] border border-border text-text-secondary hover:bg-bg-tertiary disabled:opacity-50 cursor-pointer"
          >
            Edit
          </button>
          <button
            type="button"
            onClick={handleReject}
            disabled={!!busy}
            aria-label="Reject plan"
            className="px-2 py-1 rounded text-[12px] border border-border text-text-secondary hover:bg-bg-tertiary disabled:opacity-50 cursor-pointer ml-auto"
          >
            {busy === 'reject' ? '…' : 'Reject'}
          </button>
        </div>
      )}

      {autoMode && !editing && canAct && (
        <div className="mt-2 pt-2 border-t border-border flex items-center justify-between text-[11px]">
          <span className="text-text-tertiary">
            Subsequent gated tool calls run without prompting.
          </span>
          <button
            type="button"
            onClick={handleRevoke}
            disabled={!!busy}
            className="text-text-secondary underline hover:text-text-primary disabled:opacity-50 cursor-pointer"
          >
            {busy === 'revoke' ? '…' : 'revoke'}
          </button>
        </div>
      )}
    </div>
  );
}
