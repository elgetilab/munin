import { createRef } from 'react';
import { fireEvent, render, screen, waitFor, act } from '@testing-library/react';
import { ChatInput, type ChatInputHandle } from './ChatInput';
import { useDeepResearchStore } from '../stores/deepResearchStore';

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
vi.mock('../lib/api', () => ({ uploadDocument: uploadDocumentMock, cancelUpload: () => Promise.resolve(), startDeepResearch: startDeepResearchMock }));

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

describe('Sending during an upload', () => {
  // An upload that stays open until the test says how it ends.
  function pendingUpload() {
    let finish!: (doc: unknown) => void;
    uploadDocumentMock.mockImplementationOnce(((_f: File, _c: string, _p: unknown, signal: AbortSignal) =>
      new Promise((resolve, reject) => {
        finish = resolve;
        signal.addEventListener('abort', () => reject(new DOMException('cancelled', 'AbortError')));
      })) as never);
    return (doc: unknown) => finish(doc);
  }
  const DOC = { document_id: 'doc-1', filename: 'paper.pdf', status: 'embedded', chunks: 3, upload_time: '' };

  it('holds Send until the upload ends, so the document goes with the message', async () => {
    const finish = pendingUpload();
    const onSend = vi.fn();
    const onSendMultimodal = vi.fn();
    const ref = createRef<ChatInputHandle>();
    render(<ChatInput ref={ref} onSend={onSend} onSendMultimodal={onSendMultimodal} onStop={() => {}} isStreaming={false} persona={null} conversationId="conv-1" />);
    await act(async () => { void ref.current!.handleDroppedFiles([file('paper.pdf', 'application/pdf')]); });
    type('Summarise this');

    const send = screen.getByTestId('composer-send');
    expect(send).toBeDisabled();
    expect(send).toHaveAttribute('title', 'Waiting for upload to finish');
    key('Enter');
    expect(onSend).not.toHaveBeenCalled();

    await act(async () => { finish(DOC); });
    expect(send).toBeEnabled();
    key('Enter');
    expect(onSendMultimodal).toHaveBeenCalledWith([
      { type: 'text', text: 'Summarise this' },
      { type: 'document', document_id: 'doc-1', filename: 'paper.pdf' },
    ]);
  });

  it('Cancel upload really cancels: no banner comes back and nothing attaches', async () => {
    pendingUpload();
    const ref = renderChatInput();
    await act(async () => { void ref.current!.handleDroppedFiles([file('paper.pdf', 'application/pdf')]); });

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Cancel upload' })); });

    expect(screen.queryByText(/Uploading paper\.pdf/)).not.toBeInTheDocument();
    expect(screen.queryByText('paper.pdf')).not.toBeInTheDocument();
    expect(screen.queryByText(/cancelled/i)).not.toBeInTheDocument();
    expect(screen.getByTestId('composer-send')).toBeDisabled(); // nothing to send
    type('Now send');
    expect(screen.getByTestId('composer-send')).toBeEnabled();
  });
});

describe('Composer accessibility', () => {
  it('names the composer and the + menu, and reports its state', () => {
    renderChatInput();
    expect(screen.getByRole('textbox', { name: 'Message Munin' })).toBeInTheDocument();
    const attach = screen.getByRole('button', { name: 'Attach' });
    expect(attach).toHaveAttribute('aria-haspopup', 'menu');
    expect(attach).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(attach);
    expect(attach).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('menuitemcheckbox', { name: /Deep Research/ })).toHaveAttribute('aria-checked', 'false');
    expect(screen.getByRole('menuitem', { name: 'Upload file' })).toHaveFocus();
  });

  it('names the remove buttons on tag chips by what they remove', () => {
    const setActiveTags = vi.fn();
    workspaceState.tagCatalog = CATALOG;
    workspaceState.activeTags = [{ kind: 'group', value: 'zeitler' }];
    workspaceState.setActiveTags = setActiveTags;
    renderChatInput();
    fireEvent.click(screen.getByRole('button', { name: 'Remove tag Zeitler Group' }));
    expect(setActiveTags).toHaveBeenCalledWith([]);
  });

  // index.css draws the full ring on the bubble only for this attribute; any
  // other focus gets the gentle tint. A textarea is :focus-visible either way.
  it('marks focus that came from the keyboard, and not focus from a click', () => {
    renderChatInput();
    fireEvent.pointerDown(document.body);
    act(() => composer().focus());
    expect(composer()).not.toHaveAttribute('data-keyboard-focus');
    act(() => composer().blur());

    fireEvent.keyDown(document.body, { key: 'Tab' });
    act(() => composer().focus());
    expect(composer()).toHaveAttribute('data-keyboard-focus');
    // Typing a reply is not a reason to change how focus looks.
    fireEvent.pointerDown(composer());
    expect(composer()).toHaveAttribute('data-keyboard-focus');
    act(() => composer().blur());
    expect(composer()).not.toHaveAttribute('data-keyboard-focus');
  });
});

