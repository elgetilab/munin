import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../test/msw-server';
import { MOCK_ARTIFACTS, MOCK_ARTIFACT_FULL } from '../test/msw-handlers';
import { ArtifactPanel } from './ArtifactPanel';
import { useUiStore, _resetUiStoreForTests } from '../stores/uiStore';
import { useChatStore } from '../stores/chatStore';
import type { ArtifactSummary } from '../lib/types';

// Characterization tests written before the Phase 4 restructure. This panel
// had no unit tests: the e2e suite covers it, but that is slow feedback for a
// change that rewrites how the component decides what it is showing.

const SECOND: ArtifactSummary = {
  id: 'a2', title: 'Second Artifact', content_type: 'text/markdown',
  latest_version: 1, word_count: 10, byte_size: 40, source: 'model_written',
  created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
};

const ARTIFACTS = [...MOCK_ARTIFACTS.artifacts, SECOND] as ArtifactSummary[];

function renderPanel(artifacts: ArtifactSummary[] = ARTIFACTS) {
  return render(<ArtifactPanel artifacts={artifacts} conversationId="c1" />);
}

describe('ArtifactPanel', () => {
  beforeEach(() => _resetUiStoreForTests());

  it('lists the artifacts it was given', () => {
    renderPanel();
    expect(screen.getByText('Analysis Report')).toBeInTheDocument();
    expect(screen.getByText('Second Artifact')).toBeInTheDocument();
  });

  it('loads and renders the selected artifact', async () => {
    renderPanel();
    useUiStore.setState({ selectedArtifactId: 'a1' });
    await waitFor(() => expect(screen.getByText('Some analysis here.')).toBeInTheDocument());
  });

  it('shows the content of the newly selected artifact, not the previous one', async () => {
    server.use(http.get('/api/chats/:cid/artifacts/:aid', ({ params }) =>
      params.aid === 'a2'
        ? HttpResponse.json({ ...MOCK_ARTIFACT_FULL, id: 'a2', title: 'Second Artifact',
                              content: 'Totally different body.', version: 1, latest_version: 1 })
        : HttpResponse.json(MOCK_ARTIFACT_FULL)));

    renderPanel();
    useUiStore.setState({ selectedArtifactId: 'a1' });
    await waitFor(() => expect(screen.getByText('Some analysis here.')).toBeInTheDocument());

    useUiStore.setState({ selectedArtifactId: 'a2' });
    await waitFor(() => expect(screen.getByText('Totally different body.')).toBeInTheDocument());
    expect(screen.queryByText('Some analysis here.')).not.toBeInTheDocument();
  });

  it('surfaces a failed load', async () => {
    server.use(http.get('/api/chats/:cid/artifacts/:aid', () => new HttpResponse(null, { status: 404 })));
    renderPanel();
    useUiStore.setState({ selectedArtifactId: 'a1' });
    await waitFor(() => expect(screen.getByText(/failed/i)).toBeInTheDocument());
  });

  it('re-reads the artifact when a newer version arrives in the list', async () => {
    let version = 2;
    server.use(http.get('/api/chats/:cid/artifacts/:aid', () =>
      HttpResponse.json({ ...MOCK_ARTIFACT_FULL, version,
                          content: version === 2 ? 'Some analysis here.' : 'Revised body.' })));

    const { rerender } = renderPanel();
    useUiStore.setState({ selectedArtifactId: 'a1' });
    await waitFor(() => expect(screen.getByText('Some analysis here.')).toBeInTheDocument());

    // What the SSE artifact event does: the summary's latest_version moves
    // ahead of the loaded one, and the panel should pull the new content.
    version = 3;
    rerender(<ArtifactPanel conversationId="c1"
      artifacts={[{ ...ARTIFACTS[0], latest_version: 3 }, SECOND] as ArtifactSummary[]} />);

    await waitFor(() => expect(screen.getByText('Revised body.')).toBeInTheDocument());
  });

  it('opens the editor with the loaded content and saves an edit', async () => {
    const user = userEvent.setup();
    renderPanel();
    useUiStore.setState({ selectedArtifactId: 'a1' });
    await waitFor(() => expect(screen.getByText('Some analysis here.')).toBeInTheDocument());

    await user.click(screen.getByText('Edit'));
    const box = screen.getByDisplayValue(/Some analysis here\./);
    await user.clear(box);
    await user.type(box, 'Edited body');

    await user.click(screen.getByText('Save'));
    await waitFor(() => expect(screen.queryByDisplayValue('Edited body')).not.toBeInTheDocument());
  });
});

