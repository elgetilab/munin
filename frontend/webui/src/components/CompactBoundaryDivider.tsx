import { useState } from 'react';
import type { CompactBoundary } from '../lib/types';

/**
 * Renders a horizontal divider above the assistant turn that
 * triggered compaction (P2 #22). The divider says "earlier N
 * messages summarised" and reveals the full summary text on click
 * so the user knows what context the model is actually seeing.
 */
export function CompactBoundaryDivider({ boundary }: { boundary: CompactBoundary }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="my-3 select-none">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-2 text-[11px] uppercase tracking-wider text-text-tertiary hover:text-text-secondary cursor-pointer"
        aria-expanded={open}
        aria-label="Show conversation summary"
      >
        <span className="h-px flex-1 bg-border" />
        <span className="px-2">
          earlier {boundary.dropped_messages} messages summarised
          {boundary.is_fresh ? ' (just now)' : ''}
        </span>
        <span className="h-px flex-1 bg-border" />
      </button>
      {open && (
        <div className="mt-2 px-3 py-2 rounded-md border border-border bg-bg-secondary text-[12px] text-text-secondary whitespace-pre-wrap">
          {boundary.summary}
        </div>
      )}
    </div>
  );
}
