import { useState } from 'react';
import { useUiStore } from '../stores/uiStore';
import type { DeepResearchJob, ResearchEvent } from '../stores/deepResearchStore';
import { isActive } from '../stores/deepResearchStore';
import { cancelDeepResearch } from '../lib/api';
import { friendlyError } from '../lib/modelError';
import { doiUrl } from '../lib/doi';
import { BTN_SECONDARY, BTN_QUIET, ICON } from './ui/cardButton';

const SEARCH_ICON = <svg {...ICON}><circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" /></svg>;
const PAGE_ICON = <svg {...ICON}><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" /><polyline points="14 2 14 8 20 8" /></svg>;

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

// The source of a finding: its title (or DOI when untitled), linked to
// doi.org when the DOI is a real one.
function NoteRef({ doi, title }: { doi?: string; title?: string }) {
  const label = title || doi;
  if (!label) return null;
  const href = doiUrl(doi);
  return (
    <span className="text-[11px]">
      {' · '}
      {href
        ? <a href={href} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline wrap-anywhere">{label}</a>
        : label}
    </span>
  );
}

function derive(events: ResearchEvent[]) {
  const plan: Record<string, PlanItem> = {};
  const planOrder: string[] = [];
  const rows: ToolRow[] = [];
  const rowById: Record<string, ToolRow> = {};
  const notes: Note[] = [];
  let artifactId: string | null = null;
  let synthesising = false;
  let errorMessage: string | null = null;

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
      case 'error': errorMessage = s(e.message) || null; break;
      default: break;
    }
  }
  return { plan: planOrder.map((id) => plan[id]), rows, notes, artifactId, synthesising, errorMessage };
}

const STATUS_MARK: Record<string, string> = { open: '○', in_progress: '◐', resolved: '●', unresolvable: '×' };
const STATUS_TEXT: Record<string, string> = { open: 'Not started', in_progress: 'In progress', resolved: 'Resolved', unresolvable: 'Could not be resolved' };

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
  // Tied to the job, since the next job in this chat reuses this component.
  const [cancel, setCancel] = useState<{ jobId: string; state: 'cancelling' | 'failed' } | null>(null);
  const cancelState = cancel?.jobId === job.id ? cancel.state : 'idle';
  const { plan, rows, notes, artifactId, errorMessage } = derive(job.events);
  const active = isActive(job.status);
  const reportId = artifactId || job.artifactId;

  const handleCancel = async () => {
    const jobId = job.id;
    setCancel({ jobId, state: 'cancelling' });
    try {
      // The status changes on the next poll; until then it stays "Cancelling".
      await cancelDeepResearch(jobId);
    } catch {
      setCancel({ jobId, state: 'failed' });
    }
  };

  const openReport = () => {
    if (!reportId) return;
    setSelectedArtifactId(reportId);
    setArtifactPanelOpen(true);
  };

  return (
    // Rendered as the last item of the message transcript (see MessageList),
    // so it scrolls WITH the conversation rather than sitting pinned above the
    // composer. It therefore flows at its natural height - no internal scroll
    // or height cap, which would nest a second scrollbar inside the chat area.
    <div data-testid="research-timeline" className="mx-auto max-w-3xl w-full my-4 rounded-xl border border-border bg-bg-secondary overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-accent">
          <path d="M9 3h6M10 3v6.5L5.5 18a2 2 0 0 0 1.8 3h9.4a2 2 0 0 0 1.8-3L14 9.5V3" />
        </svg>
        <span className="text-sm font-semibold text-text-primary flex-1">Deep Research</span>
        <span data-testid="research-status" className={`text-[11px] px-2 py-0.5 rounded-full ${
          job.status === 'done' ? 'bg-success/15 text-success'
          : job.status === 'error' || job.status === 'cancelled' ? 'bg-error/15 text-error'
          : 'bg-accent/15 text-accent'}`}>
          {job.status === 'queued' ? 'queued' : active ? 'running' : job.status}
        </span>
      </div>

      {/* Body: plan + activity + findings. divide-y draws the inter-section
          rules so nothing doubles against the footer's top border. */}
      <div className="divide-y divide-border">

      {/* Plan checklist */}
      {plan.length > 0 && (
        <div className="px-4 py-3">
          <div className="text-[11px] uppercase tracking-wide text-text-secondary mb-2">Plan</div>
          <ul className="space-y-1.5">
            {plan.map((p) => {
              // Once the job has ended, nothing left open is still being worked on.
              const unfinished = !active && p.status !== 'resolved' && p.status !== 'unresolvable';
              return (
              <li key={p.id} data-testid="research-plan-item" className="flex items-start gap-2 text-sm">
                <span aria-hidden="true" className={`mt-0.5 ${p.status === 'resolved' ? 'text-success' : p.status === 'in_progress' && active ? 'text-accent' : p.status === 'unresolvable' ? 'text-error' : 'text-text-secondary'}`}>
                  {unfinished ? '○' : STATUS_MARK[p.status] || '○'}
                </span>
                <span className="sr-only">{unfinished ? 'Not finished' : STATUS_TEXT[p.status] || STATUS_TEXT.open}: </span>
                <span className={p.status === 'resolved' ? 'text-text-primary' : 'text-text-secondary'}>{p.text}</span>
              </li>
              );
            })}
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
                  <span aria-hidden="true" className="text-text-secondary shrink-0 mt-px">{r.name === 'search' ? SEARCH_ICON : PAGE_ICON}</span>
                  <span className="flex-1 min-w-0">
                    <span className="text-text-primary">{r.name === 'search' ? `Searched: ${r.query || ''}` : `Read: ${r.title || ''}`}</span>
                    {r.summary && <span className="text-text-secondary"> · {r.summary}</span>}
                    {!r.done && active && <span className="text-accent"> …</span>}
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
                <NoteRef doi={n.ref?.doi} title={n.ref?.title} />
                <TierBadge tier={n.tier} />
              </li>
            ))}
          </ul>
        </div>
      )}

      </div>{/* /body */}

      {/* Footer / report link */}
      <div className="border-t border-border px-4 py-3 flex items-center gap-2">
        {active ? (
          <span className="flex flex-wrap items-center gap-2 text-xs text-text-secondary">
            <svg className="animate-spin" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M21 12a9 9 0 1 1-6.219-8.56" /></svg>
            {job.status === 'queued' ? 'Waiting for another research job to finish…' : 'Researching in the background…'}
            <button
              data-testid="research-cancel"
              onClick={handleCancel}
              disabled={cancelState === 'cancelling'}
              className={BTN_QUIET}
            >
              {cancelState === 'cancelling' ? 'Cancelling…' : 'Cancel'}
            </button>
            {cancelState === 'failed' && (
              <span role="alert" className="text-error">Could not cancel. Try again.</span>
            )}
          </span>
        ) : reportId ? (
          <button data-testid="research-report-link" onClick={openReport} className={BTN_SECONDARY}>
            View the full report →
          </button>
        ) : job.status === 'error' ? (
          <span data-testid="research-ended" className="text-xs text-error wrap-anywhere">
            Research failed.
            {errorMessage && ` ${friendlyError(errorMessage).replace(/\.?$/, '.')}`}
            {notes.length > 0 && ' The findings above are partial.'}
            {' '}You can start Deep Research again from the + menu.
          </span>
        ) : job.status === 'cancelled' ? (
          <span data-testid="research-ended" className="text-xs text-text-secondary">
            Research cancelled.
            {notes.length > 0 && ' The findings above were gathered before it stopped.'}
          </span>
        ) : (
          <span className="text-xs text-text-secondary">Research finished.</span>
        )}
      </div>
    </div>
  );
}
