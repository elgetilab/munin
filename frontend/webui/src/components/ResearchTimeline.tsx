import { useState } from 'react';
import { useUiStore } from '../stores/uiStore';
import type { DeepResearchJob, ResearchEvent } from '../stores/deepResearchStore';
import { isActive } from '../stores/deepResearchStore';

// Renders a Deep Research job's durable event log inline in the conversation:
// a plan checklist, a tool card per search / paper read, the notes it found, and
// a link to the finished report. Fed from the store (which is loaded + polled by
// App), so a reconnect just re-derives from the same log.

interface PlanItem { id: string; text: string; status: string }
interface ToolRow {
  id: string; name: string; query?: string; title?: string;
  summary?: string; outcome?: string; read_depth?: string; tier?: string; done: boolean;
}
interface Note { claim: string; ref?: { doi?: string; title?: string }; tier?: string }

function derive(events: ResearchEvent[]) {
  const plan: Record<string, PlanItem> = {};
  const planOrder: string[] = [];
  const rows: ToolRow[] = [];
  const rowById: Record<string, ToolRow> = {};
  const notes: Note[] = [];
  let artifactId: string | null = null;
  let synthesising = false;

  const s = (v: unknown) => (typeof v === 'string' ? v : undefined);
  for (const e of events) {
    switch (e.type) {
      case 'plan': {
        for (const it of (e.items as PlanItem[]) || []) {
          if (!plan[it.id]) planOrder.push(it.id);
          plan[it.id] = { id: it.id, text: it.text, status: it.status || 'open' };
        }
        break;
      }
      case 'plan_update': {
        const id = s(e.id);
        if (id && plan[id]) plan[id].status = s(e.status) || plan[id].status;
        else if (id) { plan[id] = { id, text: s(e.sub_question) || '', status: s(e.status) || 'open' }; if (!planOrder.includes(id)) planOrder.push(id); }
        break;
      }
      case 'tool_call': {
        const args = (e.arguments as Record<string, unknown>) || {};
        const row: ToolRow = {
          id: s(e.id) || String(rows.length), name: s(e.name) || 'tool',
          query: s(args.query), title: s(args.title) || s(args.doi), done: false,
        };
        rows.push(row);
        rowById[row.id] = row;
        break;
      }
      case 'tool_result': {
        const row = rowById[s(e.id) || ''];
        if (row) { row.summary = s(e.summary); row.outcome = s(e.outcome); row.read_depth = s(e.read_depth); row.tier = s(e.tier); row.done = true; }
        break;
      }
      case 'note': {
        notes.push({ claim: s(e.claim) || '', ref: (e.ref as Note['ref']) || undefined, tier: s(e.tier) });
        break;
      }
      case 'synthesising': synthesising = true; break;
      case 'artifact': artifactId = s(e.artifact_id) || null; break;
      default: break;
    }
  }
  return { plan: planOrder.map((id) => plan[id]), rows, notes, artifactId, synthesising };
}

const STATUS_MARK: Record<string, string> = { open: '○', in_progress: '◐', resolved: '●', unresolvable: '×' };

// Which source tier a citation came from - the breadth signal.
const TIER_LABEL: Record<string, string> = { corpus: 'corpus', oa: 'open access', web: 'web' };
function TierBadge({ tier }: { tier?: string }) {
  if (!tier || !TIER_LABEL[tier]) return null;
  return (
    <span
      data-testid="research-tier"
      data-tier={tier}
      className="ml-1 px-1 py-px rounded text-[10px] uppercase tracking-wide bg-bg-secondary text-text-secondary"
    >
      {TIER_LABEL[tier]}
    </span>
  );
}

