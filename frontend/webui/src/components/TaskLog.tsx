import { useState, type ReactNode } from 'react';
import type { ToolCall, AgentState, RagContext } from '../lib/types';
import { doiUrl } from '../lib/doi';
import { BookIcon, FolderIcon, PageIcon, SearchIcon } from './ui/icons';

// ── SVG Icons (14x14, stroke-based, no emojis) ─────────────────────────────

const S = { width: 14, height: 14, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const };

const Icons = {
  thinking: (
    <svg {...S}><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
  ),
  papers: (
    <svg {...S}><path d="M4 19.5A2.5 2.5 0 016.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z"/></svg>
  ),
  link: (
    <svg {...S}><path d="M10 13a5 5 0 007.54.54l3-3a5 5 0 00-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 00-7.54-.54l-3 3a5 5 0 007.07 7.07l1.71-1.71"/></svg>
  ),
  globe: (
    <svg {...S}><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z"/></svg>
  ),
  edit: (
    <svg {...S}><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
  ),
  file: (
    <PageIcon />
  ),
  flask: (
    <svg {...S}><path d="M9 3h6v7l5 8H4l5-8V3z"/><line x1="9" y1="3" x2="15" y2="3"/></svg>
  ),
  folder: (
    <FolderIcon />
  ),
  sources: (
    <BookIcon />
  ),
  result: (
    <svg {...S}><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>
  ),
  gear: (
    <svg {...S}><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 01-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"/></svg>
  ),
  warning: (
    <svg {...S}><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
  ),
  magnifier: (
    <SearchIcon />
  ),
  terminal: (
    <svg {...S}><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>
  ),
  question: (
    <svg {...S}><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 015.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
  ),
  calculator: (
    <svg {...S}><rect x="4" y="2" width="16" height="20" rx="2"/><line x1="8" y1="6" x2="16" y2="6"/><line x1="8" y1="10" x2="8.01" y2="10"/><line x1="12" y1="10" x2="12.01" y2="10"/><line x1="16" y1="10" x2="16.01" y2="10"/><line x1="8" y1="14" x2="8.01" y2="14"/><line x1="12" y1="14" x2="12.01" y2="14"/><line x1="16" y1="14" x2="16.01" y2="14"/><line x1="8" y1="18" x2="16" y2="18"/></svg>
  ),
  robot: (
    <svg {...S}><rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="5" r="2"/><line x1="12" y1="7" x2="12" y2="11"/><line x1="8" y1="16" x2="8.01" y2="16"/><line x1="16" y1="16" x2="16.01" y2="16"/></svg>
  ),
  memo: (
    <svg {...S}><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
  ),
};

const TOOL_ICON_MAP: Record<string, ReactNode> = {
  paper_search: Icons.papers,
  semantic_scholar_search: Icons.papers,
  paper_lookup: Icons.papers,
  check_papers_availability: Icons.papers,
  browse_tag_papers: Icons.papers,
  source: Icons.papers,
  // Retired as tools (superseded by source), kept so persisted
  // conversations whose tool_calls still carry these names render them.
  read_paper: Icons.papers,
  compare_papers: Icons.papers,
  search: Icons.magnifier,
  tool_search: Icons.magnifier,
  s2_get_citations: Icons.link,
  s2_get_references: Icons.link,
  get_citations: Icons.link,
  get_references: Icons.link,
  get_author_papers: Icons.link,
  export_citations: Icons.link,
  web_search: Icons.globe,
  web_fetch: Icons.globe,
  llm_summarize: Icons.memo,
  get_paper_pdf: Icons.file,
  invoke_agent: Icons.robot,
  search_user_docs: Icons.folder,
  list_documents: Icons.folder,
  deep_research: Icons.magnifier,
  run_python: Icons.terminal,
  edit_python: Icons.edit,
  compute: Icons.terminal,
  sandbox_reset: Icons.terminal,
  compile_latex: Icons.file,
  ask_clarification: Icons.question,
  set_plan: Icons.result,
  update_plan_item: Icons.edit,
  calculate: Icons.calculator,
  faq: Icons.question,
  remember: Icons.edit,
  forget: Icons.edit,
  recall: Icons.edit,
  create_artifact: Icons.file,
  read_artifact: Icons.file,
  update_artifact: Icons.edit,
  list_artifacts: Icons.result,
  show_protein: Icons.file,
  save_artifact_to_documents: Icons.folder,
  list_projects: Icons.folder,
  get_current_project: Icons.folder,
  search_past_conversations: Icons.magnifier,
  view_attachment: Icons.file,
  transcribe_equation: Icons.calculator,
};

