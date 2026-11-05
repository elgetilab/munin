import { createRef } from 'react';
import { fireEvent, render, screen, waitFor, act } from '@testing-library/react';
import { ChatInput, type ChatInputHandle } from './ChatInput';

// Mutable store state shared with the mocked selectors (hoisted so the
// vi.mock factories can reference it).
const { userState, workspaceState, uploadDocumentMock, startDeepResearchMock } = vi.hoisted(() => ({
  userState: { personas: [] as unknown[], selectedPersona: 'chat', setSelectedPersona: () => {} },
  workspaceState: {
    isEphemeral: false,
    tagCatalog: null as unknown,
    activeTags: [] as unknown[],
    setActiveTags: (() => {}) as (tags: unknown[]) => void,
  },
  startDeepResearchMock: vi.fn(async () => ({ job_id: 'job-1', conversation_id: 'conv-1', created_conversation: false })),
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
vi.mock('../lib/api', () => ({ uploadDocument: uploadDocumentMock, startDeepResearch: startDeepResearchMock }));

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
  startDeepResearchMock.mockClear();
  workspaceState.isEphemeral = false;
  workspaceState.tagCatalog = null;
  workspaceState.activeTags = [];
  workspaceState.setActiveTags = () => {};
});

const CATALOG = {
  groups: [{ slug: 'zeitler', display_name: 'Zeitler Group', paper_count: 412 }],
  topics: [{ slug: 'nmr', label: 'NMR', paper_count: 30 }],
  contributors: [{ username: 'ada', display_name: 'Ada L', paper_count: 1 }],
};

const composer = () => screen.getByTestId('composer-textarea') as HTMLTextAreaElement;
const type = (value: string) => fireEvent.change(composer(), { target: { value } });
const key = (k: string) => fireEvent.keyDown(composer(), { key: k });

describe('Deep Research with attachments', () => {
  function arm() {
    fireEvent.click(screen.getByTestId('composer-attach'));
    fireEvent.click(screen.getByTestId('attach-deep-research'));
  }

  it('blocks sending and says why, rather than sending a chat turn', async () => {
    const onSendMultimodal = vi.fn();
    const ref = createRef<ChatInputHandle>();
    render(<ChatInput ref={ref} onSend={() => {}} onSendMultimodal={onSendMultimodal} onStop={() => {}} isStreaming={false} persona={null} conversationId="conv-1" />);
    arm();
    await act(async () => { await ref.current!.handleDroppedFiles([file('shot.png', 'image/png')]); });
    await waitFor(() => expect(screen.getByAltText('shot.png')).toBeInTheDocument());
    type('What is this?');

    expect(screen.getByTestId('dr-armed')).toHaveTextContent(/cannot use attachments/i);
    expect(screen.getByTestId('composer-send')).toBeDisabled();

    key('Enter');
    expect(onSendMultimodal).not.toHaveBeenCalled();
    expect(startDeepResearchMock).not.toHaveBeenCalled();
    expect(composer()).toHaveValue('What is this?');
  });

  it('allows sending again once the attachment is removed', async () => {
    const ref = renderChatInput();
    arm();
    await act(async () => { await ref.current!.handleDroppedFiles([file('shot.png', 'image/png')]); });
    await waitFor(() => expect(screen.getByAltText('shot.png')).toBeInTheDocument());
    type('Question');
    fireEvent.click(screen.getByRole('button', { name: /remove image shot\.png/i }));

    expect(screen.getByTestId('composer-send')).toBeEnabled();
    await act(async () => { key('Enter'); });
    expect(startDeepResearchMock).toHaveBeenCalledWith('conv-1', 'Question');
  });
});

describe('Image remove button', () => {
  it('is visible unless the device can hover, and shows on keyboard focus', async () => {
    const ref = renderChatInput();
    await act(async () => { await ref.current!.handleDroppedFiles([file('shot.png', 'image/png')]); });
    const remove = await screen.findByRole('button', { name: /remove image/i });
    expect(remove.className).toMatch(/(^| )opacity-100( |$)/);
    expect(remove.className).toContain('[@media(hover:hover)]:opacity-0');
    expect(remove.className).toContain('[@media(hover:hover)]:focus-visible:opacity-100');
  });
});

describe('Tag autocomplete', () => {
  const rowLabels = () =>
    screen.getAllByRole('button').map(b => b.textContent ?? '').filter(t => /papers?$/.test(t));

  it('arrow keys follow the order the rows are drawn in', () => {
    const setActiveTags = vi.fn();
    workspaceState.tagCatalog = CATALOG;
    workspaceState.setActiveTags = setActiveTags;
    renderChatInput();
    type('#');
    // Drawn: group, contributor, topic.
    expect(rowLabels().map(t => t.replace(/\d.*$/, ''))).toEqual(['groupZeitler Group', 'personAda L', 'topicNMR']);

    key('ArrowDown');
    key('Enter');
    expect(setActiveTags).toHaveBeenCalledWith([{ kind: 'contributor', value: 'ada' }]);
  });

  it('resets the highlight when the query narrows, so Enter still selects', () => {
    const setActiveTags = vi.fn();
    workspaceState.tagCatalog = CATALOG;
    workspaceState.setActiveTags = setActiveTags;
    renderChatInput();
    type('#');
    key('ArrowDown');
    key('ArrowDown');
    type('#zei');
    key('Enter');
    expect(setActiveTags).toHaveBeenCalledWith([{ kind: 'group', value: 'zeitler' }]);
  });

  it('scrolls the highlighted row into view', () => {
    workspaceState.tagCatalog = CATALOG;
    renderChatInput();
    type('#');
    const spy = vi.spyOn(Element.prototype, 'scrollIntoView');
    key('ArrowDown');
    expect(spy).toHaveBeenCalledWith({ block: 'nearest' });
    spy.mockRestore();
  });

  it('Escape closes the panel when nothing matches', () => {
    workspaceState.tagCatalog = CATALOG;
    renderChatInput();
    type('hello #nothing');
    expect(screen.getByText(/No matches for/)).toBeInTheDocument();
    key('Escape');
    expect(screen.queryByText(/No matches for/)).not.toBeInTheDocument();
    expect(composer()).toHaveValue('hello ');
  });

  it.each(['C#', 'sample#', 'sample#1'])('does not open for a # inside a word: %s', (text) => {
    const onSend = vi.fn();
    workspaceState.tagCatalog = CATALOG;
    render(<ChatInput onSend={onSend} onStop={() => {}} isStreaming={false} persona={null} conversationId="conv-1" />);
    type(text);
    expect(screen.queryByText('Type to search knowledge...')).not.toBeInTheDocument();
    expect(screen.queryByText(/No matches for/)).not.toBeInTheDocument();
    key('Enter');
    expect(onSend).toHaveBeenCalledWith(text);
  });

  it('opens after a space and keeps that space when a tag is chosen', () => {
    const setActiveTags = vi.fn();
    workspaceState.tagCatalog = CATALOG;
    workspaceState.setActiveTags = setActiveTags;
    renderChatInput();
    type('about #zei');
    key('Enter');
    expect(setActiveTags).toHaveBeenCalledTimes(1);
    expect(composer()).toHaveValue('about ');
  });

  it('closes when the caret moves away from the #', () => {
    workspaceState.tagCatalog = CATALOG;
    renderChatInput();
    type('hello #');
    expect(screen.getByText('Type to search knowledge...')).toBeInTheDocument();
    composer().setSelectionRange(2, 2);
    fireEvent.select(composer());
    expect(screen.queryByText('Type to search knowledge...')).not.toBeInTheDocument();
  });
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

describe('ChatInput + menu', () => {
  it('closes on Escape and returns focus to the + button', () => {
    renderChatInput();
    const attach = screen.getByTestId('composer-attach');
    fireEvent.click(attach);
    expect(screen.getByText('Upload image')).toBeInTheDocument();

    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(screen.queryByText('Upload image')).not.toBeInTheDocument();
    expect(attach).toHaveFocus();
  });

  it('leaves focus alone on Escape when the menu is closed', () => {
    renderChatInput();
    const box = screen.getByTestId('composer-textarea');
    box.focus();
    fireEvent.keyDown(box, { key: 'Escape' });
    expect(box).toHaveFocus();
  });
});
