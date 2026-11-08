import { useState, type ReactNode } from 'react';
import type { Plan, PlanItem, PlanItemStatus } from '../lib/types';
import { approvePlan, rejectPlan, editPlan } from '../lib/api';
import { ErrorNotice } from './ui/ErrorNotice';
import { BTN_PRIMARY, BTN_SECONDARY, BTN_QUIET, ICON } from './ui/cardButton';
import { SpinnerIcon } from './ui/icons';

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
 *   button so the user can flip back to per-call gating. Revoking
 *   only changes a setting, so it sends no message.
 *
 * `plan` is a snapshot taken when the message was built and nothing
 * updates it afterwards, so the result of an action is held here.
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

const STATUS_ICON: Record<PlanItemStatus, ReactNode> = {
  pending: <svg {...ICON}><circle cx="12" cy="12" r="9" /></svg>,
  in_progress: <SpinnerIcon className="motion-safe:animate-spin" />,
  done: <svg {...ICON}><polyline points="20 6 9 17 4 12" /></svg>,
  cancelled: <svg {...ICON}><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg>,
};

const STATUS_LABEL: Record<PlanItemStatus, string> = {
  pending: 'pending',
  in_progress: 'in progress',
  done: 'done',
  cancelled: 'cancelled',
};

// What the user did on this card, once the request has succeeded.
type Outcome = 'approved' | 'auto' | 'rejected' | 'revoked';

function progressSummary(items: PlanItem[]): string {
  const done = items.filter(i => i.status === 'done').length;
  return `${done} / ${items.length} done`;
}

interface EditDraft {
  id: string;
  title: string;
  status: PlanItemStatus;
  notes: string;
}

