import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../test/msw-server';
import { MOCK_ARTIFACTS, MOCK_ARTIFACT_FULL } from '../test/msw-handlers';
import { ArtifactPanel } from './ArtifactPanel';
import { useUiStore, _resetUiStoreForTests } from '../stores/uiStore';
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