describe('Disabled menu items explain themselves', () => {
  it('shows the reason as text in temporary chats', () => {
    workspaceState.isEphemeral = true;
    renderChatInput();
    fireEvent.click(screen.getByTestId('composer-attach'));
    expect(screen.getAllByText('Not available in temporary chats')).toHaveLength(2);
    expect(screen.getByRole('menuitem', { name: /Upload file/ })).toBeDisabled();
    expect(screen.queryByText(/\(disabled\)/)).not.toBeInTheDocument();
  });

  it('describes Deep Research in the menu when it is available', () => {
    renderChatInput();
    fireEvent.click(screen.getByTestId('composer-attach'));
    expect(screen.getByText('Slower, multi-source report that runs in the background')).toBeInTheDocument();
  });

  it('lets the armed line wrap instead of truncating it', () => {
    renderChatInput();
    fireEvent.click(screen.getByTestId('composer-attach'));
    fireEvent.click(screen.getByTestId('attach-deep-research'));
    expect(screen.getByTestId('dr-armed').querySelector('.truncate')).toBeNull();
  });
});

describe('Deep Research running', () => {
  afterEach(() => useDeepResearchStore.getState().clear());

  function renderActive() {
    render(<ChatInput onSend={() => {}} onStop={() => {}} isStreaming={false} researchActive persona={null} conversationId="conv-1" />);
  }

  it('says it once, not in both the note and the placeholder', () => {
    renderActive();
    expect(screen.getByTestId('dr-blocked')).toHaveTextContent('Deep Research is running');
    expect(composer()).not.toHaveAttribute('placeholder');
  });

  it('says waiting while the job is queued', () => {
    useDeepResearchStore.getState().startJob('j1', 'Q', 'conv-1', 0);
    renderActive();
    expect(screen.getByTestId('dr-blocked')).toHaveTextContent(/waiting for another research job/i);
  });
});

