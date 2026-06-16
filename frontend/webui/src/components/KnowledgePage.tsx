import { useState, useEffect, useCallback, useMemo } from 'react';
import type { TagCatalog, TagChip, TagPaper, TopicTag, GroupTag } from '../lib/types';
import { fetchTags, fetchTagPapers, fetchEmbeddingMap } from '../lib/api';
import { EmbeddingMapView } from './EmbeddingMapView';

interface KnowledgePageProps {
  initialRoute?: string; // e.g. "group/zeitler" or "topic/nmr-..."
  tagCatalog: TagCatalog | null;
  onClose?: () => void;
  onChatWithTag: (tags: TagChip[]) => void;
}

type View =
  | { type: 'overview' }
  | { type: 'browse'; kind: string; slug: string };

export function KnowledgePage({ initialRoute, tagCatalog, onChatWithTag }: KnowledgePageProps) {
  const [catalog, setCatalog] = useState<TagCatalog | null>(tagCatalog);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [mapStats, setMapStats] = useState<{ paper_count: number; cluster_count: number } | null>(null);
  const [view, setView] = useState<View>(
    initialRoute ? { type: 'browse', kind: initialRoute.split('/')[0], slug: initialRoute.split('/').slice(1).join('/') } : { type: 'overview' }
  );

  // Sync from prop when it arrives (App-level fetch may complete after mount)
  useEffect(() => {
    if (tagCatalog && !catalog) {
      setCatalog(tagCatalog);
    }
  }, [tagCatalog, catalog]);

  // Fetch ourselves if prop is null
  useEffect(() => {
    if (!catalog && !loadError) {
      fetchTags()
        .then(setCatalog)
        .catch(e => setLoadError(e instanceof Error ? e.message : 'Failed to load'));
    }
  }, [catalog, loadError]);

  // Fetch embedding map stats for accurate paper count
  useEffect(() => {
    if (!mapStats) {
      fetchEmbeddingMap()
        .then(data => setMapStats({ paper_count: data.paper_count, cluster_count: data.cluster_count }))
        .catch(() => {});
    }
  }, [mapStats]);

  if (!catalog) {
    return (
      <div className="flex-1 flex items-center justify-center text-text-secondary text-sm">
        {loadError ? `Error: ${loadError}` : 'Loading knowledge base...'}
      </div>
    );
  }

  const totalPapers = mapStats?.paper_count ?? catalog.topics.reduce((sum, t) => sum + t.paper_count, 0);

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Sub-header */}
      <div className="flex items-center gap-3 px-6 py-3 border-b border-border bg-bg-primary">
        {view.type !== 'overview' && (
          <button
            onClick={() => setView({ type: 'overview' })}
            className="text-text-secondary hover:text-text-primary text-sm cursor-pointer"
          >
            &larr; Back
          </button>
        )}
        <h2 className="text-lg font-semibold text-text-primary">
          {view.type === 'overview' ? 'Knowledge Base' : getBrowseTitle(view.kind, view.slug, catalog)}
        </h2>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto">
        {view.type === 'overview' ? (
          <OverviewView
            catalog={catalog}
            totalPapers={totalPapers}
            onBrowse={(kind, slug) => setView({ type: 'browse', kind, slug })}
            onChatWithTag={onChatWithTag}
          />
        ) : (
          <BrowseView
            kind={view.kind}
            slug={view.slug}
            catalog={catalog}
            onChatWithTag={onChatWithTag}
          />
        )}
      </div>
    </div>
  );
}

function getBrowseTitle(kind: string, slug: string, catalog: TagCatalog): string {
  if (kind === 'group') {
    const g = catalog.groups.find(g => g.slug === slug);
    return g ? g.display_name : slug;
  }
  if (kind === 'topic') {
    const t = catalog.topics.find(t => t.slug === slug);
    return t ? t.label : slug;
  }
  if (kind === 'contributor') {
    const c = catalog.contributors.find(c => c.username === slug);
    return c ? c.display_name : slug;
  }
  return slug;
}

// ── Overview ─────────────────────────────────────────────────────────────────

