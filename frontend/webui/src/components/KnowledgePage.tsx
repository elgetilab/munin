import { useState, useEffect, useLayoutEffect, useCallback, useMemo, useRef } from 'react';
import type { EmbeddingMap, TagCatalog, TagChip, TagPaper, TopicTag, GroupTag } from '../lib/types';
import { fetchTags, fetchTagPapers, fetchEmbeddingMap } from '../lib/api';
import { UPLOAD_URL } from '../lib/urls';
import { useUiStore } from '../stores/uiStore';
import { EmbeddingMapView } from './EmbeddingMapView';
import { ErrorNotice } from './ui/ErrorNotice';

interface KnowledgePageProps {
  // The part of the path after /knowledge, e.g. "group/zeitler". App keeps it in
  // step with the address bar, including on Back and Forward.
  initialRoute?: string;
  tagCatalog: TagCatalog | null;
  onClose?: () => void;
  onChatWithTag: (tags: TagChip[]) => void;
}

type View =
  | { type: 'overview' }
  | { type: 'browse'; kind: string; slug: string };

const BROWSE_KINDS = ['group', 'topic', 'contributor'];

function viewFromRoute(route: string | undefined): View {
  const [kind, ...rest] = (route ?? '').split('/');
  const slug = rest.join('/');
  if (!BROWSE_KINDS.includes(kind) || !slug) return { type: 'overview' };
  try {
    return { type: 'browse', kind, slug: decodeURIComponent(slug) };
  } catch {
    return { type: 'browse', kind, slug };
  }
}

