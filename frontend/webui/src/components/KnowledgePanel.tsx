import { useMemo, useRef, useState } from 'react';
import type { MouseEvent, ReactNode } from 'react';
import type { TagCatalog, TagChip } from '../lib/types';
import { isModifiedClick } from '../lib/nav';
import { useUiStore } from '../stores/uiStore';
import { useBelowXl, useModal } from '../hooks/useModal';

interface KnowledgePanelProps {
  catalog: TagCatalog;
  activeTags: TagChip[];
  onTagsChange: (tags: TagChip[]) => void;
  // P2 #26 commit 2: onClose dropped; pulled from uiStore directly.
}

// Topics are a long list. Above this many, a filter beats paging through them.
const TOPIC_FILTER_THRESHOLD = 20;

function CloseIcon({ size = 12 }: { size?: number }) {
  return (
    <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  );
}

function Section({ title, defaultOpen, children }: { title: string; defaultOpen?: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(defaultOpen ?? false);
  return (
    <div className="border-b border-border last:border-b-0">
      <button
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-4 py-3 text-sm font-medium text-text-primary hover:bg-bg-tertiary transition-colors cursor-pointer"
      >
        {title}
        <svg
          aria-hidden="true"
          width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
          className={`transition-transform ${open ? 'rotate-180' : ''}`}
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>
      {open && <div className="px-4 pb-3">{children}</div>}
    </div>
  );
}

function TagRow({ label, count, active, activeClass, title, onToggle }: {
  label: string;
  count: number;
  active: boolean;
  activeClass: string;
  title?: string;
  onToggle: () => void;
}) {
  // An active row stays usable, so a filter on an emptied tag can still be removed.
  const disabled = count === 0 && !active;
  return (
    <button
      aria-pressed={active}
      disabled={disabled}
      onClick={onToggle}
      title={disabled ? 'No papers uploaded yet' : title}
      className={`flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-xs text-left transition-colors ${
        active ? `${activeClass} cursor-pointer` :
        disabled ? 'text-text-secondary/50 cursor-not-allowed' :
        'text-text-secondary hover:bg-bg-tertiary hover:text-text-primary cursor-pointer'
      }`}
    >
      {active && (
        <svg aria-hidden="true" data-testid="tag-check" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" className="flex-shrink-0">
          <polyline points="20 6 9 17 4 12" />
        </svg>
      )}
      <span className="flex-1 truncate">{label}</span>
      <span className="text-[10px] tabular-nums">{count.toLocaleString()}</span>
    </button>
  );
}

export function KnowledgePanel({ catalog, activeTags, onTagsChange }: KnowledgePanelProps) {
  const setKnowledgePanelOpen = useUiStore(s => s.setKnowledgePanelOpen);
  const setShowKnowledgePage = useUiStore(s => s.setShowKnowledgePage);
  const onClose = () => setKnowledgePanelOpen(false);
  const [topicLimit, setTopicLimit] = useState(10);
  const [topicQuery, setTopicQuery] = useState('');
  const activeKeys = new Set(activeTags.map(t => `${t.kind}:${t.value}`));

  const closeRef = useRef<HTMLButtonElement>(null);
  // A full-screen overlay below xl, so modal there; a plain side column from xl up.
  const modal = useModal({ onClose, active: useBelowXl(), initialFocusRef: closeRef });

  const toggleTag = (kind: TagChip['kind'], value: string) => {
    const key = `${kind}:${value}`;
    if (activeKeys.has(key)) {
      onTagsChange(activeTags.filter(t => `${t.kind}:${t.value}` !== key));
    } else {
      onTagsChange([...activeTags, { kind, value }]);
    }
  };

  const tagName = (tag: TagChip): string => {
    if (tag.kind === 'group') return catalog.groups.find(g => g.slug === tag.value)?.display_name ?? tag.value;
    if (tag.kind === 'contributor') return catalog.contributors.find(c => c.username === tag.value)?.display_name ?? tag.value;
    return catalog.topics.find(t => t.slug === tag.value)?.label ?? tag.value;
  };

  const query = topicQuery.trim().toLowerCase();
  const shownTopics = useMemo(
    () => (query ? catalog.topics.filter(t => t.label.toLowerCase().includes(query)) : catalog.topics.slice(0, topicLimit)),
    [catalog.topics, query, topicLimit],
  );

  const nothingUploaded = ![...catalog.groups, ...catalog.contributors, ...catalog.topics].some(item => item.paper_count > 0);

  const openKnowledgePage = (e: MouseEvent) => {
    if (isModifiedClick(e)) return;
    e.preventDefault();
    onClose();
    setShowKnowledgePage(true);
    window.history.pushState(null, '', '/knowledge');
  };

  return (
    <div {...modal.panelProps} data-testid="knowledge-panel" className="fixed inset-0 z-40 w-full flex flex-col bg-bg-secondary xl:static xl:z-auto xl:w-72 xl:flex-shrink-0 xl:border-l xl:border-border xl:h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <h3 id={modal.titleId} className="text-sm font-semibold text-text-primary">Available Knowledge</h3>
        <button
          ref={closeRef}
          aria-label="Close knowledge panel"
          onClick={onClose}
          className="p-2 -m-2 text-text-secondary hover:text-text-primary cursor-pointer"
        >
          <CloseIcon />
        </button>
      </div>

      {nothingUploaded ? (
        <div className="flex-1 px-4 py-8 text-center text-sm text-text-secondary">Nothing uploaded yet</div>
      ) : (
        <>
          <div className="px-4 py-3 border-b border-border text-xs text-text-secondary">
            <p>Pick groups, people or topics to limit what Munin searches when you send a message. Numbers are paper counts.</p>
            <a href="/knowledge" onClick={openKnowledgePage} className="inline-block mt-1.5 text-accent hover:underline">
              Browse all papers
            </a>
          </div>

          {/* Scrollable content */}
          <div className="flex-1 overflow-y-auto">
            {/* Research Groups */}
            <Section title={`Research Groups (${catalog.groups.length})`} defaultOpen={true}>
              <div className="flex flex-col gap-1">
                {catalog.groups.map(g => (
                  <TagRow
                    key={g.slug}
                    label={g.display_name}
                    count={g.paper_count}
                    active={activeKeys.has(`group:${g.slug}`)}
                    activeClass="bg-accent/15 text-accent"
                    onToggle={() => toggleTag('group', g.slug)}
                  />
                ))}
              </div>
            </Section>

            {/* Contributors */}
            <Section title={`Contributors (${catalog.contributors.length})`}>
              <div className="flex flex-col gap-1">
                {catalog.contributors.map(c => (
                  <TagRow
                    key={c.username}
                    label={c.display_name}
                    count={c.paper_count}
                    active={activeKeys.has(`contributor:${c.username}`)}
                    activeClass="bg-success/15 text-success"
                    onToggle={() => toggleTag('contributor', c.username)}
                  />
                ))}
              </div>
            </Section>

            {/* Topics */}
            <Section title={`Topics (${catalog.topics.length})`}>
              <div className="flex flex-col gap-1">
                {catalog.topics.length > TOPIC_FILTER_THRESHOLD && (
                  <input
                    type="text"
                    aria-label="Filter topics"
                    value={topicQuery}
                    onChange={e => setTopicQuery(e.target.value)}
                    placeholder="Filter topics..."
                    className="mb-1 px-2.5 py-1.5 bg-bg-tertiary border border-border rounded-lg text-xs text-text-primary placeholder:text-text-secondary outline-none focus:border-accent"
                  />
                )}
                {shownTopics.map(t => (
                  <TagRow
                    key={t.slug}
                    label={t.label}
                    count={t.paper_count}
                    active={activeKeys.has(`topic:${t.slug}`)}
                    activeClass="bg-warning/15 text-warning"
                    onToggle={() => toggleTag('topic', t.slug)}
                  />
                ))}
                {query && shownTopics.length === 0 && (
                  <p className="px-2.5 py-1.5 text-xs text-text-secondary">No topics match "{topicQuery.trim()}"</p>
                )}
                {!query && topicLimit < catalog.topics.length && (
                  <button
                    onClick={() => setTopicLimit(l => l + 20)}
                    className="px-2.5 py-1.5 text-xs text-accent hover:text-accent-hover cursor-pointer text-left"
                  >
                    Show more ({catalog.topics.length - topicLimit} remaining)
                  </button>
                )}
              </div>
            </Section>
          </div>
        </>
      )}

      {/* Active filters summary */}
      {activeTags.length > 0 && (
        <div className="px-4 py-3 border-t border-border">
          <div className="flex items-center justify-between mb-2">
            <span className="text-[10px] uppercase tracking-wider text-text-secondary font-medium">Active filters</span>
            <button
              onClick={() => onTagsChange([])}
              className="text-[10px] text-accent hover:text-accent-hover cursor-pointer"
            >
              Clear all
            </button>
          </div>
          <div className="flex flex-wrap gap-1">
            {activeTags.map((tag, i) => {
              const name = tagName(tag);
              return (
                <span
                  key={`${tag.kind}:${tag.value}`}
                  className={`inline-flex items-center gap-1 pl-2 rounded text-[10px] font-medium ${
                    tag.kind === 'group' ? 'bg-accent/15 text-accent' :
                    tag.kind === 'contributor' ? 'bg-success/15 text-success' :
                    'bg-warning/15 text-warning'
                  }`}
                >
                  {name}
                  <button
                    aria-label={`Remove filter ${name}`}
                    onClick={() => onTagsChange(activeTags.filter((_, j) => j !== i))}
                    className="inline-flex items-center justify-center w-6 h-6 opacity-60 hover:opacity-100 cursor-pointer"
                  >
                    <CloseIcon size={10} />
                  </button>
                </span>
              );
            })}
          </div>
        </div>
      )}

      {/* The overlay covers the chat, so it needs an obvious way back to it. */}
      <div className="xl:hidden px-4 py-3 border-t border-border">
        <button
          onClick={onClose}
          className="w-full px-3 py-2 bg-accent text-bg-primary rounded-lg text-sm font-medium hover:bg-accent-hover transition-colors cursor-pointer"
        >
          Done
        </button>
      </div>
    </div>
  );
}