// Shaped like what artifact_store.get_artifact_version returns for a file the
// sandbox holds: register_sandbox_artifact stores this sentence as the content.
function sandboxFile(filename: string, content_type: string, bytes = 12) {
  const external_url = `/api/artifacts/c1/hash-${filename}`;
  const summary: ArtifactSummary = {
    id: 'sb1', title: filename, filename, content_type, external_url,
    latest_version: 1, word_count: 0, byte_size: 0, source: 'sandbox_generated',
    created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
  };
  const full = {
    ...summary, version: 1,
    content: `[Sandbox-generated file: ${filename} (${content_type}, ${bytes} bytes). The actual bytes live in the sandbox container; fetch them via GET ${external_url}.]`,
  };
  return { summary, full, external_url };
}

// jsdom has no object URLs and does not follow a click on a link, so this stands
// in for the browser's side of saving a file.
function captureDownloads() {
  const saved: { name: string; blob: Blob }[] = [];
  let last: Blob;
  URL.createObjectURL = vi.fn((blob: Blob) => { last = blob; return 'blob:test'; });
  URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    saved.push({ name: this.download, blob: last });
  });
  return saved;
}

describe('ArtifactPanel downloads', () => {
  beforeEach(() => _resetUiStoreForTests());
  afterEach(() => vi.restoreAllMocks());

  async function openSandboxFile(filename: string, content_type: string) {
    const file = sandboxFile(filename, content_type);
    server.use(http.get('/api/chats/:cid/artifacts/:aid', () => HttpResponse.json(file.full)));
    renderPanel([file.summary]);
    useUiStore.setState({ selectedArtifactId: 'sb1' });
    await screen.findByRole('heading', { name: filename });
    return file;
  }

  it('saves the real bytes of a sandbox file under its own name', async () => {
    const saved = captureDownloads();
    const file = await openSandboxFile('model.ckpt', 'application/octet-stream');
    server.use(http.get(file.external_url, () => new HttpResponse('REAL-BYTES', {
      headers: { 'Content-Type': 'application/octet-stream' },
    })));

    await userEvent.click(await screen.findByRole('button', { name: 'Download' }));

    await waitFor(() => expect(saved).toHaveLength(1));
    expect(saved[0].name).toBe('model.ckpt');
    expect(await saved[0].blob.text()).toBe('REAL-BYTES');
  });

  it.each([
    ['plot.png', 'image/png'],
    ['data.csv', 'text/csv'],
    ['paper.pdf', 'application/pdf'],
  ])('does not double the extension of %s', async (filename, contentType) => {
    const saved = captureDownloads();
    const file = await openSandboxFile(filename, contentType);
    server.use(http.get(file.external_url, () => new HttpResponse('x')));
    await userEvent.click(await screen.findByRole('button', { name: 'Download' }));
    await waitFor(() => expect(saved).toHaveLength(1));
    expect(saved[0].name).toBe(filename);
  });

  it('shows a card with the file details instead of the placeholder sentence', async () => {
    await openSandboxFile('model.ckpt', 'application/octet-stream');
    const card = await screen.findByTestId('artifact-file-card');
    expect(card).toHaveTextContent('model.ckpt');
    expect(card).toHaveTextContent('File · 12 B');
    expect(card).toHaveTextContent('No preview for this file type');
    expect(screen.queryByText(/Sandbox-generated/)).not.toBeInTheDocument();
    expect(screen.queryByText(/sandbox container/)).not.toBeInTheDocument();
  });

  it('shows the card for a sandbox file with an empty body too', async () => {
    const file = sandboxFile('model.ckpt', 'application/octet-stream');
    server.use(http.get('/api/chats/:cid/artifacts/:aid', () => HttpResponse.json({ ...file.full, content: '' })));
    renderPanel([file.summary]);
    useUiStore.setState({ selectedArtifactId: 'sb1' });
    expect(await screen.findByTestId('artifact-file-card')).toHaveTextContent('model.ckpt');
  });

  it('shows a sandbox image, with a white ground for transparent plots', async () => {
    await openSandboxFile('plot.png', 'image/png');
    const img = await screen.findByRole('img', { name: 'plot.png' });
    expect(img).toHaveAttribute('src', '/api/artifacts/c1/hash-plot.png');
    expect(img).toHaveClass('bg-white');
    expect(screen.queryByTestId('artifact-file-card')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Download' })).toBeInTheDocument();
  });

  it('offers neither Copy nor PDF for a file it only has a description of', async () => {
    await openSandboxFile('model.ckpt', 'application/octet-stream');
    await screen.findByRole('button', { name: 'Download' });
    expect(screen.queryByRole('button', { name: 'Copy' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Save as PDF' })).not.toBeInTheDocument();
  });

  it('says so when the sandbox no longer has the file', async () => {
    const saved = captureDownloads();
    const file = await openSandboxFile('model.ckpt', 'application/octet-stream');
    server.use(http.get(file.external_url, () => new HttpResponse(null, { status: 404 })));
    await userEvent.click(await screen.findByRole('button', { name: 'Download' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not download this file');
    expect(saved).toHaveLength(0);
  });

  it('still saves a text body, with one extension', async () => {
    const saved = captureDownloads();
    const summary = { ...SECOND, id: 'a4', title: 'analysis.py', content_type: 'application/python' };
    server.use(http.get('/api/chats/:cid/artifacts/:aid', () =>
      HttpResponse.json({ ...summary, version: 1, content: 'print(1)' })));
    renderPanel([summary]);
    useUiStore.setState({ selectedArtifactId: 'a4' });
    await userEvent.click(await screen.findByRole('button', { name: 'Download' }));
    expect(saved[0].name).toBe('analysis.py');
  });
});

describe('ArtifactPanel failed actions', () => {
  beforeEach(() => _resetUiStoreForTests());
  afterEach(() => vi.restoreAllMocks());

  it('keeps the editor and the draft when a save fails', async () => {
    const user = userEvent.setup();
    server.use(http.patch('/api/chats/:cid/artifacts/:aid', () => new HttpResponse(null, { status: 500 })));
    renderPanel();
    useUiStore.setState({ selectedArtifactId: 'a1' });
    await screen.findByText('Some analysis here.');

    await user.click(screen.getByText('Edit'));
    const box = screen.getByDisplayValue(/Some analysis here\./);
    await user.clear(box);
    await user.type(box, 'My long draft');
    await user.click(screen.getByText('Save'));

    expect(await screen.findByRole('alert')).toHaveTextContent('Your edits are still here');
    expect(screen.getByDisplayValue('My long draft')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled();
  });

  it('keeps the artifact on screen when the print view is blocked', async () => {
    const user = userEvent.setup();
    vi.spyOn(window, 'open').mockReturnValue(null);
    renderPanel();
    useUiStore.setState({ selectedArtifactId: 'a1' });
    await screen.findByText('Some analysis here.');

    await user.click(screen.getByRole('button', { name: 'Save as PDF' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('allow pop-ups');
    expect(screen.getByText('Some analysis here.')).toBeInTheDocument();
  });

  it('offers a retry when the artifact cannot be read', async () => {
    let fail = true;
    server.use(http.get('/api/chats/:cid/artifacts/:aid', () => fail
      ? new HttpResponse(null, { status: 500 })
      : HttpResponse.json(MOCK_ARTIFACT_FULL)));
    renderPanel();
    useUiStore.setState({ selectedArtifactId: 'a1' });
    await screen.findByRole('alert');
    fail = false;
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Some analysis here.')).toBeInTheDocument();
  });
});

describe('ArtifactPanel after a save', () => {
  // The store is what tells the panel about versions, as it does under App.
  function StoreBackedPanel() {
    const artifacts = useChatStore(s => s.artifacts);
    return <ArtifactPanel artifacts={artifacts} conversationId="c1" />;
  }

  // A backend with a real version history: a PATCH makes a new latest version
  // and answers with no content, like artifact_store.update_artifact.
  function serveVersions() {
    const bodies: Record<number, string> = { 1: 'First body.', 2: 'Second body.' };
    let latest = 2;
    server.use(
      http.get('/api/chats/:cid/artifacts/:aid', ({ request }) => {
        const version = Number(new URL(request.url).searchParams.get('version')) || latest;
        return HttpResponse.json({ ...MOCK_ARTIFACT_FULL, latest_version: latest, version, content: bodies[version] });
      }),
      http.patch('/api/chats/:cid/artifacts/:aid', async ({ request }) => {
        const body = await request.json() as { content: string };
        latest += 1;
        bodies[latest] = body.content;
        return HttpResponse.json({ id: 'a1', title: MOCK_ARTIFACT_FULL.title, version: latest, source: 'model_written' });
      }),
    );
  }

  beforeEach(() => {
    _resetUiStoreForTests();
    useChatStore.setState({ artifacts: [ARTIFACTS[0]] });
    serveVersions();
  });
  afterEach(() => useChatStore.setState({ artifacts: [] }));

  async function saveEdit(user: ReturnType<typeof userEvent.setup>, text: string) {
    await user.click(screen.getByText('Edit'));
    const box = screen.getByRole('textbox', { name: /content/i });
    await user.clear(box);
    await user.type(box, text);
    await user.click(screen.getByText('Save'));
  }

  it('keeps Edit and moves the picker to the new version', async () => {
    const user = userEvent.setup();
    render(<StoreBackedPanel />);
    useUiStore.setState({ selectedArtifactId: 'a1' });
    await screen.findByText('Second body.');

    await saveEdit(user, 'Third body.');

    expect(await screen.findByText('Third body.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Edit' })).toBeInTheDocument();
    const picker = screen.getByRole('combobox') as HTMLSelectElement;
    expect(picker.value).toBe('3');
    expect(screen.getByRole('option', { name: 'v3 (latest)' })).toBeInTheDocument();
    expect(useChatStore.getState().artifacts[0].latest_version).toBe(3);
  });

  it('shows the new version even after the latest was picked from the list', async () => {
    const user = userEvent.setup();
    render(<StoreBackedPanel />);
    useUiStore.setState({ selectedArtifactId: 'a1' });
    await screen.findByText('Second body.');

    await user.selectOptions(screen.getByRole('combobox'), '1');
    await screen.findByText('First body.');
    await user.selectOptions(screen.getByRole('combobox'), '2');
    await screen.findByText('Second body.');

    await saveEdit(user, 'Third body.');

    expect(await screen.findByText('Third body.')).toBeInTheDocument();
  });
});

describe('ArtifactPanel protein artifacts', () => {
  const SPEC = { widget: 'protein', source: 'pdb', id: '1CRN', style: 'cartoon', color: 'chain', highlight: [] };
  const summary: ArtifactSummary = { ...SECOND, id: 'p1', title: '1CRN structure', content_type: 'application/vnd.munin.widget+json' };

  beforeEach(() => {
    _resetUiStoreForTests();
    server.use(
      http.get('/api/chats/:cid/artifacts/:aid', () => HttpResponse.json({ ...summary, version: 1, content: JSON.stringify(SPEC) })),
      http.get('/api/chats/:cid/artifacts/:aid/structure', () => HttpResponse.json({ format: 'cif', data: 'data_1CRN' })),
    );
  });
  afterEach(() => vi.restoreAllMocks());

  it('downloads the structure file, offers no Copy, and shows the title once', async () => {
    const saved = captureDownloads();
    renderPanel([summary]);
    useUiStore.setState({ selectedArtifactId: 'p1' });
    await screen.findByRole('heading', { name: '1CRN structure' });
    await screen.findByTestId('protein-viewer');
    expect(screen.queryByRole('button', { name: 'Copy' })).not.toBeInTheDocument();
    expect(screen.getAllByText('1CRN structure')).toHaveLength(1);

    await userEvent.click(screen.getByRole('button', { name: 'Download' }));
    await waitFor(() => expect(saved).toHaveLength(1));
    expect(saved[0].name).toBe('1CRN.cif');
    expect(await saved[0].blob.text()).toBe('data_1CRN');
  });

  it('reports a structure it cannot fetch', async () => {
    const saved = captureDownloads();
    renderPanel([summary]);
    useUiStore.setState({ selectedArtifactId: 'p1' });
    await screen.findByTestId('protein-viewer');
    // The viewer fetches the structure itself. Let that finish first, or on a
    // slow machine it also gets the failure below and the message shows twice.
    await screen.findByTitle('3D structure: 1CRN structure');
    server.use(http.get('/api/chats/:cid/artifacts/:aid/structure', () => HttpResponse.json(
      { detail: { error: { message: 'no PDB entry 1CRN' } } }, { status: 502 },
    )));
    await userEvent.click(await screen.findByRole('button', { name: 'Download' }));
    expect(await screen.findByText('no PDB entry 1CRN')).toBeInTheDocument();
    expect(saved).toHaveLength(0);
  });

  it('still copies and saves the text of a spec it cannot draw', async () => {
    const saved = captureDownloads();
    server.use(http.get('/api/chats/:cid/artifacts/:aid', () => HttpResponse.json({ ...summary, version: 1, content: '{"widget":"other"}' })));
    renderPanel([summary]);
    useUiStore.setState({ selectedArtifactId: 'p1' });
    expect(await screen.findByRole('button', { name: 'Copy' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Download' }));
    expect(saved[0].name).toBe('1CRN structure.json');
  });
});

describe('ArtifactPanel edit mode', () => {
  beforeEach(() => _resetUiStoreForTests());

  async function openEditor(user: ReturnType<typeof userEvent.setup>) {
    useUiStore.setState({ artifactPanelOpen: true });
    renderPanel();
    useUiStore.setState({ selectedArtifactId: 'a1' });
    await screen.findByText('Some analysis here.');
    await user.click(screen.getByRole('button', { name: 'Edit' }));
    return screen.getByRole('textbox', { name: 'Artifact content' });
  }

  it('takes the controls that act on the saved content away', async () => {
    await openEditor(userEvent.setup());
    for (const name of ['Copy', 'Download', 'Save as PDF']) {
      expect(screen.queryByRole('button', { name })).not.toBeInTheDocument();
    }
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
  });

  it('only allows saving a changed, non-empty draft', async () => {
    const user = userEvent.setup();
    const box = await openEditor(user);
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    await user.type(box, 'x');
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled();
    await user.clear(box);
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  });

  it('shows the size limit as an error', async () => {
    const user = userEvent.setup();
    const box = await openEditor(user);
    fireEvent.change(box, { target: { value: 'x'.repeat(500_001) } });
    expect(screen.getByText('500 KB limit exceeded!')).toHaveClass('text-error');
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  });

  it('leaves an unchanged draft without asking', async () => {
    const user = userEvent.setup();
    await openEditor(user);
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Edit' })).toBeInTheDocument();
  });

  it('asks before Cancel discards a changed draft, and can keep it', async () => {
    const user = userEvent.setup();
    const box = await openEditor(user);
    await user.type(box, ' more');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(screen.getByRole('dialog', { name: 'Discard your changes?' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Keep editing' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect((screen.getByRole('textbox', { name: 'Artifact content' }) as HTMLTextAreaElement).value).toContain(' more');

    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    await user.click(screen.getByRole('button', { name: 'Discard' }));
    expect(screen.queryByRole('textbox', { name: 'Artifact content' })).not.toBeInTheDocument();
    expect(screen.getByText('Some analysis here.')).toBeInTheDocument();
  });

  it('asks before Close and Back discard a changed draft', async () => {
    const user = userEvent.setup();
    const box = await openEditor(user);
    await user.type(box, ' more');

    await user.click(screen.getByRole('button', { name: 'Close artifacts' }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(useUiStore.getState().artifactPanelOpen).toBe(true);
    await user.click(screen.getByRole('button', { name: 'Keep editing' }));

    await user.click(screen.getByRole('button', { name: 'Back to artifact list' }));
    expect(useUiStore.getState().selectedArtifactId).toBe('a1');
    await user.click(screen.getByRole('button', { name: 'Discard' }));
    expect(useUiStore.getState().selectedArtifactId).toBeNull();
    expect(useUiStore.getState().artifactPanelOpen).toBe(true);
  });
});

describe('ArtifactPanel list', () => {
  beforeEach(() => _resetUiStoreForTests());

  it('uses friendly labels rather than MIME types', () => {
    const file = sandboxFile('model.ckpt', 'application/octet-stream');
    renderPanel([file.summary, { ...SECOND, content_type: 'application/vnd.munin.widget+json' }]);
    expect(screen.getByText(/File · Generated by code/)).toBeInTheDocument();
    expect(screen.getByText(/3D structure/)).toBeInTheDocument();
    expect(screen.queryByText(/application\//)).not.toBeInTheDocument();
    expect(screen.queryByText(/sandbox/)).not.toBeInTheDocument();
  });

  it('says what an artifact is when there are none', () => {
    renderPanel([]);
    expect(screen.getByText(/Reports, code and figures Munin creates in this chat appear here/)).toBeInTheDocument();
  });
});

describe('ArtifactPanel labels', () => {
  beforeEach(() => _resetUiStoreForTests());

  it('names its icon buttons and fields', async () => {
    const user = userEvent.setup();
    renderPanel();
    expect(screen.getByRole('button', { name: 'Close artifacts' })).toBeInTheDocument();
    useUiStore.setState({ selectedArtifactId: 'a1' });
    await screen.findByText('Some analysis here.');
    expect(screen.getByRole('button', { name: 'Back to artifact list' })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Version' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Edit' }));
    expect(screen.getByRole('textbox', { name: 'Artifact content' })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Change summary' })).toBeInTheDocument();
  });
});

describe('ArtifactPanel as a modal below xl', () => {
  beforeEach(() => {
    _resetUiStoreForTests();
    useUiStore.setState({ artifactPanelOpen: true });
    window.matchMedia = vi.fn(() => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })) as unknown as typeof window.matchMedia;
  });
  afterEach(() => {
    delete (window as Partial<Window>).matchMedia;
  });

  it('is a labelled dialog with focus on Close, and Escape closes it', async () => {
    const user = userEvent.setup();
    renderPanel();
    expect(screen.getByRole('dialog', { name: 'Artifacts' })).toBe(screen.getByTestId('artifact-panel'));
    expect(screen.getByRole('button', { name: 'Close artifacts' })).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(useUiStore.getState().artifactPanelOpen).toBe(false);
  });

  it('asks before Escape discards a changed draft', async () => {
    const user = userEvent.setup();
    renderPanel();
    useUiStore.setState({ selectedArtifactId: 'a1' });
    await screen.findByText('Some analysis here.');
    await user.click(screen.getByRole('button', { name: 'Edit' }));
    await user.type(screen.getByRole('textbox', { name: 'Artifact content' }), 'x');
    await user.keyboard('{Escape}');
    expect(screen.getByRole('dialog', { name: 'Discard your changes?' })).toBeInTheDocument();
    expect(useUiStore.getState().artifactPanelOpen).toBe(true);
  });

  it('is an ordinary column from xl up', () => {
    window.matchMedia = vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })) as unknown as typeof window.matchMedia;
    renderPanel();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('ArtifactPanel SVG', () => {
  beforeEach(() => _resetUiStoreForTests());

  it('draws a model-written SVG instead of showing its source', async () => {
    const svg: ArtifactSummary = { ...SECOND, id: 'a3', title: 'Pathway', content_type: 'image/svg+xml' };
    server.use(http.get('/api/chats/:cid/artifacts/:aid', () =>
      HttpResponse.json({ ...svg, version: 1, content: '<svg xmlns="http://www.w3.org/2000/svg"/>' })));
    renderPanel([svg]);
    useUiStore.setState({ selectedArtifactId: 'a3' });
    const img = await screen.findByRole('img', { name: 'Pathway' });
    expect(img.getAttribute('src')).toMatch(/^data:image\/svg\+xml/);
    // Transparent drawings need an opaque backing to read in both themes.
    expect(img).toHaveClass('bg-white');
  });

  it('colours a code artifact from the --code-* variables, so it follows the theme', async () => {
    const code: ArtifactSummary = { ...SECOND, id: 'a5', title: 'analysis.py', content_type: 'application/python' };
    server.use(http.get('/api/chats/:cid/artifacts/:aid', () =>
      HttpResponse.json({ ...code, version: 1, content: '# note\nx = "hi"' })));
    renderPanel([code]);
    useUiStore.setState({ selectedArtifactId: 'a5' });
    const comment = await screen.findByText('# note');
    expect(comment.style.color).toBe('var(--code-comment)');
    const pre = comment.closest('pre')!;
    expect(pre.style.background).toBe('var(--code-bg)');
  });
});