export function KnowledgePage({ initialRoute, tagCatalog, onChatWithTag }: KnowledgePageProps) {
  // The catalog reaches this page two ways: as a prop once App's /api/tags
  // call lands, or from this page's own fetch when that has not happened
  // yet. Deriving it from both rather than copying the prop into state is
  // what removes the old "sync from prop when it arrives" effect: there is
  // one value, the prop wins whenever it is present, and nothing has to
  // watch for it changing.
  const [fetchedCatalog, setFetchedCatalog] = useState<TagCatalog | null>(null);
  const catalog = tagCatalog ?? fetchedCatalog;
  const [loadError, setLoadError] = useState<string | null>(null);
  const setShowKnowledgePage = useUiStore(s => s.setShowKnowledgePage);
  const view = viewFromRoute(initialRoute);
  // The overview stays mounted behind a browse view, so its filters, its search
  // and the map survive the trip. It is only mounted once it has been visited.
  const [overviewVisited, setOverviewVisited] = useState(view.type === 'overview');
  if (view.type === 'overview' && !overviewVisited) setOverviewVisited(true);

  // The map is fetched once here: the stats and the map view both use it.
  const [mapAttempt, setMapAttempt] = useState(0);
  const [mapResult, setMapResult] = useState<{ attempt: number; data: EmbeddingMap | null; error: string | null } | null>(null);
  const mapLoading = mapResult?.attempt !== mapAttempt;
  const mapData = mapLoading ? null : mapResult.data;
  const mapError = mapLoading ? null : mapResult.error;

  const scrollRef = useRef<HTMLDivElement>(null);
  const overviewScroll = useRef(0);
  const browseKey = view.type === 'browse' ? `${view.kind}/${view.slug}` : null;
  useLayoutEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = browseKey ? 0 : overviewScroll.current;
  }, [browseKey]);

  const goToRoute = (route: string | null) => {
    window.history.pushState(null, '', route ? `/knowledge/${route}` : '/knowledge');
    setShowKnowledgePage(route ?? true);
  };

  // Fetch ourselves if the prop is still null.
  useEffect(() => {
    if (catalog || loadError) return;
    let cancelled = false;
    fetchTags()
      .then(c => { if (!cancelled) setFetchedCatalog(c); })
      .catch(e => { if (!cancelled) setLoadError(e instanceof Error ? e.message : 'Failed to load'); });
    return () => { cancelled = true; };
  }, [catalog, loadError]);

  useEffect(() => {
    let cancelled = false;
    fetchEmbeddingMap()
      .then(data => { if (!cancelled) setMapResult({ attempt: mapAttempt, data, error: null }); })
      .catch(e => {
        if (!cancelled) setMapResult({ attempt: mapAttempt, data: null, error: e instanceof Error ? e.message : 'Failed to load' });
      });
    return () => { cancelled = true; };
  }, [mapAttempt]);

  if (!catalog) {
    return loadError ? (
      <div className="flex-1 flex items-center justify-center p-6">
        <ErrorNotice
          message="Could not load the knowledge base."
          onRetry={() => setLoadError(null)}
          className="w-full max-w-md"
        />
      </div>
    ) : (
      <div className="flex-1 flex items-center justify-center text-text-secondary text-sm">
        Loading knowledge base...
      </div>
    );
  }

  const totalPapers = mapData?.paper_count ?? catalog.topics.reduce((sum, t) => sum + t.paper_count, 0);
  const catalogEmpty = !mapLoading && totalPapers === 0
    && ![...catalog.groups, ...catalog.contributors, ...catalog.topics].some(item => item.paper_count > 0);

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Sub-header */}
      <div className="flex items-center gap-3 px-6 py-3 border-b border-border bg-bg-primary">
        {view.type !== 'overview' && (
          <button
            onClick={() => goToRoute(null)}
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
      <div
        ref={scrollRef}
        onScroll={e => { if (view.type === 'overview') overviewScroll.current = e.currentTarget.scrollTop; }}
        className="flex-1 overflow-y-auto md:pb-16"
      >
        {catalogEmpty && view.type === 'overview' ? (
          <div className="px-6 py-16 text-center text-sm text-text-secondary">
            <p>No papers yet.</p>
            <a href={UPLOAD_URL} className="inline-block mt-2 text-accent hover:underline">Upload papers</a>
          </div>
        ) : overviewVisited && (
          <div hidden={view.type !== 'overview'}>
            <OverviewView
              catalog={catalog}
              totalPapers={totalPapers}
              mapData={mapData}
              mapLoading={mapLoading}
              mapError={mapError}
              onRetryMap={() => setMapAttempt(a => a + 1)}
              onBrowse={(kind, slug) => goToRoute(`${kind}/${encodeURIComponent(slug)}`)}
              onChatWithTag={onChatWithTag}
            />
          </div>
        )}
        {view.type === 'browse' && (
          <BrowseView
            key={browseKey}
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

function OverviewView({ catalog, totalPapers, mapData, mapLoading, mapError, onRetryMap, onBrowse, onChatWithTag }: {
  catalog: TagCatalog;
  totalPapers: number;
  mapData: EmbeddingMap | null;
  mapLoading: boolean;
  mapError: string | null;
  onRetryMap: () => void;
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
      <EmbeddingMapView
        mapData={mapData}
        loading={mapLoading}
        error={mapError}
        onRetry={onRetryMap}
        onNavigateToTopic={(slug) => onBrowse('topic', slug)}
        onChatWithTopic={(slug) => onChatWithTag([{ kind: 'topic', value: slug }])}
      />

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

  if (groups.length === 0) return null;

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

  if (topics.length === 0) return null;

  return (
    <>
      <div className="flex items-center gap-3 mb-3">
        <h3 className="text-sm font-semibold text-text-primary">Topics</h3>
        <input
          type="text"
          aria-label="Search topics"
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
  // Null until a response arrives: before that the count is not known to be 0.
  const [total, setTotal] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [sort, setSort] = useState('year_desc');
  const limit = 50;
  // Only the newest request may report back, so a slow answer for the previous
  // sort or group cannot land in this list.
  const latestRequest = useRef(0);

  const loadPapers = useCallback(async (offset: number, newSort: string) => {
    const request = ++latestRequest.current;
    setLoading(true);
    setFailed(false);
    try {
      const res = await fetchTagPapers(kind, slug, { offset, limit, sort: newSort });
      if (request !== latestRequest.current) return;
      setPapers(offset === 0 ? res.papers : prev => [...prev, ...res.papers]);
      setTotal(res.total);
    } catch {
      if (request === latestRequest.current) setFailed(true);
    } finally {
      if (request === latestRequest.current) setLoading(false);
    }
  }, [kind, slug]);

  useEffect(() => {
    setPapers([]);
    setTotal(null);
    loadPapers(0, sort);
  }, [kind, slug, sort, loadPapers]);

  // The next page starts after what actually arrived, so retrying a failed
  // page asks for the same one again rather than skipping it.
  const loadMore = () => loadPapers(papers.length, sort);
  const isEmpty = total === 0 && !loading && !failed;

  return (
    <div className="px-6 py-4">
      {/* Controls */}
      {total !== null && !isEmpty && <div className="flex items-center gap-3 mb-4">
        <span className="text-xs text-text-secondary">{total.toLocaleString()} papers</span>
        <div className="flex-1" />
        <select
          aria-label="Sort papers"
          value={sort}
          onChange={e => setSort(e.target.value)}
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
      </div>}

      {isEmpty && (
        <div className="text-center py-12 text-sm text-text-secondary">
          <p>No papers here yet.</p>
          <a href={UPLOAD_URL} className="inline-block mt-2 text-accent hover:underline">Upload papers</a>
        </div>
      )}

      {/* Paper list */}
      <div className="flex flex-col gap-2">
        {papers.map((p, i) => (
          <PaperRow key={`${p.doi}-${i}`} paper={p} />
        ))}
      </div>

      {/* Load more */}
      {failed && (
        <ErrorNotice
          message="Could not load the papers."
          onRetry={papers.length > 0 ? loadMore : () => loadPapers(0, sort)}
          className="mt-3"
        />
      )}

      {total !== null && papers.length < total && !failed && (
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

// Not every paper's doi field is a real DOI, and only a real one resolves.
function doiLink(doi: string | null | undefined): string | null {
  if (!doi || !/^10\.\d+\//.test(doi)) return null;
  return `https://doi.org/${encodeURIComponent(doi).replace(/%2F/g, '/')}`;
}

function PaperRow({ paper }: { paper: TagPaper }) {
  const doiUrl = doiLink(paper.doi);
  return (
    <div className="bg-bg-secondary border border-border rounded-xl px-4 py-3">
      <div className="flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <h4 className="text-sm font-medium text-text-primary leading-snug">
            {doiUrl ? (
              <a href={doiUrl} target="_blank" rel="noopener noreferrer" className="hover:text-accent hover:underline">{paper.title}</a>
            ) : paper.title}
          </h4>
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
            {doiUrl && (
              <span className="text-[10px] text-text-secondary truncate max-w-48">{paper.doi}</span>
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
            aria-label={`Download PDF: ${paper.title}`}
            className="flex-shrink-0 whitespace-nowrap text-xs px-2.5 py-1.5 bg-bg-tertiary border border-border rounded-lg text-text-secondary hover:text-accent hover:border-accent transition-colors"
          >
            Download PDF
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
