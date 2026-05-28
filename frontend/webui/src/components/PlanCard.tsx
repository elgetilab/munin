import type { Plan, PlanItemStatus } from '../lib/types';

/**
 * Inline read-only checkbox list (P2 #24 Phase 1) rendered above the
 * assistant bubble that invoked set_plan or update_plan_item. Phase 2
 * extends this with Approve / Edit / Reject buttons when
 * `requires_approval` is true and `approved_at` is null.
 *
 * Notes hover-reveal via the standard title attribute — keeps the list
 * compact while still surfacing context the model attached to an item.
 */
interface Props {
  plan: Plan;
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

export function PlanCard({ plan }: Props) {
  if (!plan.items.length) return null;
  return (
    <div className="my-3 rounded-md border border-border bg-bg-secondary px-3 py-2 text-[13px]">
      <div className="flex items-center justify-between mb-1.5">
        <div className="text-[11px] uppercase tracking-wider text-text-secondary">
          Plan
        </div>
        <div className="text-[11px] text-text-tertiary">
          {progressSummary(plan)}
        </div>
      </div>
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
    </div>
  );
}
