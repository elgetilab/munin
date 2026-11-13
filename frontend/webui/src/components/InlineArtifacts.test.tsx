import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../test/msw-server';
import { InlineArtifacts } from './InlineArtifacts';
import { useUiStore, _resetUiStoreForTests } from '../stores/uiStore';
import { MAX_INLINE_PREVIEW_BYTES, WIDGET_CONTENT_TYPE } from '../lib/artifactDisplay';
import type { ToolCall } from '../lib/types';

vi.mock('3dmol/build/3Dmol-min.js?raw', () => ({ default: 'library' }));
vi.mock('../widgets/proteinRuntime.js?raw', () => ({ default: 'runtime' }));

function sandboxCall(file: Record<string, unknown>): ToolCall {
  return {
    name: 'run_python',
    arguments: {},
    result: { artifacts: [{ registered_artifact_id: 'art_1', external_url: '/api/artifacts/c1/hash', ...file }] },
  };
}

const PLOT = sandboxCall({ filename: 'plot.png', content_type: 'image/png', size_bytes: 2048 });
const PDF = sandboxCall({ filename: 'paper.pdf', content_type: 'application/pdf', size_bytes: 2 * 1024 * 1024 });
const SVG: ToolCall = {
  name: 'create_artifact',
  arguments: {},
  result: { id: 'art_svg', title: 'Pathway', content_type: 'image/svg+xml', version: 2 },
};

