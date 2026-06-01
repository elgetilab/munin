import { useState } from 'react';
import type { TagCatalog, TagChip } from '../lib/types';
import { useUiStore } from '../stores/uiStore';

interface KnowledgePanelProps {
  catalog: TagCatalog;
  activeTags: TagChip[];
  onTagsChange: (tags: TagChip[]) => void;
  // P2 #26 commit 2: onClose dropped; pulled from uiStore directly.
  onBrowse?: (kind: string, slug: string) => void;
}

function Section({ title, defaultOpen, children }: { title: string; defaultOpen?: boolean; children: React.ReactNode }) {
  const [open, setOpen] = useState(defaultOpen ?? false);
  return (
    <div className="border-b border-border last:border-b-0">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-4 py-3 text-sm font-medium text-text-primary hover:bg-bg-tertiary transition-colors cursor-pointer"
      >
        {title}
        <svg
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

export function KnowledgePanel({ catalog, activeTags, onTagsChange }: KnowledgePanelProps) {
  const setKnowledgePanelOpen = useUiStore(s => s.setKnowledgePanelOpen);
  const onClose = () => setKnowledgePanelOpen(false);
  const [topicLimit, setTopicLimit] = useState(10);
  const activeKeys = new Set(activeTags.map(t => `${t.kind}:${t.value}`));

  const toggleTag = (kind: TagChip['kind'], value: string) => {
    const key = `${kind}:${value}`;
    if (activeKeys.has(key)) {
      onTagsChange(activeTags.filter(t => `${t.kind}:${t.value}` !== key));
    } else {
      onTagsChange([...activeTags, { kind, value }]);
    }
  };

  return (
    <div className="w-72 flex-shrink-0 border-l border-border bg-bg-secondary flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <h3 className="text-sm font-semibold text-text-primary">Available Knowledge</h3>
        <button
          onClick={onClose}
          className="text-text-secondary hover:text-text-primary cursor-pointer text-xs"
        >&#10005;</button>
      </div>

      {/* Scrollable content */}
      <div className="flex-1 overflow-y-auto">
        {/* Research Groups */}
        <Section title={`Research Groups (${catalog.groups.length})`} defaultOpen={true}>
          <div className="flex flex-col gap-1">
            {catalog.groups.map(g => {
              const active = activeKeys.has(`group:${g.slug}`);
              const empty = g.paper_count === 0;
              return (
                <button
                  key={g.slug}
                  onClick={() => !empty && toggleTag('group', g.slug)}
                  className={`flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-xs text-left transition-colors cursor-pointer ${
                    active ? 'bg-accent/15 text-accent' :
                    empty ? 'text-text-secondary/50 cursor-not-allowed' :
                    'text-text-secondary hover:bg-bg-tertiary hover:text-text-primary'
                  }`}
                  title={empty ? 'No papers uploaded yet' : `Click to scope search to ${g.display_name}`}
                >
                  <span className="flex-1 truncate">{g.display_name}</span>
                  <span className="text-[10px] tabular-nums">{g.paper_count.toLocaleString()}</span>
                </button>
              );
            })}
          </div>
        </Section>

        {/* Contributors */}
        <Section title={`Contributors (${catalog.contributors.length})`}>
          <div className="flex flex-col gap-1">
            {catalog.contributors.map(c => {
              const active = activeKeys.has(`contributor:${c.username}`);
              const empty = c.paper_count === 0;
              return (
                <button
                  key={c.username}
                  onClick={() => !empty && toggleTag('contributor', c.username)}
                  className={`flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-xs text-left transition-colors cursor-pointer ${
                    active ? 'bg-success/15 text-success' :
                    empty ? 'text-text-secondary/50 cursor-not-allowed' :
                    'text-text-secondary hover:bg-bg-tertiary hover:text-text-primary'
                  }`}
                >
                  <span className="flex-1 truncate">{c.display_name}</span>
                  <span className="text-[10px] tabular-nums">{c.paper_count.toLocaleString()}</span>
                </button>
              );
            })}
          </div>
        </Section>

        {/* Topics */}
        <Section title={`Topics (${catalog.topics.length})`}>
          <div className="flex flex-col gap-1">
            {catalog.topics.slice(0, topicLimit).map(t => {
              const active = activeKeys.has(`topic:${t.slug}`);
              return (
                <button
                  key={t.slug}
                  onClick={() => toggleTag('topic', t.slug)}
                  className={`flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-xs text-left transition-colors cursor-pointer ${
                    active ? 'bg-warning/15 text-warning' :
                    'text-text-secondary hover:bg-bg-tertiary hover:text-text-primary'
                  }`}
                >
                  <span className="flex-1 truncate">{t.label}</span>
                  <span className="text-[10px] tabular-nums">{t.paper_count.toLocaleString()}</span>
                </button>
              );
            })}
            {topicLimit < catalog.topics.length && (
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
            {activeTags.map((tag, i) => (
              <span
                key={`${tag.kind}:${tag.value}`}
                className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium ${
                  tag.kind === 'group' ? 'bg-accent/15 text-accent' :
                  tag.kind === 'contributor' ? 'bg-success/15 text-success' :
                  'bg-warning/15 text-warning'
                }`}
              >
                #{tag.kind === 'contributor' ? '@' : ''}{tag.value}
                <button onClick={() => onTagsChange(activeTags.filter((_, j) => j !== i))} className="opacity-60 hover:opacity-100 cursor-pointer">&#10005;</button>
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