function OverviewView({ catalog, totalPapers, onBrowse, onChatWithTag }: {
  catalog: TagCatalog;
  totalPapers: number;
  onBrowse: (kind: string, slug: string) => void;
  onChatWithTag: (tags: TagChip[]) => void;
}) {
  return (
    <div className="px-6 py-6 max-w-4xl mx-auto">
      {/* Stats bar */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
        <StatCard label="Papers" value={totalPapers.toLocaleString()} />
        <StatCard label="Topics" value={String(catalog.topics.length)} />
        <StatCard label="Research Groups" value={String(catalog.groups.length)} />
        <StatCard label="Contributors" value={String(catalog.contributor_count ?? catalog.contributors.length)} />
      </div>

      {/* Embedding Map */}
      <EmbeddingMapView onNavigateToTopic={(slug) => onBrowse('topic', slug)} />

      {/* Research Groups */}
      <ResearchGroupsSection
        groups={catalog.groups}
        onBrowse={onBrowse}
        onChatWithTag={onChatWithTag}
      />

      {/* Topics */}
      <TopicSearch topics={catalog.topics} onBrowse={onBrowse} />
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-bg-secondary border border-border rounded-xl px-4 py-3">
      <div className="text-xl font-semibold text-text-primary tabular-nums">{value}</div>
      <div className="text-xs text-text-secondary mt-1">{label}</div>
    </div>
  );
}

// Empty research groups (0 papers) are hidden by default so a long tail of
// labs that have not uploaded yet does not crowd the overview. A toggle
// reveals them; visible groups are ordered by paper count, most first.
function ResearchGroupsSection({ groups, onBrowse, onChatWithTag }: {
  groups: GroupTag[];
  onBrowse: (kind: string, slug: string) => void;
  onChatWithTag: (tags: TagChip[]) => void;
}) {
  const [showAll, setShowAll] = useState(false);

  const nonEmpty = useMemo(
    () => groups.filter(g => g.paper_count > 0).sort((a, b) => b.paper_count - a.paper_count),
    [groups],
  );
  const empties = useMemo(
    () => groups.filter(g => g.paper_count === 0).sort((a, b) => a.display_name.localeCompare(b.display_name)),
    [groups],
  );
  const visible = showAll ? [...nonEmpty, ...empties] : nonEmpty;

  return (
    <>
      <h3 className="text-sm font-semibold text-text-primary mb-3">Research Groups</h3>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 mb-3">
        {visible.map(g => (
          <GroupCard
            key={g.slug}
            group={g}
            onBrowse={() => onBrowse('group', g.slug)}
            onChat={() => onChatWithTag([{ kind: 'group', value: g.slug }])}
          />
        ))}
      </div>
      {empties.length > 0 && (
        <div className="mb-8">
          <button
            onClick={() => setShowAll(s => !s)}
            className="text-xs text-accent hover:underline cursor-pointer"
          >
            {showAll ? 'Show fewer' : `Show all ${groups.length} research groups`}
          </button>
        </div>
      )}
    </>
  );
}

function GroupCard({ group, onBrowse, onChat }: {
  group: { slug: string; display_name: string; paper_count: number };
  onBrowse: () => void;
  onChat: () => void;
}) {
  const empty = group.paper_count === 0;
  return (
    <div className={`bg-bg-secondary border border-border rounded-xl p-4 ${empty ? 'opacity-50' : ''}`}>
      <h4 className="text-sm font-medium text-text-primary mb-1">{group.display_name}</h4>
      <p className="text-xs text-text-secondary mb-3">
        {empty ? 'No papers uploaded yet' : `${group.paper_count.toLocaleString()} papers`}
      </p>
      {!empty && (
        <div className="flex gap-2">
          <button
            onClick={onBrowse}
            className="text-xs px-3 py-1.5 bg-bg-tertiary border border-border rounded-lg text-text-secondary hover:text-text-primary hover:border-accent transition-colors cursor-pointer"
          >
            Browse papers
          </button>
          <button
            onClick={onChat}
            className="text-xs px-3 py-1.5 bg-accent/15 border border-accent/30 rounded-lg text-accent hover:bg-accent/25 transition-colors cursor-pointer"
          >
            Discuss in chat
          </button>
        </div>
      )}
    </div>
  );
}

// ── Topic Search ─────────────────────────────────────────────────────────────

function TopicSearch({ topics, onBrowse }: { topics: TopicTag[]; onBrowse: (kind: string, slug: string) => void }) {
  const [query, setQuery] = useState('');
  const [showAll, setShowAll] = useState(false);

  const filtered = useMemo(() => {
    if (query.trim()) {
      const q = query.toLowerCase();
      return topics.filter(t => t.label.toLowerCase().includes(q));
    }
    return showAll ? topics : topics.slice(0, 20);
  }, [topics, query, showAll]);

  return (
    <>
      <div className="flex items-center gap-3 mb-3">
        <h3 className="text-sm font-semibold text-text-primary">Topics</h3>
        <input
          type="text"
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="Search topics..."
          className="flex-1 max-w-64 text-xs px-3 py-1.5 bg-bg-secondary border border-border rounded-lg text-text-primary placeholder:text-text-secondary outline-none focus:border-accent transition-colors"
        />
        <span className="text-[10px] text-text-secondary">
          {query.trim() ? `${filtered.length} results` : `${topics.length} total`}
        </span>
      </div>
      <div className="flex flex-col gap-1">
        {filtered.map(t => (
          <button
            key={t.slug}
            onClick={() => onBrowse('topic', t.slug)}
            className="flex items-center gap-3 px-3 py-2 rounded-lg text-sm text-text-secondary hover:bg-bg-secondary hover:text-text-primary transition-colors cursor-pointer text-left"
          >
            <span className="flex-1 truncate">{t.label}</span>
            <span className="text-xs tabular-nums text-text-secondary">{t.paper_count.toLocaleString()} papers</span>
          </button>
        ))}
        {!query.trim() && !showAll && topics.length > 20 && (
          <button
            onClick={() => setShowAll(true)}
            className="text-xs text-accent hover:underline px-3 py-2 text-left cursor-pointer"
          >
            Show all {topics.length} topics
          </button>
        )}
        {!query.trim() && showAll && (
          <button
            onClick={() => setShowAll(false)}
            className="text-xs text-accent hover:underline px-3 py-2 text-left cursor-pointer"
          >
            Show less
          </button>
        )}
        {query.trim() && filtered.length === 0 && (
          <p className="text-xs text-text-secondary px-3 py-2">No topics matching "{query}"</p>
        )}
      </div>
    </>
  );
}

// ── Browse View ──────────────────────────────────────────────────────────────

function BrowseView({ kind, slug, catalog: _catalog, onChatWithTag }: {
  kind: string;
  slug: string;
  catalog: TagCatalog;
  onChatWithTag: (tags: TagChip[]) => void;
}) {
  const [papers, setPapers] = useState<TagPaper[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [sort, setSort] = useState('year_desc');
  const [offset, setOffset] = useState(0);
  const limit = 50;

  const loadPapers = useCallback(async (newOffset: number, newSort: string) => {
    setLoading(true);
    try {
      const res = await fetchTagPapers(kind, slug, { offset: newOffset, limit, sort: newSort });
      setPapers(newOffset === 0 ? res.papers : prev => [...prev, ...res.papers]);
      setTotal(res.total);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [kind, slug]);

  useEffect(() => {
    setPapers([]);
    setOffset(0);
    loadPapers(0, sort);
  }, [kind, slug, sort, loadPapers]);

  const loadMore = () => {
    const newOffset = offset + limit;
    setOffset(newOffset);
    loadPapers(newOffset, sort);
  };

  return (
    <div className="px-6 py-4">
      {/* Controls */}
      <div className="flex items-center gap-3 mb-4">
        <span className="text-xs text-text-secondary">{total.toLocaleString()} papers</span>
        <div className="flex-1" />
        <select
          value={sort}
          onChange={e => { setSort(e.target.value); setOffset(0); }}
          className="text-xs px-2 py-1 bg-bg-secondary border border-border rounded-lg text-text-primary outline-none cursor-pointer"
        >
          <option value="year_desc">Newest first</option>
          <option value="year_asc">Oldest first</option>
          <option value="upload_desc">Recently added</option>
        </select>
        <button
          onClick={() => onChatWithTag([{ kind: kind as TagChip['kind'], value: slug }])}
          className="text-xs px-3 py-1.5 bg-accent/15 border border-accent/30 rounded-lg text-accent hover:bg-accent/25 transition-colors cursor-pointer"
        >
          Discuss in chat
        </button>
      </div>

      {/* Paper list */}
      <div className="flex flex-col gap-2">
        {papers.map((p, i) => (
          <PaperRow key={`${p.doi}-${i}`} paper={p} />
        ))}
      </div>

      {/* Load more */}
      {papers.length < total && (
        <div className="flex justify-center py-4">
          <button
            onClick={loadMore}
            disabled={loading}
            className="text-xs px-4 py-2 bg-bg-secondary border border-border rounded-lg text-text-secondary hover:text-text-primary transition-colors cursor-pointer disabled:opacity-50"
          >
            {loading ? 'Loading...' : `Load more (${papers.length} of ${total})`}
          </button>
        </div>
      )}

      {loading && papers.length === 0 && (
        <div className="text-center py-12 text-text-secondary text-sm">Loading papers...</div>
      )}
    </div>
  );
}

function PaperRow({ paper }: { paper: TagPaper }) {
  return (
    <div className="bg-bg-secondary border border-border rounded-xl px-4 py-3">
      <div className="flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <h4 className="text-sm font-medium text-text-primary leading-snug">{paper.title}</h4>
          <div className="flex flex-wrap items-center gap-2 mt-1.5">
            {paper.year && (
              <span className="text-[10px] px-1.5 py-0.5 bg-bg-tertiary rounded text-text-secondary">{paper.year}</span>
            )}
            {paper.journal && (
              <span className="text-[10px] text-text-secondary truncate max-w-48">{paper.journal}</span>
            )}
            {paper.topic && (
              <span className="text-[10px] px-1.5 py-0.5 bg-warning/10 text-warning rounded truncate max-w-48">{paper.topic.label}</span>
            )}
          </div>
          {paper.authors && paper.authors.length > 0 && (
            <p className="text-[11px] text-text-secondary mt-1 truncate">
              {paper.authors.slice(0, 3).join(', ')}{paper.authors.length > 3 ? ` + ${paper.authors.length - 3} more` : ''}
            </p>
          )}
        </div>
        {paper.download_url && (
          <a
            href={paper.download_url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex-shrink-0 text-xs px-2.5 py-1.5 bg-bg-tertiary border border-border rounded-lg text-text-secondary hover:text-accent hover:border-accent transition-colors"
            title="Download PDF"
          >
            PDF
          </a>
        )}
      </div>
      {/* Contributors */}
      {paper.contributors && paper.contributors.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-2">
          {paper.contributors.map((c, i) => (
            <span key={i} className="text-[10px] px-1.5 py-0.5 bg-accent/10 text-accent rounded">
              {c.display_name} ({c.group_display_name})
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
