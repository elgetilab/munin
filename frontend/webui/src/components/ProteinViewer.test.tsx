import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../test/msw-server';
import { ProteinViewer } from './ProteinViewer';
import type { ProteinSpec } from '../lib/artifactDisplay';
import { stubWidgetFrame } from '../test/widgetFrame';

// 3Dmol.js itself needs a browser, so these cover what surrounds it. The
// rendering and the sandbox are covered in e2e/widgets.spec.ts.
vi.mock('3dmol/build/3Dmol-min.js?raw', () => ({ default: 'library' }));
vi.mock('../widgets/proteinRuntime.js?raw', () => ({ default: 'runtime' }));

const SPEC: ProteinSpec = { widget: 'protein', source: 'pdb', id: '1CRN', style: 'stick', color: 'chain', highlight: [{ chain: 'A', residues: [3] }] };
const URL_PATH = '/api/chats/c1/artifacts/art_1/structure';

function renderViewer(spec = SPEC, onExpand?: () => void) {
  return render(<ProteinViewer spec={spec} title="Crambin" conversationId="c1" artifactId="art_1" version={2} onExpand={onExpand} />);
}

async function readyFrame() {
  const { post, send } = stubWidgetFrame(await screen.findByTestId('widget-frame'));
  send({ type: 'ready' });
  return { post, send };
}

function lastState(post: ReturnType<typeof stubWidgetFrame>['post']) {
  return (post.mock.lastCall?.[0] as { state: Record<string, unknown> }).state;
}

