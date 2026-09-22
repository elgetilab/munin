import { render, screen, waitFor } from '@testing-library/react';
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

  it('surfaces a failed self-fetch', async () => {
    server.use(http.get('/api/tags', () => new HttpResponse(null, { status: 500 })));
    render(<KnowledgePage tagCatalog={null} onChatWithTag={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/failed/i)).toBeInTheDocument());
  });
});