// New steps need an id of their own. Left without one, the backend numbers them
// by position (p-1, p-2...), which can repeat an existing id once a step has been
// removed, and it rejects a plan with a repeated id.
function newStepId(draft: EditDraft[]): string {
  const ids = new Set(draft.map(d => d.id));
  let n = draft.length + 1;
  while (ids.has(`step-${n}`)) n++;
  return `step-${n}`;
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
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The edited steps, once saved: `plan` still holds the ones from before.
  const [savedItems, setSavedItems] = useState<PlanItem[] | null>(null);
  const [focusStepId, setFocusStepId] = useState<string | null>(null);

  if (!plan.items.length) return null;

  const items = savedItems ?? plan.items;
  const needsApproval = plan.requires_approval && !plan.approved_at && !outcome;
  const autoMode = outcome === 'auto'
    || (!outcome && plan.requires_approval && plan.approved_at && plan.approval_mode === 'auto');
  const canAct = !!conversationId;
  const titledSteps = draft.filter(d => d.title.trim().length > 0).length;

  const updateDraftTitle = (id: string, title: string) => {
    setDraft(draft.map(d => (d.id === id ? { ...d, title } : d)));
  };

  const addStep = () => {
    const id = newStepId(draft);
    setDraft([...draft, { id, title: '', status: 'pending', notes: '' }]);
    setFocusStepId(id);
  };

  const handleApprove = async (mode: 'each' | 'auto') => {
    if (!conversationId || busy) return;
    setBusy(mode === 'auto' ? 'auto' : 'approve');
    setError(null);
    try {
      await approvePlan(conversationId, mode);
      setOutcome(mode === 'auto' ? 'auto' : 'approved');
      onAfterApprove?.();
    } catch {
      setError('Could not approve the plan. Try again.');
    } finally {
      setBusy(null);
    }
  };

  const handleReject = async () => {
    if (!conversationId || busy) return;
    setBusy('reject');
    setError(null);
    try {
      await rejectPlan(conversationId);
      setOutcome('rejected');
      onAfterReject?.();
      // The next step is the user's: say what to change, in the composer.
      document.getElementById('composer-textarea')?.focus();
    } catch {
      setError('Could not reject the plan. Try again.');
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
    const edited = draft
      .map(d => ({
        id: d.id,
        title: d.title.trim(),
        status: d.status,
        notes: d.notes.trim() || null,
      }))
      .filter(d => d.title.length > 0);
    if (edited.length === 0) return;
    setBusy('edit');
    setError(null);
    try {
      await editPlan(conversationId, edited, 'each');
      setSavedItems(edited);
      setOutcome('approved');
      onAfterEdit?.();
      setEditing(false);
    } catch {
      // The editor stays open so the user can retry.
      setError('Could not save the plan. Try again.');
    } finally {
      setBusy(null);
    }
  };

  const handleRevoke = async () => {
    if (!conversationId || busy) return;
    setBusy('revoke');
    setError(null);
    try {
      await approvePlan(conversationId, 'each');
      setOutcome('revoked');
    } catch {
      setError('Could not change the approval setting. Try again.');
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
          {autoMode ? 'auto-approve on · ' : ''}{progressSummary(items)}
        </div>
      </div>

      {editing ? (
        <div className="space-y-2">
          {draft.map((d, idx) => (
            <div key={d.id} className="flex items-start gap-2">
              <span className="text-text-tertiary text-[12px] pt-1 shrink-0">
                {idx + 1}.
              </span>
              <input
                type="text"
                value={d.title}
                onChange={e => updateDraftTitle(d.id, e.target.value)}
                aria-label={`Step ${idx + 1} title`}
                autoFocus={d.id === focusStepId}
                className="flex-1 min-w-0 px-2 py-1 rounded bg-bg-tertiary border border-border text-[13px]"
                placeholder="Step title"
              />
              <button
                type="button"
                onClick={() => setDraft(draft.filter(x => x.id !== d.id))}
                aria-label={`Remove step ${idx + 1}`}
                title="Remove step"
                className="shrink-0 w-7 h-7 rounded text-text-secondary hover:text-error hover:bg-bg-tertiary cursor-pointer flex items-center justify-center"
              >
                <span aria-hidden="true">&#10005;</span>
              </button>
            </div>
          ))}
          <button
            type="button"
            onClick={addStep}
            className="text-[12px] text-accent hover:underline cursor-pointer"
          >
            + Add step
          </button>
          {titledSteps > 0 && titledSteps < draft.length && (
            <p className="text-[11px] text-text-secondary">
              Steps with no title are left out when you save.
            </p>
          )}
          <div className="flex items-center gap-2 pt-1">
            <button
              type="button"
              onClick={handleSaveEdit}
              disabled={!!busy || titledSteps === 0}
              title={titledSteps === 0 ? 'Give at least one step a title' : undefined}
              className={BTN_PRIMARY}
            >
              {busy === 'edit' ? 'Saving…' : 'Save & approve'}
            </button>
            <button
              type="button"
              onClick={() => { setError(null); setEditing(false); }}
              disabled={!!busy}
              className={BTN_SECONDARY}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <ul className="space-y-1">
          {items.map((item, idx) => (
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
              <span className="inline-flex w-3.5 h-3.5 align-[-2px] mr-1.5" aria-hidden="true">
                {STATUS_ICON[item.status]}
              </span>
              <span className="text-text-tertiary mr-1.5">{idx + 1}.</span>
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
            className={BTN_PRIMARY}
          >
            {busy === 'approve' ? 'Approving…' : 'Approve'}
          </button>
          <button
            type="button"
            onClick={() => handleApprove('auto')}
            disabled={!!busy}
            aria-label="Approve all subsequent calls"
            title="Skip future approval prompts for this conversation"
            className={BTN_SECONDARY}
          >
            {busy === 'auto' ? '…' : 'Approve all'}
          </button>
          <button
            type="button"
            onClick={() => { setDraft(toDraft(items)); setFocusStepId(null); setError(null); setEditing(true); }}
            disabled={!!busy}
            aria-label="Edit plan"
            className={BTN_SECONDARY}
          >
            Edit
          </button>
          <button
            type="button"
            onClick={handleReject}
            disabled={!!busy}
            aria-label="Reject plan"
            className={`${BTN_SECONDARY} ml-auto`}
          >
            {busy === 'reject' ? '…' : 'Reject'}
          </button>
        </div>
      )}

      {autoMode && !editing && canAct && (
        <div className="mt-2 pt-2 border-t border-border flex items-center justify-between gap-2 text-[11px]">
          <span className="text-text-tertiary">
            Munin will carry on without asking before each step.
          </span>
          <button
            type="button"
            onClick={handleRevoke}
            disabled={!!busy}
            className={`${BTN_QUIET} shrink-0`}
          >
            {busy === 'revoke' ? '…' : 'Revoke'}
          </button>
        </div>
      )}

      {error && <ErrorNotice message={error} className="mt-2" />}

      {outcome && outcome !== 'auto' && !editing && (
        <div role="status" className="mt-2 pt-2 border-t border-border text-[12px] text-text-secondary">
          {outcome === 'approved' && 'Approved'}
          {outcome === 'rejected' && 'Rejected. Tell Munin what to change.'}
          {outcome === 'revoked' && 'Asking before each step that needs approval.'}
        </div>
      )}
    </div>
  );
}
