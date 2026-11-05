import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../test/msw-server';
import { KnowledgePage } from './KnowledgePage';
import { _resetTagCacheForTests } from '../lib/api';
import type { TagCatalog } from '../lib/types';

// Characterization tests written before the copy-prop-to-state effect is
// removed. The catalog reaches this page two ways, from App's fetch via the
// prop or from its own fetch when that prop is still null, and both have to
// keep working when the state copy goes away.

const CATALOG: TagCatalog = {
  topics: [{ slug: 'nmr', label: 'NMR', paper_count: 12 }],
  groups: [{ slug: 'elgeti', display_name: 'Elgeti Lab', paper_count: 30 }],
  contributors: [{ username: 'ada', display_name: 'Ada', group_slug: 'elgeti', paper_count: 5 }],
  contributor_count: 7,
};

const FETCHED: TagCatalog = {
  ...CATALOG,
  topics: [...CATALOG.topics, { slug: 'xray', label: 'X-ray', paper_count: 3 }],
};

describe('KnowledgePage', () => {
  beforeEach(() => {
    _resetTagCacheForTests();
    server.use(
      http.get('/api/tags', () => HttpResponse.json(FETCHED)),
      // EmbeddingMapView spreads `clusters` unguarded, so a response missing
      // it takes the whole page down. Keep this fixture shaped like the real
      // EmbeddingMap.
      http.get('/api/embedding_map', () => HttpResponse.json({
        generated_at: new Date().toISOString(),
        paper_count: 99,
        cluster_count: 1,
        points: [{ id: 'p1', doi: '10.1/x', title: 'A paper', year: 2026, x: 0.1, y: 0.2, cluster: 0 }],
        clusters: [{ id: 0, label: 'NMR', slug: 'nmr', size: 1, centroid: [0.1, 0.2] }],
      })),
    );
  });

  it('renders the overview from the catalog prop', async () => {
    render(<KnowledgePage tagCatalog={CATALOG} onChatWithTag={vi.fn()} />);
    await waitFor(() => expect(screen.getByText('Knowledge Base')).toBeInTheDocument());
    // "Topics" and "Research Groups" appear both as stat-card labels and as
    // section headings, hence getAllByText.
    expect(screen.getAllByText('Topics').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Research Groups').length).toBeGreaterThan(0);
    expect(screen.getByText('Papers')).toBeInTheDocument();
  });

  it('fetches the catalog itself when the prop is still null', async () => {
    render(<KnowledgePage tagCatalog={null} onChatWithTag={vi.fn()} />);
    await waitFor(() => expect(screen.getByText('Knowledge Base')).toBeInTheDocument());
    // 2 topics in the fetched fixture vs 1 in the prop one, which is how we
    // know this came from the page's own request.
    expect(screen.getByText('2')).toBeInTheDocument();
  });

  it('picks the catalog up when the prop arrives after mount', async () => {
    // App's /api/tags call can land after this page mounts. The page must not
    // stay stuck on its loading state when it does.
    server.use(http.get('/api/tags', () => new HttpResponse(null, { status: 500 })));
    const { rerender } = render(<KnowledgePage tagCatalog={null} onChatWithTag={vi.fn()} />);
    await waitFor(() => expect(screen.queryByText('Knowledge Base')).not.toBeInTheDocument());

    rerender(<KnowledgePage tagCatalog={CATALOG} onChatWithTag={vi.fn()} />);
    await waitFor(() => expect(screen.getByText('Knowledge Base')).toBeInTheDocument());
  });

  describe('browsing papers', () => {
    const PAPERS_PATH = '/api/tags/group/elgeti/papers';
    const paper = (n: number) => ({ title: `Paper ${n}`, doi: `10.1/${n}`, year: 2024, authors: [], journal: 'J', contributors: [] });
    const page = (offset: number, count: number, total: number) => ({
      kind: 'group', slug: 'elgeti', total, offset, limit: 50, sort: 'year_desc',
      papers: Array.from({ length: count }, (_, i) => paper(offset + i)),
    });
    const renderBrowse = () => render(<KnowledgePage initialRoute="group/elgeti" tagCatalog={CATALOG} onChatWithTag={vi.fn()} />);

    it('tells a failed fetch from an empty group, and can retry', async () => {
      let fail = true;
      server.use(http.get(PAPERS_PATH, () => fail
        ? new HttpResponse(null, { status: 500 })
        : HttpResponse.json(page(0, 2, 2))));
      renderBrowse();

      expect(await screen.findByRole('alert')).toHaveTextContent('Could not load the papers');
      expect(screen.queryByText(/0 papers/)).not.toBeInTheDocument();
      expect(screen.queryByText('No papers here yet.')).not.toBeInTheDocument();

      fail = false;
      await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
      expect(await screen.findByText('Paper 0')).toBeInTheDocument();
      expect(screen.queryByRole('alert')).not.toBeInTheDocument();
      expect(screen.getByText('2 papers')).toBeInTheDocument();
    });

    it('shows no count before the first answer', async () => {
      server.use(http.get(PAPERS_PATH, () => HttpResponse.json(page(0, 2, 2))));
      renderBrowse();
      expect(screen.queryByText(/0 papers/)).not.toBeInTheDocument();
      expect(await screen.findByText('2 papers')).toBeInTheDocument();
    });

    it('says when a group has no papers, with nothing to discuss', async () => {
      server.use(http.get(PAPERS_PATH, () => HttpResponse.json(page(0, 0, 0))));
      renderBrowse();
      expect(await screen.findByText('No papers here yet.')).toBeInTheDocument();
      expect(screen.getByRole('link', { name: 'Upload papers' })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Discuss in chat' })).not.toBeInTheDocument();
    });

    it('asks for the same page again after a failed Load more', async () => {
      const offsets: (string | null)[] = [];
      let fail = true;
      server.use(http.get(PAPERS_PATH, ({ request }) => {
        const offset = new URL(request.url).searchParams.get('offset');
        offsets.push(offset);
        if (offset === null) return HttpResponse.json(page(0, 50, 60));
        return fail ? new HttpResponse(null, { status: 500 }) : HttpResponse.json(page(50, 10, 60));
      }));
      renderBrowse();

      await userEvent.click(await screen.findByRole('button', { name: 'Load more (50 of 60)' }));
      await screen.findByRole('alert');
      expect(screen.getByText('Paper 49')).toBeInTheDocument();

      fail = false;
      await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
      expect(await screen.findByText('Paper 59')).toBeInTheDocument();
      expect(offsets).toEqual([null, '50', '50']);
      expect(screen.queryByRole('button', { name: /Load more/ })).not.toBeInTheDocument();
    });
  });

  it('surfaces a failed self-fetch', async () => {
    server.use(http.get('/api/tags', () => new HttpResponse(null, { status: 500 })));
    render(<KnowledgePage tagCatalog={null} onChatWithTag={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/failed/i)).toBeInTheDocument());
  });
});