describe('InlineArtifacts', () => {
  beforeEach(() => _resetUiStoreForTests());

  it('renders nothing when the turn made no artifacts', () => {
    const { container } = render(<InlineArtifacts toolCalls={[{ name: 'search', arguments: {}, result: {} }]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('shows a plot as a preview', () => {
    render(<InlineArtifacts toolCalls={[PLOT]} />);
    expect(screen.getByRole('img', { name: 'plot.png' })).toHaveAttribute('src', '/api/artifacts/c1/hash');
    expect(screen.queryByTestId('artifact-card')).not.toBeInTheDocument();
  });

  it('captions a preview with its name and says it opens', () => {
    render(<InlineArtifacts toolCalls={[PLOT]} />);
    expect(screen.getByRole('button', { name: 'Open plot.png' })).toBe(screen.getByTestId('artifact-preview'));
    const caption = screen.getByTestId('artifact-preview-caption');
    expect(caption).toHaveTextContent('plot.png');
    expect(caption).toHaveTextContent('Open');
  });

  it('shows a PDF as a card with its type and size', () => {
    render(<InlineArtifacts toolCalls={[PDF]} />);
    const card = screen.getByTestId('artifact-card');
    expect(card).toHaveTextContent('paper.pdf');
    expect(card).toHaveTextContent('PDF · 2.0 MB');
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
  });

  it('shows an oversized image as a card', () => {
    render(<InlineArtifacts toolCalls={[sandboxCall({ filename: 'huge.png', content_type: 'image/png', size_bytes: MAX_INLINE_PREVIEW_BYTES + 1 })]} />);
    expect(screen.getByTestId('artifact-card')).toHaveTextContent('huge.png');
  });

  it.each([
    ['a card', PDF, 'artifact-card'],
    ['a preview', PLOT, 'artifact-preview'],
  ])('opens the panel on the artifact when %s is clicked', async (_label, call, testId) => {
    render(<InlineArtifacts toolCalls={[call]} />);
    useUiStore.setState({ knowledgePanelOpen: true });
    await userEvent.click(screen.getByTestId(testId));
    const ui = useUiStore.getState();
    expect(ui.selectedArtifactId).toBe('art_1');
    expect(ui.artifactPanelOpen).toBe(true);
    expect(ui.knowledgePanelOpen).toBe(false);
  });

  it('asks before another artifact replaces a changed draft, and keeps it on No', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    useUiStore.setState({ selectedArtifactId: 'art_other', artifactPanelOpen: true, artifactDraftChanged: true });
    render(<InlineArtifacts toolCalls={[PDF]} />);
    await userEvent.click(screen.getByTestId('artifact-card'));
    expect(confirm).toHaveBeenCalledWith('Discard your changes to this artifact?');
    expect(useUiStore.getState().selectedArtifactId).toBe('art_other');

    confirm.mockReturnValue(true);
    await userEvent.click(screen.getByTestId('artifact-card'));
    expect(useUiStore.getState().selectedArtifactId).toBe('art_1');
    confirm.mockRestore();
  });

  it('falls back to a card when the file is gone', () => {
    render(<InlineArtifacts toolCalls={[PLOT]} />);
    fireEvent.error(screen.getByRole('img', { name: 'plot.png' }));
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
    expect(screen.getByTestId('artifact-card')).toHaveTextContent('preview unavailable');
  });

  it('does not repeat an image the model embedded in its text', () => {
    render(<InlineArtifacts toolCalls={[PLOT]} content="Here: ![plot](/api/artifacts/c1/hash)" />);
    expect(screen.queryByTestId('inline-artifacts')).not.toBeInTheDocument();
  });

  it('loads a model-written SVG at its version and shows it as an image', async () => {
    const versions: (string | null)[] = [];
    server.use(http.get('/api/chats/:cid/artifacts/:aid', ({ request }) => {
      versions.push(new URL(request.url).searchParams.get('version'));
      return HttpResponse.json({ id: 'art_svg', content: '<svg xmlns="http://www.w3.org/2000/svg"/>' });
    }));
    render(<InlineArtifacts toolCalls={[SVG]} conversationId="c1" />);
    const loading = screen.getByTestId('artifact-preview-loading');
    expect(loading).toHaveTextContent('Loading preview...');
    expect(loading).toHaveAttribute('aria-busy', 'true');
    const img = await screen.findByRole('img', { name: 'Pathway' });
    expect(img.getAttribute('src')).toMatch(/^data:image\/svg\+xml/);
    expect(versions).toEqual(['2']);
  });

  it('falls back to a card when the SVG cannot be loaded', async () => {
    server.use(http.get('/api/chats/:cid/artifacts/:aid', () => new HttpResponse(null, { status: 404 })));
    render(<InlineArtifacts toolCalls={[SVG]} conversationId="c1" />);
    await waitFor(() => expect(screen.getByTestId('artifact-card')).toHaveTextContent('preview unavailable'));
  });
});

describe('InlineArtifacts widgets', () => {
  const VIEWER: ToolCall = {
    name: 'show_protein',
    arguments: {},
    result: { id: 'art_p', title: 'Crambin', content_type: WIDGET_CONTENT_TYPE, version: 1 },
  };

  beforeEach(() => _resetUiStoreForTests());

  it('shows a viewer in the message, and Expand opens the panel', async () => {
    server.use(
      http.get('/api/chats/c1/artifacts/art_p', () => HttpResponse.json({
        id: 'art_p',
        content: JSON.stringify({ widget: 'protein', source: 'pdb', id: '1CRN', style: 'cartoon', color: 'spectrum', highlight: [] }),
      })),
      http.get('/api/chats/c1/artifacts/art_p/structure', () => HttpResponse.json({ format: 'pdb', data: 'ATOM' })),
    );
    render(<InlineArtifacts toolCalls={[VIEWER]} conversationId="c1" />);
    await screen.findByTestId('protein-viewer');
    await userEvent.click(screen.getByRole('button', { name: 'Expand' }));
    expect(useUiStore.getState().selectedArtifactId).toBe('art_p');
    expect(useUiStore.getState().artifactPanelOpen).toBe(true);
  });

  it('shows a primer design in the message, by the kind in its spec', async () => {
    const spec = {
      kind: 'primer_design',
      version: 1,
      mode: 'pcr',
      template: { name: 'pUC19', length: 2686, circular: true },
      target: [400, 450],
      pairs: [{
        name: 'pair_1',
        forward: { sequence: 'TGTAAAACGACGGCCAGT', display: 'tgtaaAACGACGGCCAGT', anneal: 'AACGACGGCCAGT', tail: 'tgtaa', strand: '+', start: 377, end: 395, length: 18, tm: 60.2, gc_percent: 50 },
        reverse: { sequence: 'CAGGAAACAGCTATGAC', display: 'CAGGAAACAGCTATGAC', anneal: 'CAGGAAACAGCTATGAC', tail: '', strand: '-', start: 483, end: 500, length: 17, tm: 58.1, gc_percent: 47.1 },
        product_size: 123,
        amplicon: 'ACGT',
        checks: [],
        verified: { outcome: 'single_product', product_count: 1, product_sizes: [123], single_clean_product: true },
      }],
      summary: { total: 0, passed: 0, hard_failures: 0, soft_failures: 0, not_computed: 0, usable: true },
      notes: [],
    };
    server.use(http.get('/api/chats/c1/artifacts/art_p', () => HttpResponse.json({ id: 'art_p', content: JSON.stringify(spec) })));
    render(<InlineArtifacts toolCalls={[{ ...VIEWER, name: 'design_primers' }]} conversationId="c1" />);
    const viewer = await screen.findByTestId('primer-design-viewer');
    expect(viewer).toHaveTextContent('Single clean product');
    expect(screen.queryByTestId('protein-viewer')).not.toBeInTheDocument();
  });

  it('falls back to a card when the spec is not usable', async () => {
    server.use(http.get('/api/chats/c1/artifacts/art_p', () => HttpResponse.json({ id: 'art_p', content: 'broken' })));
    render(<InlineArtifacts toolCalls={[VIEWER]} conversationId="c1" />);
    expect(await screen.findByTestId('artifact-card')).toHaveTextContent('preview unavailable');
  });

  it('falls back to a card when the spec cannot be loaded', async () => {
    server.use(http.get('/api/chats/c1/artifacts/art_p', () => new HttpResponse(null, { status: 404 })));
    render(<InlineArtifacts toolCalls={[VIEWER]} conversationId="c1" />);
    expect(await screen.findByTestId('artifact-card')).toHaveTextContent('Interactive view');
  });
});