function getIcon(name: string): ReactNode {
  return TOOL_ICON_MAP[name] || Icons.gear;
}

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? '' : 's'}`;
}

// A tool reports failure by returning { error: ... }.
function isFailedResult(result: unknown): boolean {
  return typeof result === 'object' && result !== null && !!(result as Record<string, unknown>).error;
}

function formatResult(result: unknown): string {
  if (!result) return '';
  if (isFailedResult(result)) return 'failed';
  if (typeof result === 'object' && result !== null) {
    const r = result as Record<string, unknown>;
    if (Array.isArray(r.papers)) return plural(r.papers.length, 'paper');
    if (Array.isArray(r.results)) return plural(r.results.length, 'result');
    if (typeof r.content === 'string') return plural(Math.round(r.content.length / 5), 'word');
  }
  return 'done';
}

function ExternalLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline wrap-anywhere">
      {children}
    </a>
  );
}

function Doi({ doi }: { doi: string }) {
  const href = doiUrl(doi);
  return href ? <ExternalLink href={href}>{doi}</ExternalLink> : <>{doi}</>;
}

interface ResultItem { title: string; href: string | null; year: string | null }

// Papers or search hits as a short title list, or null when the result
// is not shaped like that (then only the raw JSON is shown).
function resultItems(result: unknown): ResultItem[] | null {
  if (typeof result !== 'object' || result === null || isFailedResult(result)) return null;
  const r = result as Record<string, unknown>;
  const list = Array.isArray(r.papers) ? r.papers : Array.isArray(r.results) ? r.results : null;
  if (!list) return null;
  const items: ResultItem[] = [];
  for (const entry of list) {
    if (typeof entry !== 'object' || entry === null) continue;
    const e = entry as Record<string, unknown>;
    const title = typeof e.title === 'string' && e.title.trim() ? e.title.trim() : null;
    if (!title) continue;
    const url = typeof e.url === 'string' && /^https?:\/\//i.test(e.url) ? e.url : null;
    const year = typeof e.year === 'number' || (typeof e.year === 'string' && e.year) ? String(e.year) : null;
    items.push({ title, href: doiUrl(e.doi) ?? url, year });
  }
  return items.length > 0 ? items : null;
}

function ResultBody({ result }: { result: unknown }) {
  const [showRaw, setShowRaw] = useState(false);
  const raw = <pre className="text-text-secondary whitespace-pre-wrap wrap-anywhere">{JSON.stringify(result, null, 2)}</pre>;
  const items = resultItems(result);
  if (!items) return raw;
  return (
    <>
      <ol className="list-decimal ml-4 space-y-1 font-sans text-text-primary">
        {items.map((item, i) => (
          <li key={i}>
            {item.href ? <ExternalLink href={item.href}>{item.title}</ExternalLink> : item.title}
            {item.year && <span className="text-text-secondary"> ({item.year})</span>}
          </li>
        ))}
      </ol>
      <button
        type="button"
        aria-expanded={showRaw}
        onClick={() => setShowRaw(!showRaw)}
        className="mt-2 text-text-secondary hover:text-accent cursor-pointer"
      >
        {showRaw ? 'Hide raw' : 'Show raw'}
      </button>
      {showRaw && <div className="mt-1">{raw}</div>}
    </>
  );
}

function ArgumentsBody({ call }: { call: ToolCall }) {
  const code = call.name === 'run_python' && typeof call.arguments.code === 'string' ? call.arguments.code : null;
  if (code === null) {
    return <pre className="text-text-secondary whitespace-pre-wrap wrap-anywhere">{JSON.stringify(call.arguments, null, 2)}</pre>;
  }
  const rest = Object.fromEntries(Object.entries(call.arguments).filter(([k]) => k !== 'code'));
  return (
    <>
      <pre className="text-text-primary bg-bg-secondary rounded p-2 overflow-x-auto"><code>{code}</code></pre>
      {Object.keys(rest).length > 0 && (
        <pre className="mt-1 text-text-secondary whitespace-pre-wrap wrap-anywhere">{JSON.stringify(rest, null, 2)}</pre>
      )}
    </>
  );
}

const STOP_REASON_LABELS: Record<string, { label: string; warn: boolean }> = {
  done: { label: 'Completed', warn: false },
  max_iterations: { label: 'Stopped: max iterations', warn: true },
  max_tool_calls: { label: 'Stopped: max tool calls', warn: true },
  timeout: { label: 'Stopped: timeout', warn: true },
  error: { label: 'Stopped: error', warn: true },
};

interface TaskLogProps {
  thinking?: string | null;
  toolCalls?: ToolCall[] | null;
  ragContext?: RagContext | null;
  thinkingDuration?: number;
  isStreaming?: boolean;
}

export function TaskLog({ thinking, toolCalls, ragContext, thinkingDuration, isStreaming }: TaskLogProps) {
  const hasContent = thinking || (toolCalls && toolCalls.length > 0) || ragContext;
  if (!hasContent) return null;

  return (
    <div className="bg-bg-secondary border border-border rounded-xl p-3 mb-3 text-xs font-mono">
      {ragContext && ragContext.documents.length > 0 && (
        <RagContextRow context={ragContext} />
      )}
      {thinking && (
        <ThinkingRow
          content={thinking}
          duration={thinkingDuration}
          isActive={isStreaming && !toolCalls?.length}
        />
      )}
      {toolCalls?.map((tc, i) => (
        tc.name === 'invoke_agent' && tc.agent
          ? <AgentBlock key={tc.id || i} call={tc} agent={tc.agent} isActive={isStreaming && i === toolCalls.length - 1 && !tc.result} />
          : <ToolCallRow key={tc.id || i} call={tc} isActive={isStreaming && i === toolCalls.length - 1 && !tc.result} />
      ))}
    </div>
  );
}

function ThinkingRow({ content, duration, isActive }: { content: string; duration?: number; isActive?: boolean }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="border-b border-border last:border-0 py-1.5">
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
        className={`w-full text-left flex items-center gap-2 cursor-pointer hover:text-accent transition-colors ${isActive ? 'text-text-primary' : 'text-text-secondary'}`}
      >
        <span className="flex-shrink-0">{Icons.thinking}</span>
        <span className="flex-1">
          {isActive ? 'Reasoning...' : `Reasoned${duration ? ` for ${(duration / 1000).toFixed(1)}s` : ''}`}
        </span>
        <span className="text-text-secondary">{expanded ? '\u25BE' : '\u25B8'}</span>
      </button>
      {expanded && (
        <div className="mt-2 ml-6 p-3 bg-bg-tertiary rounded-lg text-text-secondary whitespace-pre-wrap wrap-anywhere text-[11px] leading-relaxed max-h-48 overflow-y-auto">
          {content}
        </div>
      )}
    </div>
  );
}

function ToolCallRow({ call, isActive }: { call: ToolCall; isActive?: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const icon = getIcon(call.name);
  const resultSummary = call.result ? formatResult(call.result) : null;

  return (
    <div className="border-b border-border last:border-0 py-1.5">
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
        className={`w-full text-left flex items-center gap-2 cursor-pointer hover:text-accent transition-colors ${isActive ? 'text-text-primary' : 'text-text-secondary'}`}
      >
        <span className="flex-shrink-0">{icon}</span>
        <span className="flex-1">
          {call.name.replace(/_/g, ' ')}
          {call.arguments.query ? `: "${call.arguments.query}"` : ''}
        </span>
        {isActive && !resultSummary && (
          <span className="text-accent animate-pulse">...</span>
        )}
        {resultSummary && (isFailedResult(call.result) ? (
          <span className="text-warning flex items-center gap-1">
            <span className="flex-shrink-0">{Icons.warning}</span>
            {resultSummary}
          </span>
        ) : (
          <span className="text-text-secondary">{'\u2192'} {resultSummary}</span>
        ))}
        {call.duration_ms && (
          <span className="text-text-secondary ml-1">{(call.duration_ms / 1000).toFixed(1)}s</span>
        )}
        <span className="text-text-secondary">{expanded ? '\u25BE' : '\u25B8'}</span>
      </button>
      {expanded && (
        <div className="mt-2 ml-6 space-y-2">
          <div className="p-3 bg-bg-tertiary rounded-lg text-[11px] max-h-48 overflow-y-auto">
            <div className="text-text-secondary mb-1 font-semibold">Arguments</div>
            <ArgumentsBody call={call} />
          </div>
          {call.result != null && (
            <div className="p-3 bg-bg-tertiary rounded-lg text-[11px] max-h-48 overflow-y-auto">
              <div className="text-text-secondary mb-1 font-semibold">Result</div>
              <ResultBody result={call.result} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function AgentBlock({ call, agent, isActive }: { call: ToolCall; agent: AgentState; isActive?: boolean }) {
  const [expanded, setExpanded] = useState(true);
  const isDone = agent.stoppedReason != null;
  const stopInfo = agent.stoppedReason ? STOP_REASON_LABELS[agent.stoppedReason] || { label: agent.stoppedReason, warn: true } : null;

  return (
    <div className="border-b border-border last:border-0 py-1.5">
      {/* Agent header */}
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
        className={`w-full text-left flex items-center gap-2 cursor-pointer hover:text-accent transition-colors ${isActive ? 'text-text-primary' : 'text-text-secondary'}`}
      >
        <span className="flex-shrink-0">{Icons.flask}</span>
        <span className="flex-1 font-medium">
          {agent.name.replace(/_/g, ' ')}
          {agent.query ? `: "${agent.query}"` : ''}
        </span>
        {isActive && !isDone && (
          <span className="text-accent animate-pulse">running...</span>
        )}
        {isDone && agent.durationSeconds != null && (
          <span className="text-text-secondary">
            {agent.durationSeconds.toFixed(0)}s · {agent.toolCalls.length} tool{agent.toolCalls.length !== 1 ? 's' : ''}
          </span>
        )}
        <span className="text-text-secondary">{expanded ? '\u25BE' : '\u25B8'}</span>
      </button>

      {/* Expanded agent content */}
      {expanded && (
        <div className="ml-4 mt-1 border-l-2 border-border pl-3">
          {/* Agent thinking */}
          {agent.thinking && (
            <ThinkingRow
              content={agent.thinking}
              isActive={isActive && !isDone && agent.toolCalls.length === 0}
            />
          )}

          {/* Agent tool calls */}
          {agent.toolCalls.map((atc, i) => (
            <ToolCallRow
              key={atc.id || i}
              call={atc}
              isActive={isActive && !isDone && i === agent.toolCalls.length - 1 && !atc.result}
            />
          ))}

          {/* Stop reason */}
          {stopInfo && stopInfo.warn && (
            <div className="py-1.5 text-warning flex items-center gap-2">
              <span className="flex-shrink-0">{Icons.warning}</span>
              <span>{stopInfo.label}</span>
            </div>
          )}

          {/* Final result from invoke_agent tool_result */}
          {isDone && call.result != null && (
            <AgentResultRow result={call.result} />
          )}
        </div>
      )}
    </div>
  );
}

function RagContextRow({ context }: { context: RagContext }) {
  const [expanded, setExpanded] = useState(false);
  const sourceCount = context.sources_used.length;

  return (
    <div className="border-b border-border last:border-0 py-1.5">
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
        className="w-full text-left flex items-center gap-2 cursor-pointer hover:text-accent transition-colors text-text-secondary"
      >
        <span className="flex-shrink-0">{Icons.sources}</span>
        <span className="flex-1">
          Retrieved from {sourceCount} source{sourceCount !== 1 ? 's' : ''}
          {context.sources_used.length > 0 && (
            <span className="ml-1 text-text-secondary">({context.sources_used.join(', ')})</span>
          )}
        </span>
        <span className="text-text-secondary">{expanded ? '\u25BE' : '\u25B8'}</span>
      </button>
      {expanded && (
        <div className="mt-2 ml-6 space-y-1.5">
          {context.documents.map((doc, i) => (
            <div key={i} className="p-2.5 bg-bg-tertiary rounded-lg text-[11px]">
              <div className="text-text-primary font-medium">{doc.title}</div>
              <div className="text-text-secondary mt-0.5">
                {doc.source}
                {doc.doi && (
                  <span>
                    {' · DOI: '}
                    <Doi doi={doc.doi} />
                  </span>
                )}
                {doc.score != null && <span> · Score: {doc.score.toFixed(2)}</span>}
              </div>
              {doc.content && (
                <div className="text-text-secondary mt-1 line-clamp-2">{doc.content}</div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function AgentResultRow({ result }: { result: unknown }) {
  const [expanded, setExpanded] = useState(false);
  const summary = formatResult(result);
  const failed = isFailedResult(result);

  return (
    <div className="py-1.5">
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
        className={`w-full text-left flex items-center gap-2 cursor-pointer hover:text-accent transition-colors ${failed ? 'text-warning' : 'text-text-secondary'}`}
      >
        <span className="flex-shrink-0">{failed ? Icons.warning : Icons.result}</span>
        <span className="flex-1">Agent result{summary ? `: ${summary}` : ''}</span>
        <span className="text-text-secondary">{expanded ? '\u25BE' : '\u25B8'}</span>
      </button>
      {expanded && (
        <div className="mt-2 ml-6 p-3 bg-bg-tertiary rounded-lg text-[11px] max-h-48 overflow-y-auto">
          <ResultBody result={result} />
        </div>
      )}
    </div>
  );
}
