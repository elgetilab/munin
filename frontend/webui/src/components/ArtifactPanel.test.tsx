import { render, screen, waitFor } from '@testing-library/react';
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

  it('offers neither Copy nor PDF for a file it only has a description of', async () => {
    await openSandboxFile('model.ckpt', 'application/octet-stream');
    await screen.findByRole('button', { name: 'Download' });
    expect(screen.queryByRole('button', { name: 'Copy' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'PDF' })).not.toBeInTheDocument();
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

    await user.click(screen.getByRole('button', { name: 'PDF' }));

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
  });
});
