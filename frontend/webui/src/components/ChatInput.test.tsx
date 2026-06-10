import { createRef } from 'react';
import { render, screen, waitFor, act } from '@testing-library/react';
import { ChatInput, type ChatInputHandle } from './ChatInput';

// Mutable store state shared with the mocked selectors (hoisted so the
// vi.mock factories can reference it).
const { userState, workspaceState, uploadDocumentMock } = vi.hoisted(() => ({
  userState: { personas: [] as unknown[], selectedPersona: 'chat', setSelectedPersona: () => {} },
  workspaceState: { isEphemeral: false, tagCatalog: null, activeTags: [] as unknown[], setActiveTags: () => {} },
  uploadDocumentMock: vi.fn(async () => ({
    document_id: 'doc-1', filename: 'paper.pdf', status: 'embedded', chunks: 3, upload_time: '',
  })),
}));

vi.mock('../stores/userStore', () => ({
  useUserStore: (selector: (s: typeof userState) => unknown) => selector(userState),
}));
vi.mock('../stores/workspaceStore', () => ({
  useWorkspaceStore: (selector: (s: typeof workspaceState) => unknown) => selector(workspaceState),
}));
vi.mock('../lib/api', () => ({ uploadDocument: uploadDocumentMock }));

function renderChatInput() {
  const ref = createRef<ChatInputHandle>();
  render(
    <ChatInput
      ref={ref}
      onSend={() => {}}
      onSendMultimodal={() => {}}
      onStop={() => {}}
      isStreaming={false}
      persona={null}
      conversationId="conv-1"
    />,
  );
  return ref;
}

const file = (name: string, type: string) => new File(['data'], name, { type });

beforeEach(() => {
  uploadDocumentMock.mockClear();
  workspaceState.isEphemeral = false;
});

describe('ChatInput drag-and-drop routing (handleDroppedFiles)', () => {
  it('routes a document to the upload pipeline', async () => {
    const ref = renderChatInput();
    await act(async () => { await ref.current!.handleDroppedFiles([file('paper.pdf', 'application/pdf')]); });
    expect(uploadDocumentMock).toHaveBeenCalledTimes(1);
    expect(uploadDocumentMock.mock.calls[0][0]).toBeInstanceOf(File);
  });

  it('routes an image to the inline-image pipeline (thumbnail), not upload', async () => {
    const ref = renderChatInput();
    await act(async () => { await ref.current!.handleDroppedFiles([file('shot.png', 'image/png')]); });
    await waitFor(() => expect(screen.getByAltText('shot.png')).toBeInTheDocument());
    expect(uploadDocumentMock).not.toHaveBeenCalled();
  });

  it('skips unsupported types with an error and no upload', async () => {
    const ref = renderChatInput();
    await act(async () => { await ref.current!.handleDroppedFiles([file('archive.zip', 'application/zip')]); });
    expect(screen.getByText(/Some files were skipped/i)).toBeInTheDocument();
    expect(uploadDocumentMock).not.toHaveBeenCalled();
  });

  it('blocks document upload in ephemeral chats', async () => {
    workspaceState.isEphemeral = true;
    const ref = renderChatInput();
    await act(async () => { await ref.current!.handleDroppedFiles([file('paper.pdf', 'application/pdf')]); });
    expect(uploadDocumentMock).not.toHaveBeenCalled();
    expect(screen.getByText(/disabled in ephemeral/i)).toBeInTheDocument();
  });

  it('routes a mixed drop: image thumbnail + document upload', async () => {
    const ref = renderChatInput();
    await act(async () => {
      await ref.current!.handleDroppedFiles([
        file('shot.png', 'image/png'),
        file('paper.pdf', 'application/pdf'),
      ]);
    });
    await waitFor(() => expect(screen.getByAltText('shot.png')).toBeInTheDocument());
    expect(uploadDocumentMock).toHaveBeenCalledTimes(1);
  });
});