describe('ProteinViewer', () => {
  beforeEach(() => {
    server.use(http.get(URL_PATH, () => HttpResponse.json({ format: 'pdb', data: 'ATOM' })));
  });

  it('sends the structure and the spec to the frame', async () => {
    const { post } = await readyFrame_();
    expect(lastState(post)).toMatchObject({
      structure: { key: 'pdb:1CRN', format: 'pdb', data: 'ATOM' },
      source: 'pdb',
      style: 'stick',
      color: 'chain',
      highlight: SPEC.highlight,
      spin: false,
    });
  });

  it('asks for the version it was given', async () => {
    let requested: string | null = null;
    server.use(http.get(URL_PATH, ({ request }) => {
      requested = new URL(request.url).searchParams.get('version');
      return HttpResponse.json({ format: 'pdb', data: 'ATOM' });
    }));
    renderViewer();
    await screen.findByTestId('widget-frame');
    expect(requested).toBe('2');
  });

  it('sends a change of style, colour, spin and view to the frame', async () => {
    const { post, send } = await readyFrame_();
    send({ type: 'rendered', atoms: 3 });
    await userEvent.selectOptions(screen.getByLabelText('Style'), 'sphere');
    expect(lastState(post).style).toBe('sphere');
    await userEvent.selectOptions(screen.getByLabelText('Color'), 'spectrum');
    expect(lastState(post).color).toBe('spectrum');
    await userEvent.click(screen.getByRole('button', { name: 'Spin' }));
    expect(lastState(post).spin).toBe(true);
    await userEvent.click(screen.getByRole('button', { name: 'Reset view' }));
    expect(lastState(post).reset).toBe(1);
  });

  it('calls the bfactor colour Confidence for an AlphaFold model only', async () => {
    const { unmount } = renderViewer();
    expect(screen.getByRole('option', { name: 'B-factor' })).toBeInTheDocument();
    unmount();
    renderViewer({ ...SPEC, source: 'alphafold', id: 'P69905' });
    expect(screen.getByRole('option', { name: 'Confidence' })).toBeInTheDocument();
    await screen.findByTestId('widget-frame');
  });

  it('links to the entry in its database', async () => {
    renderViewer();
    expect(screen.getByRole('link', { name: 'RCSB' })).toHaveAttribute('href', 'https://www.rcsb.org/structure/1CRN');
    await screen.findByTestId('widget-frame');
  });

  it('encodes the id in that link', async () => {
    renderViewer({ ...SPEC, id: '../x?y' });
    expect(screen.getByRole('link', { name: 'RCSB' })).toHaveAttribute('href', 'https://www.rcsb.org/structure/..%2Fx%3Fy');
    await screen.findByTestId('widget-frame');
  });

  it('shows why a structure could not load, with no frame', async () => {
    server.use(http.get(URL_PATH, () => HttpResponse.json(
      { detail: { error: { message: 'no PDB entry 1CRN' } } },
      { status: 502 },
    )));
    renderViewer();
    expect(await screen.findByRole('alert')).toHaveTextContent('no PDB entry 1CRN');
    expect(screen.queryByTestId('widget-frame')).not.toBeInTheDocument();
  });

  it('shows a failure the viewer reports, as plain text', async () => {
    renderViewer();
    const { send } = await readyFrame();
    send({ type: 'error', message: '<b>WebGL</b> is unavailable' });
    expect(screen.getByRole('alert')).toHaveTextContent('<b>WebGL</b> is unavailable');
    expect(screen.getByRole('alert').querySelector('b')).toBeNull();
  });

  it('recovers through Retry after a failed load', async () => {
    let fail = true;
    server.use(http.get(URL_PATH, () => fail
      ? HttpResponse.json({ detail: { error: { message: 'no PDB entry 1CRN' } } }, { status: 502 })
      : HttpResponse.json({ format: 'pdb', data: 'ATOM' })));
    renderViewer();
    await screen.findByRole('alert');

    fail = false;
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));

    expect(await screen.findByTestId('widget-frame')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByTestId('protein-viewer')).toHaveAttribute('data-status', 'loading');
  });

  it('retries after the viewer itself reports a failure', async () => {
    renderViewer();
    const { send } = await readyFrame();
    send({ type: 'rendered', atoms: 3 });
    send({ type: 'error', message: 'WebGL is unavailable' });
    expect(screen.queryByTestId('widget-frame')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));

    expect(await screen.findByTestId('widget-frame')).toBeInTheDocument();
    expect(screen.getByTestId('protein-viewer')).toHaveAttribute('data-status', 'loading');
  });

  it('points to the database from the error', async () => {
    server.use(http.get(URL_PATH, () => new HttpResponse(null, { status: 502 })));
    renderViewer();
    const alert = await screen.findByRole('alert');
    expect(within(alert).getByRole('link', { name: 'Open on RCSB' })).toHaveAttribute('href', 'https://www.rcsb.org/structure/1CRN');
  });

  it('keeps the controls disabled until the structure is drawn, and in an error', async () => {
    renderViewer();
    expect(screen.getByLabelText('Style')).toBeDisabled();
    const { send } = await readyFrame();
    expect(screen.getByRole('button', { name: 'Reset view' })).toBeDisabled();

    send({ type: 'rendered', atoms: 3 });
    await waitFor(() => expect(screen.getByLabelText('Style')).toBeEnabled());
    expect(screen.getByRole('button', { name: 'Spin' })).toBeEnabled();

    send({ type: 'error', message: 'WebGL is unavailable' });
    expect(screen.getByLabelText('Color')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Spin' })).toBeDisabled();
  });

  it('marks itself rendered when the viewer says so', async () => {
    renderViewer();
    expect(screen.getByTestId('protein-viewer')).toHaveAttribute('data-status', 'loading');
    const { send } = await readyFrame();
    send({ type: 'rendered', atoms: 3 });
    await waitFor(() => expect(screen.getByTestId('protein-viewer')).toHaveAttribute('data-status', 'rendered'));
  });

  it('offers Expand only when it can expand', async () => {
    const onExpand = vi.fn();
    const { unmount } = renderViewer(SPEC, onExpand);
    await userEvent.click(screen.getByRole('button', { name: 'Expand' }));
    expect(onExpand).toHaveBeenCalledOnce();
    await screen.findByTestId('widget-frame');
    unmount();
    renderViewer();
    expect(screen.queryByRole('button', { name: 'Expand' })).not.toBeInTheDocument();
    await screen.findByTestId('widget-frame');
  });

  async function readyFrame_() {
    renderViewer();
    return readyFrame();
  }
});
