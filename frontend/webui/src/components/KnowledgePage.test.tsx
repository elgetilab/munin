import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../test/msw-server';
import { KnowledgePage } from './KnowledgePage';
import { _resetTagCacheForTests } from '../lib/api';
import { useUiStore, _resetUiStoreForTests } from '../stores/uiStore';
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

// deck.gl needs WebGL, which jsdom has not got. EmbeddingMapView.test.tsx covers
// what is passed to it.
vi.mock('@deck.gl/react', () => ({ default: () => <div data-testid="deck" /> }));

describe('KnowledgePage', () => {
  beforeEach(() => {
    _resetTagCacheForTests();
    _resetUiStoreForTests();
    window.history.replaceState(null, '', '/knowledge');
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

    it('keeps the controls and focus while a new sort loads, and after it fails', async () => {
      const user = userEvent.setup();
      let gate: Promise<void> | null = null;
      server.use(http.get(PAPERS_PATH, async () => {
        if (!gate) return HttpResponse.json(page(0, 2, 2));
        await gate;
        return new HttpResponse(null, { status: 500 });
      }));
      renderBrowse();
      const select = await screen.findByRole('combobox', { name: 'Sort papers' });

      let release = () => {};
      gate = new Promise(resolve => { release = resolve; });
      select.focus();
      await user.selectOptions(select, 'year_asc');

      expect(screen.getByRole('combobox', { name: 'Sort papers' })).toBe(select);
      expect(select).toHaveFocus();
      expect(screen.getByText('2 papers')).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /Load more|Loading/ })).not.toBeInTheDocument();

      release();
      await screen.findByRole('alert');
      expect(screen.getByRole('combobox', { name: 'Sort papers' })).toBe(select);
      expect(screen.getByRole('button', { name: 'Discuss in chat' })).toBeInTheDocument();
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

  it('reports a failed self-fetch in plain words and can retry', async () => {
    let fail = true;
    server.use(http.get('/api/tags', () => fail
      ? new HttpResponse(null, { status: 500 })
      : HttpResponse.json(FETCHED)));
    render(<KnowledgePage tagCatalog={null} onChatWithTag={vi.fn()} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load the knowledge base');

    fail = false;
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Knowledge Base')).toBeInTheDocument();
  });

  it('reports a failed map and retries it, leaving the rest of the page up', async () => {
    let fail = true;
    server.use(http.get('/api/embedding_map', () => fail
      ? new HttpResponse(null, { status: 500 })
      : HttpResponse.json({ generated_at: '', paper_count: 1, cluster_count: 0, points: [{ id: 'p', doi: '', title: 'T', year: 2020, x: 0, y: 0, cluster: -1 }], clusters: [] })));
    render(<KnowledgePage tagCatalog={CATALOG} onChatWithTag={vi.fn()} />);
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Could not load the paper map');
    expect(alert).toHaveClass('mb-8');
    expect(screen.getByRole('heading', { name: 'Research Groups' })).toBeInTheDocument();

    fail = false;
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByTestId('deck')).toBeInTheDocument();
  });

  it('shows one empty state for an empty catalog', async () => {
    server.use(http.get('/api/embedding_map', () => HttpResponse.json({ generated_at: '', paper_count: 0, cluster_count: 0, points: [], clusters: [] })));
    render(<KnowledgePage tagCatalog={{ topics: [], groups: [], contributors: [], contributor_count: 0 }} onChatWithTag={vi.fn()} />);
    expect(await screen.findByText('No papers yet.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Upload papers' })).toBeInTheDocument();
    expect(screen.queryByText('Research Groups')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Search topics')).not.toBeInTheDocument();
    expect(screen.queryByText('Papers')).not.toBeInTheDocument();
  });

  describe('paper rows', () => {
    const renderRows = (paper: Record<string, unknown>) => {
      server.use(http.get('/api/tags/group/elgeti/papers', () => HttpResponse.json({
        kind: 'group', slug: 'elgeti', total: 1, offset: 0, limit: 50, sort: 'year_desc',
        papers: [{ title: 'A paper', year: 2024, authors: [], journal: 'J', contributors: [], ...paper }],
      })));
      render(<KnowledgePage initialRoute="group/elgeti" tagCatalog={CATALOG} onChatWithTag={vi.fn()} />);
    };

    it('links a title to its DOI and shows the DOI', async () => {
      renderRows({ doi: '10.1234/ab(c)/1' });
      const link = await screen.findByRole('link', { name: 'A paper' });
      expect(link).toHaveAttribute('href', 'https://doi.org/10.1234/ab(c)/1');
      expect(link).toHaveAttribute('target', '_blank');
      expect(screen.getByText('10.1234/ab(c)/1')).toBeInTheDocument();
    });

    it('leaves the title plain when there is no real DOI', async () => {
      renderRows({ doi: 'upload-123' });
      expect(await screen.findByRole('heading', { name: 'A paper' })).toBeInTheDocument();
      expect(screen.queryByRole('link', { name: 'A paper' })).not.toBeInTheDocument();
    });

    it('names the paper in its PDF link', async () => {
      renderRows({ doi: '10.1/x', download_url: '/paper/x/pdf' });
      expect(await screen.findByRole('link', { name: 'Download PDF: A paper' })).toHaveAttribute('href', '/paper/x/pdf');
      expect(screen.getByText('Download PDF')).toBeInTheDocument();
    });
  });

  describe('routing', () => {
    // App's part: it passes the store's route down.
    function RoutedPage() {
      const route = useUiStore(s => s.showKnowledgePage);
      return <KnowledgePage initialRoute={typeof route === 'string' ? route : undefined} tagCatalog={CATALOG} onChatWithTag={vi.fn()} />;
    }

    beforeEach(() => {
      server.use(http.get('/api/tags/:kind/:slug/papers', () => HttpResponse.json({
        kind: 'group', slug: 'elgeti', total: 1, offset: 0, limit: 50, sort: 'year_desc',
        papers: [{ title: 'Browsed paper', doi: '10.1/x', year: 2024, authors: [], journal: 'J', contributors: [] }],
      })));
    });

    it('gives a browse view its own address, and Back returns to the overview', async () => {
      const user = userEvent.setup();
      render(<RoutedPage />);
      await user.click(await screen.findByRole('button', { name: 'Browse papers' }));
      expect(window.location.pathname).toBe('/knowledge/group/elgeti');
      expect(await screen.findByText('Browsed paper')).toBeInTheDocument();
      expect(screen.getByRole('heading', { name: 'Elgeti Lab' })).toBeInTheDocument();

      await user.click(screen.getByRole('button', { name: /Back/ }));
      expect(window.location.pathname).toBe('/knowledge');
      expect(screen.getByRole('heading', { name: 'Knowledge Base' })).toBeInTheDocument();
      expect(screen.queryByText('Browsed paper')).not.toBeInTheDocument();
    });

    it('follows the route when the browser goes back', async () => {
      render(<RoutedPage />);
      await screen.findByRole('heading', { name: 'Knowledge Base' });
      act(() => useUiStore.getState().setShowKnowledgePage('topic/nmr'));
      expect(await screen.findByRole('heading', { name: 'NMR' })).toBeInTheDocument();
      act(() => useUiStore.getState().setShowKnowledgePage(true));
      expect(await screen.findByRole('heading', { name: 'Knowledge Base' })).toBeInTheDocument();
    });

    it('reads a slug that had to be encoded for the address', async () => {
      let requested = '';
      server.use(http.get('/api/tags/:kind/:slug/papers', ({ params }) => {
        requested = String(params.slug);
        return HttpResponse.json({ kind: 'topic', slug: 'x', total: 0, offset: 0, limit: 50, sort: 'year_desc', papers: [] });
      }));
      render(<KnowledgePage initialRoute="topic/a%20b" tagCatalog={CATALOG} onChatWithTag={vi.fn()} />);
      await screen.findByText('No papers here yet.');
      expect(requested).toBe('a b');
    });

    it('keeps the search and does not fetch the map again after a round trip', async () => {
      const user = userEvent.setup();
      let mapFetches = 0;
      server.use(http.get('/api/embedding_map', () => {
        mapFetches += 1;
        return HttpResponse.json({ generated_at: '', paper_count: 1, cluster_count: 0, points: [{ id: 'p', doi: '', title: 'T', year: 2020, x: 0, y: 0, cluster: -1 }], clusters: [] });
      }));
      render(<RoutedPage />);
      await screen.findByTestId('deck');
      await user.type(screen.getByLabelText('Search topics'), 'nmr');

      await user.click(screen.getByRole('button', { name: 'Browse papers' }));
      await screen.findByText('Browsed paper');
      await user.click(screen.getByRole('button', { name: /Back/ }));

      expect(screen.getByLabelText('Search topics')).toHaveValue('nmr');
      expect(mapFetches).toBe(1);
    });
  });
});