describe('Knowledge lists', () => {
  it('chips show the catalog name and keep the slug in a title', () => {
    workspaceState.tagCatalog = CATALOG;
    workspaceState.activeTags = [{ kind: 'group', value: 'zeitler' }, { kind: 'topic', value: 'gone' }];
    renderChatInput();
    const chip = screen.getByText('Zeitler Group');
    expect(chip).toHaveAttribute('title', '#zeitler');
    expect(screen.getByText('#gone')).toBeInTheDocument();
  });

  it('the picker and the tag menu both count papers, with the right plural', () => {
    workspaceState.tagCatalog = CATALOG;
    renderChatInput();
    type('#');
    expect(screen.getByText('412 papers')).toBeInTheDocument();
    expect(screen.getByText('1 paper')).toBeInTheDocument();
    expect(screen.getByText('3 results')).toBeInTheDocument();
    key('Escape');

    openPicker();
    expect(screen.getByText('412 papers')).toBeInTheDocument();
    expect(screen.getByText('1 paper')).toBeInTheDocument();
  });

  function openPicker() {
    fireEvent.click(screen.getByTestId('composer-attach'));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Attach knowledge' }));
    return screen.getByRole('textbox', { name: /search groups/i });
  }

  it('the picker supports arrow keys and Enter', () => {
    const setActiveTags = vi.fn();
    workspaceState.tagCatalog = CATALOG;
    workspaceState.setActiveTags = setActiveTags;
    renderChatInput();
    const search = openPicker();
    fireEvent.keyDown(search, { key: 'ArrowDown' });
    fireEvent.keyDown(search, { key: 'Enter' });
    expect(setActiveTags).toHaveBeenCalledWith([{ kind: 'contributor', value: 'ada' }]);
    expect(screen.queryByRole('dialog', { name: 'Attach knowledge' })).not.toBeInTheDocument();
  });

  it('Escape in the picker closes only it, back onto the + button', () => {
    workspaceState.tagCatalog = CATALOG;
    renderChatInput();
    const search = openPicker();
    fireEvent.keyDown(search, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'Attach knowledge' })).not.toBeInTheDocument();
    expect(screen.getByTestId('composer-attach')).toHaveFocus();
  });

  it('caps each kind separately and says when there are more', () => {
    const many = (n: number) => Array.from({ length: n }, (_, i) => i);
    workspaceState.tagCatalog = {
      groups: many(30).map(i => ({ slug: `g${i}`, display_name: `Group ${i}`, paper_count: 1 })),
      topics: [],
      contributors: [{ username: 'ada', display_name: 'Ada L', paper_count: 1 }],
    };
    renderChatInput();
    type('#');
    // The groups must not push the only contributor out of the list.
    expect(screen.getByText('Ada L')).toBeInTheDocument();
    expect(screen.getByText('Showing 9 of 31, keep typing to narrow')).toBeInTheDocument();
    expect(screen.getByText('31 results')).toBeInTheDocument();
  });

  it('shows wording rather than internals once a document is uploaded', async () => {
    const ref = renderChatInput();
    await act(async () => { await ref.current!.handleDroppedFiles([file('paper.pdf', 'application/pdf')]); });
    expect(await screen.findByText(/ready to send/)).toBeInTheDocument();
    expect(screen.queryByText(/chunks/)).not.toBeInTheDocument();
  });
});

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

  it('blocks document upload in temporary chats', async () => {
    workspaceState.isEphemeral = true;
    const ref = renderChatInput();
    await act(async () => { await ref.current!.handleDroppedFiles([file('paper.pdf', 'application/pdf')]); });
    expect(uploadDocumentMock).not.toHaveBeenCalled();
    expect(screen.getByText(/disabled in temporary/i)).toBeInTheDocument();
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

  it('keeps a drop of many images to the limit of 3 and says so', async () => {
    const ref = renderChatInput();
    await act(async () => {
      await ref.current!.handleDroppedFiles(
        ['a', 'b', 'c', 'd', 'e'].map(n => file(`${n}.png`, 'image/png')),
      );
    });
    await waitFor(() => expect(screen.getAllByRole('img', { name: /\.png$/ })).toHaveLength(3));
    expect(screen.getByText('Maximum 3 images')).toBeInTheDocument();
  });

  it('removes only the image asked for when several arrive in one drop', async () => {
    const ref = renderChatInput();
    await act(async () => {
      await ref.current!.handleDroppedFiles([file('a.png', 'image/png'), file('b.png', 'image/png')]);
    });
    await waitFor(() => expect(screen.getByAltText('b.png')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Remove image a.png' }));
    expect(screen.queryByAltText('a.png')).not.toBeInTheDocument();
    expect(screen.getByAltText('b.png')).toBeInTheDocument();
  });
});

describe('Composer icons', () => {
  it('uses line icons, not emoji, in the slash menu', () => {
    renderChatInput();
    type('/');
    const row = screen.getByRole('button', { name: /\/research/ });
    expect(row.querySelector('svg')).not.toBeNull();
    expect(row.textContent).not.toMatch(/\p{Extended_Pictographic}/u);
  });

  it('uses a line icon, not emoji, once a document is uploaded', async () => {
    const ref = renderChatInput();
    await act(async () => { await ref.current!.handleDroppedFiles([file('paper.pdf', 'application/pdf')]); });
    const banner = (await screen.findByText(/ready to send/)).parentElement!;
    expect(banner.querySelector('svg')).not.toBeNull();
    expect(banner.textContent).not.toMatch(/\p{Extended_Pictographic}/u);
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

  it('with the knowledge picker open, + closes it rather than opening the menu under it', () => {
    workspaceState.tagCatalog = CATALOG;
    renderChatInput();
    const attach = screen.getByTestId('composer-attach');
    fireEvent.click(attach);
    fireEvent.click(screen.getByRole('menuitem', { name: 'Attach knowledge' }));
    expect(screen.getByRole('dialog', { name: 'Attach knowledge' })).toBeInTheDocument();

    fireEvent.mouseDown(attach);
    fireEvent.click(attach);
    expect(screen.queryByRole('dialog', { name: 'Attach knowledge' })).not.toBeInTheDocument();
    expect(screen.getByRole('menu', { name: 'Attach' })).toBeInTheDocument();
  });

  it('leaves focus alone on Escape when the menu is closed', () => {
    renderChatInput();
    const box = screen.getByTestId('composer-textarea');
    box.focus();
    fireEvent.keyDown(box, { key: 'Escape' });
    expect(box).toHaveFocus();
  });
});

describe('ChatInput mode chip', () => {
  const renderWith = (onSend = vi.fn(), conversationId: string | null = 'conv-1') => {
    const view = render(<ChatInput onSend={onSend} onStop={() => {}} isStreaming={false} persona={null} conversationId={conversationId} />);
    return { onSend, ...view };
  };
  const pick = (label: string) => {
    fireEvent.click(screen.getByTestId('mode-chip'));
    fireEvent.click(screen.getByRole('menuitemradio', { name: new RegExp(`^${label}`) }));
  };

  it('starts on Auto and sends the message as typed', () => {
    const { onSend } = renderWith();
    expect(screen.getByTestId('mode-chip')).toHaveAccessibleName('Mode: Auto');
    type('What is a GPCR?');
    key('Enter');
    expect(onSend).toHaveBeenCalledWith('What is a GPCR?');
  });

  it('sends with the chosen mode\'s command, and keeps the mode for the next message', () => {
    const { onSend } = renderWith();
    pick('Research');
    expect(screen.getByTestId('mode-chip')).toHaveAccessibleName('Mode: Research');
    type('GPCR desensitisation');
    key('Enter');
    expect(onSend).toHaveBeenLastCalledWith('/research GPCR desensitisation');
    type('and arrestins?');
    key('Enter');
    expect(onSend).toHaveBeenLastCalledWith('/research and arrestins?');
  });

  it('marks the chosen mode in its menu', () => {
    renderWith();
    pick('Code');
    fireEvent.click(screen.getByTestId('mode-chip'));
    expect(screen.getByRole('menuitemradio', { name: /^Code/ })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('menuitemradio', { name: /^Auto/ })).toHaveAttribute('aria-checked', 'false');
  });

  it('shows a typed command, which wins over the chip on send', () => {
    const { onSend } = renderWith();
    pick('Research');
    type('/code fix this loop');
    expect(screen.getByTestId('mode-chip')).toHaveAccessibleName('Mode: Code, set by the typed command');
    key('Enter');
    expect(onSend).toHaveBeenCalledWith('/code fix this loop');
  });

  it('goes back to Auto in another chat, but not when a new chat gets its id', () => {
    const onSend = vi.fn();
    const { rerender } = render(<ChatInput onSend={onSend} onStop={() => {}} isStreaming={false} persona={null} conversationId={null} />);
    pick('Code');
    rerender(<ChatInput onSend={onSend} onStop={() => {}} isStreaming={false} persona={null} conversationId="new-id" />);
    expect(screen.getByTestId('mode-chip')).toHaveAccessibleName('Mode: Code');
    rerender(<ChatInput onSend={onSend} onStop={() => {}} isStreaming={false} persona={null} conversationId="other" />);
    expect(screen.getByTestId('mode-chip')).toHaveAccessibleName('Mode: Auto');
  });
});