export function ResearchTimeline({ job }: { job: DeepResearchJob }) {
  const setSelectedArtifactId = useUiStore((s) => s.setSelectedArtifactId);
  const setArtifactPanelOpen = useUiStore((s) => s.setArtifactPanelOpen);
  const [openLog, setOpenLog] = useState(true);
  const { plan, rows, notes, artifactId } = derive(job.events);
  const active = isActive(job.status);
  const reportId = artifactId || job.artifactId;

  const openReport = () => {
    if (!reportId) return;
    setSelectedArtifactId(reportId);
    setArtifactPanelOpen(true);
  };

  return (
    // Height is bounded and the body scrolls internally: a finished job can
    // carry 50+ findings and as many activity rows, and this card renders as
    // a flex sibling of the (min-h-0, collapsible) message list. Without the
    // cap the card's intrinsic height wins the flex fight and blocks the whole
    // conversation from view. Header + footer stay pinned; detail scrolls.
    <div data-testid="research-timeline" className="mx-auto max-w-3xl w-full my-4 rounded-xl border border-border bg-bg-secondary overflow-hidden flex flex-col max-h-[min(60vh,34rem)]">
      <div className="shrink-0 flex items-center gap-2 px-4 py-3 border-b border-border">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-accent">
          <path d="M9 3h6M10 3v6.5L5.5 18a2 2 0 0 0 1.8 3h9.4a2 2 0 0 0 1.8-3L14 9.5V3" />
        </svg>
        <span className="text-sm font-semibold text-text-primary flex-1">Deep Research</span>
        <span data-testid="research-status" className={`text-[11px] px-2 py-0.5 rounded-full ${
          job.status === 'done' ? 'bg-success/15 text-success'
          : job.status === 'error' || job.status === 'cancelled' ? 'bg-error/15 text-error'
          : 'bg-accent/15 text-accent'}`}>
          {active ? 'running' : job.status}
        </span>
      </div>

      {/* Scrollable body: plan + activity + findings. divide-y draws the
          inter-section rules so nothing doubles against the pinned footer. */}
      <div className="flex-1 min-h-0 overflow-y-auto divide-y divide-border">

      {/* Plan checklist */}
      {plan.length > 0 && (
        <div className="px-4 py-3">
          <div className="text-[11px] uppercase tracking-wide text-text-secondary mb-2">Plan</div>
          <ul className="space-y-1.5">
            {plan.map((p) => (
              <li key={p.id} data-testid="research-plan-item" className="flex items-start gap-2 text-sm">
                <span className={`mt-0.5 ${p.status === 'resolved' ? 'text-success' : p.status === 'in_progress' ? 'text-accent' : p.status === 'unresolvable' ? 'text-error' : 'text-text-secondary'}`}>
                  {STATUS_MARK[p.status] || '○'}
                </span>
                <span className={p.status === 'resolved' ? 'text-text-primary' : 'text-text-secondary'}>{p.text}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Activity log (search + reads) */}
      {rows.length > 0 && (
        <div className="px-4 py-3">
          <button onClick={() => setOpenLog((v) => !v)} className="text-[11px] uppercase tracking-wide text-text-secondary mb-2 cursor-pointer hover:text-text-primary">
            {openLog ? '▾' : '▸'} Activity ({rows.length})
          </button>
          {openLog && (
            <ul className="space-y-1.5">
              {rows.map((r, i) => (
                <li key={r.id + i} data-testid="research-tool-row" className="flex items-start gap-2 text-xs">
                  <span className="text-text-secondary shrink-0">{r.name === 'search' ? '🔍' : '📄'}</span>
                  <span className="flex-1 min-w-0">
                    <span className="text-text-primary">{r.name === 'search' ? `Searched: ${r.query || ''}` : `Read: ${r.title || ''}`}</span>
                    {r.summary && <span className="text-text-secondary"> · {r.summary}</span>}
                    {!r.done && <span className="text-accent"> …</span>}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* Findings */}
      {notes.length > 0 && (
        <div className="px-4 py-3">
          <div className="text-[11px] uppercase tracking-wide text-text-secondary mb-2">Findings ({notes.length})</div>
          <ul className="space-y-1.5">
            {notes.map((n, i) => (
              <li key={i} data-testid="research-note" className="text-sm text-text-secondary">
                <span className="text-text-primary">{n.claim}</span>
                {n.ref?.title && <span className="text-[11px]"> · {n.ref.title}</span>}
                <TierBadge tier={n.tier} />
              </li>
            ))}
          </ul>
        </div>
      )}

      </div>{/* /scrollable body */}

      {/* Footer / report link */}
      <div className="shrink-0 border-t border-border px-4 py-3 flex items-center gap-2">
        {active ? (
          <span className="flex items-center gap-2 text-xs text-text-secondary">
            <svg className="animate-spin" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M21 12a9 9 0 1 1-6.219-8.56" /></svg>
            Researching in the background…
          </span>
        ) : reportId ? (
          <button data-testid="research-report-link" onClick={openReport} className="text-xs text-accent hover:underline cursor-pointer">
            View the full report →
          </button>
        ) : job.status === 'error' ? (
          <span className="text-xs text-error">Research failed.</span>
        ) : (
          <span className="text-xs text-text-secondary">Research finished.</span>
        )}
      </div>
    </div>
  );
}
