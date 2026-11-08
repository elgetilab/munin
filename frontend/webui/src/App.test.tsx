import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { onTestFinished } from 'vitest';
import { http, HttpResponse } from 'msw';
import { server } from './test/msw-server';
import { MOCK_CONVERSATION, MOCK_PROJECTS } from './test/msw-handlers';
import App from './App';
import { _resetChatStoreForTests, useChatStore } from './stores/chatStore';
import { _resetUiStoreForTests, useUiStore } from './stores/uiStore';
import { _resetUserStoreForTests, useUserStore } from './stores/userStore';
import { _resetWorkspaceStoreForTests, useWorkspaceStore } from './stores/workspaceStore';
import { _resetSessionForTests, reportApiFailure } from './lib/session';
import { AUTH_BASE } from './lib/urls';

// App is the one place routing, the panels and the sidebar meet, so these run
// the whole shell against the mocked network.

const PROJECT = MOCK_PROJECTS.projects[0];

function stubMatchMedia(width = 1280) {
  vi.stubGlobal('matchMedia', (query: string) => {
    const below = /width < (\d+(?:\.\d+)?)rem/.exec(query);
    return {
      matches: below ? width < Number(below[1]) * 16 : false,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    };
  });
}

function openAt(path: string) {
  window.history.replaceState(null, '', path);
  return render(<App />);
}

const projectRow = async () =>
  (await screen.findByText(PROJECT.name)).closest('.relative') as HTMLElement;

beforeEach(() => {
  stubMatchMedia();
  _resetChatStoreForTests();
  _resetUiStoreForTests();
  _resetUserStoreForTests();
  _resetWorkspaceStoreForTests();
  _resetSessionForTests();
  useUiStore.setState({ sidebarOpen: true });
  server.use(
    http.get('/api/tags', () => new HttpResponse(null, { status: 500 })),
    http.get('/api/chats/:cid/research', () => HttpResponse.json({ job: null })),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.history.replaceState(null, '', '/');
});

describe('direct links', () => {
  it('keeps /knowledge in the address bar', async () => {
    const pushes = vi.spyOn(window.history, 'pushState');
    openAt('/knowledge/group/lab');

    await waitFor(() => expect(useUiStore.getState().showKnowledgePage).toBe('group/lab'));
    expect(window.location.pathname).toBe('/knowledge/group/lab');
    expect(pushes).not.toHaveBeenCalled();
  });

  it('opens /c/<id> without adding history entries', async () => {
    const pushes = vi.spyOn(window.history, 'pushState');
    openAt('/c/c1');

    await waitFor(() => expect(useChatStore.getState().conversationId).toBe(MOCK_CONVERSATION.id));
    await screen.findByText('Hi there!');
    expect(window.location.pathname).toBe('/c/c1');
    expect(pushes).not.toHaveBeenCalled();
  });

  it('leaves the address alone when /c/<id> fails to load', async () => {
    openAt('/c/missing');

    await waitFor(() => expect(useChatStore.getState().error).toBeTruthy());
    expect(window.location.pathname).toBe('/c/missing');
  });

  it("the Knowledge page's Chat link goes back to the loaded chat's URL", async () => {
    const user = userEvent.setup();
    useChatStore.setState({ conversationId: 'c1' });
    openAt('/knowledge');
    await waitFor(() => expect(useUiStore.getState().showKnowledgePage).toBe(true));

    await user.click(screen.getByRole('link', { name: 'Chat' }));

    expect(useUiStore.getState().showKnowledgePage).toBe(false);
    expect(window.location.pathname).toBe('/c/c1');
  });
});

describe('sidebar actions while Admin is open', () => {
  beforeEach(() => {
    useUiStore.setState({ showAdmin: true });
    useUserStore.setState({ isAdmin: true });
  });

  it('Project settings replaces the Admin page', async () => {
    const user = userEvent.setup();
    openAt('/');

    await user.click((await projectRow()).querySelector('[title="More"]')!);
    await user.click(await screen.findByText('Project settings'));

    expect(useUiStore.getState().showAdmin).toBe(false);
    expect(useWorkspaceStore.getState().editingProject?.id).toBe(PROJECT.id);
    expect(screen.getByRole('heading', { name: 'Project Settings' })).toBeInTheDocument();
  });

  it('New chat in project leaves the Admin page', async () => {
    const user = userEvent.setup();
    openAt('/');

    await user.click((await projectRow()).querySelector('[title="More"]')!);
    await user.click(await screen.findByText('New chat in project'));

    expect(useUiStore.getState().showAdmin).toBe(false);
    expect(useWorkspaceStore.getState().activeProjectId).toBe(PROJECT.id);
  });
});

describe('phone drawer', () => {
  it('closes when Project settings opens from it', async () => {
    const user = userEvent.setup();
    const width = window.innerWidth;
    window.innerWidth = 390;
    try {
      openAt('/');
      await user.click((await projectRow()).querySelector('[title="More"]')!);
      await user.click(await screen.findByText('Project settings'));

      expect(useUiStore.getState().sidebarOpen).toBe(false);
    } finally {
      window.innerWidth = width;
    }
  });
});

describe('browser Back', () => {
  const back = async () => {
    window.history.back();
    await new Promise(resolve => setTimeout(resolve, 20));
  };

  it('closes Settings and leaves the chat alone', async () => {
    useUserStore.setState({ userProfile: { email: 'a@b.c', name: 'A' } as never });
    openAt('/c/c1');
    await screen.findByText('Hi there!');

    act(() => useUiStore.getState().setShowSettings(true));
    await back();

    await waitFor(() => expect(useUiStore.getState().showSettings).toBe(false));
    expect(window.location.pathname).toBe('/c/c1');
    expect(useChatStore.getState().conversationId).toBe('c1');
  });

  it('closes Project settings', async () => {
    openAt('/');
    await screen.findByText(PROJECT.name);

    act(() => useWorkspaceStore.getState().setEditingProject(PROJECT as never));
    await back();

    await waitFor(() => expect(useWorkspaceStore.getState().editingProject).toBeNull());
  });

  it('does not clear a temporary chat', async () => {
    useWorkspaceStore.setState({ isEphemeral: true });
    useChatStore.setState({ messages: [{ id: 'm', role: 'user', content: 'Unsaved', created_at: '' }] });
    openAt('/');
    await screen.findAllByText('Unsaved');

    act(() => useUiStore.getState().setShowSettings(true));
    await back();

    await waitFor(() => expect(useUiStore.getState().showSettings).toBe(false));
    expect(useChatStore.getState().messages).toHaveLength(1);
  });

  it('closing a view another way takes its history entry with it', async () => {
    openAt('/');
    await screen.findByText(PROJECT.name);

    act(() => useUiStore.getState().setShowSettings(true));
    expect(window.history.state?.muninOverlay?.view).toBe('settings');

    act(() => useUiStore.getState().setShowSettings(false));
    await waitFor(() => expect(window.history.state?.muninOverlay).toBeUndefined());
  });

  it('closes the phone drawer', async () => {
    stubMatchMedia(390);
    openAt('/');
    await screen.findByRole('dialog', { name: 'Menu' });

    await back();

    await waitFor(() => expect(useUiStore.getState().sidebarOpen).toBe(false));
  });

  it('a new chat from an open view does not leave the view behind Back', async () => {
    const user = userEvent.setup();
    openAt('/c/c1');
    await screen.findByText('Hi there!');
    act(() => useUiStore.getState().setShowSettings(true));

    await user.click(screen.getByRole('link', { name: 'New chat' }));
    expect(window.location.pathname).toBe('/');

    await back();
    await waitFor(() => expect(window.location.pathname).toBe('/c/c1'));
    expect(useUiStore.getState().showSettings).toBe(false);
  });
});

describe('a changed artifact draft', () => {
  const back = async () => {
    window.history.back();
    await new Promise(resolve => setTimeout(resolve, 20));
  };

  // The chat at /c/c1, with the start page behind it in the history, and a1 being
  // edited in the panel.
  async function openChatWithDraft() {
    window.history.replaceState(null, '', '/');
    window.history.pushState(null, '', '/c/c1');
    render(<App />);
    await screen.findByText('Hi there!');
    act(() => useUiStore.setState({ artifactPanelOpen: true, selectedArtifactId: 'a1' }));
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Edit' }));
    await user.type(screen.getByRole('textbox', { name: 'Artifact content' }), 'x');
    expect(useUiStore.getState().artifactDraftChanged).toBe(true);
  }

  it('Back to another page asks, and No keeps the chat and its address', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    await openChatWithDraft();
    await back();

    await waitFor(() => expect(confirm).toHaveBeenCalledWith('Discard your changes to this artifact?'));
    expect(window.location.pathname).toBe('/c/c1');
    expect(useChatStore.getState().conversationId).toBe('c1');
  });

  it('Back to another page goes on Yes', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    await openChatWithDraft();
    await back();

    await waitFor(() => expect(useChatStore.getState().conversationId).toBeNull());
    expect(window.location.pathname).toBe('/');
  });

  it('Back that only closes Settings does not ask', async () => {
    const confirm = vi.spyOn(window, 'confirm');
    useUserStore.setState({ userProfile: { email: 'a@b.c', name: 'A' } as never });
    await openChatWithDraft();
    act(() => useUiStore.getState().setShowSettings(true));
    await back();

    await waitFor(() => expect(useUiStore.getState().showSettings).toBe(false));
    expect(confirm).not.toHaveBeenCalled();
  });

  it('a reload asks through the browser, and only while there is a draft', async () => {
    await openChatWithDraft();
    const unload = () => {
      const e = new Event('beforeunload', { cancelable: true });
      window.dispatchEvent(e);
      return e.defaultPrevented;
    };
    expect(unload()).toBe(true);
    // Taking the change back out leaves nothing to lose.
    await userEvent.type(screen.getByRole('textbox', { name: 'Artifact content' }), '{Backspace}');
    expect(useUiStore.getState().artifactDraftChanged).toBe(false);
    expect(unload()).toBe(false);
  });

  it('a new artifact from the model does not take the panel off the draft', async () => {
    await openChatWithDraft();
    act(() => useChatStore.setState({
      lastArtifactEvent: {
        id: 'a2', source: 'model_written', title: 'New', content_type: 'text/markdown',
        version: 1, conversation_id: 'c1', tool_call_id: 't',
      },
    }));
    expect(useUiStore.getState().selectedArtifactId).toBe('a1');
  });
});

describe('phone drawer as a dialog', () => {
  beforeEach(() => stubMatchMedia(390));

  it('Escape closes it', async () => {
    const user = userEvent.setup();
    openAt('/');
    const drawer = await screen.findByRole('dialog', { name: 'Menu' });
    expect(drawer).toContainElement(document.activeElement as HTMLElement);

    await user.keyboard('{Escape}');

    expect(useUiStore.getState().sidebarOpen).toBe(false);
  });

  it('has a close button', async () => {
    const user = userEvent.setup();
    openAt('/');
    await user.click(await screen.findByRole('button', { name: 'Close menu' }));

    expect(useUiStore.getState().sidebarOpen).toBe(false);
  });
});

describe('side panels', () => {
  it('opening one closes the other', () => {
    act(() => useUiStore.getState().setKnowledgePanelOpen(true));
    act(() => useUiStore.getState().setArtifactPanelOpen(true));
    expect(useUiStore.getState().knowledgePanelOpen).toBe(false);

    act(() => useUiStore.getState().setKnowledgePanelOpen(true));
    expect(useUiStore.getState().artifactPanelOpen).toBe(false);
  });

  it('the support button hides while a panel is showing', async () => {
    useChatStore.setState({ conversationId: 'c1' });
    openAt('/c/c1');
    await screen.findByText('Hi there!');
    expect(document.body).not.toHaveClass('munin-side-panel-open');

    act(() => useUiStore.getState().setArtifactPanelOpen(true));
    expect(document.body).toHaveClass('munin-side-panel-open');

    act(() => useUiStore.getState().setArtifactPanelOpen(false));
    expect(document.body).not.toHaveClass('munin-side-panel-open');
  });
});

describe('header', () => {
  it('the Chat link closes Settings in place, and is highlighted only then', async () => {
    const user = userEvent.setup();
    useUserStore.setState({ userProfile: { email: 'a@b.c', name: 'A' } as never });
    openAt('/');
    const chat = await screen.findByRole('link', { name: 'Chat' });
    expect(chat).toHaveAttribute('aria-current', 'page');

    act(() => useUiStore.getState().setShowSettings(true));
    expect(chat).not.toHaveAttribute('aria-current');

    await user.click(chat);
    expect(useUiStore.getState().showSettings).toBe(false);
    expect(chat).toHaveAttribute('aria-current', 'page');
  });

  it('pills report their state', async () => {
    const user = userEvent.setup();
    openAt('/');
    const pill = await screen.findByTestId('ephemeral-toggle');
    expect(pill).toHaveAccessibleName('Temporary chat');
    expect(pill).toHaveAttribute('aria-pressed', 'false');

    await user.click(pill);
    expect(pill).toHaveAttribute('aria-pressed', 'true');

    await user.click(screen.getByRole('button', { name: 'Turn off' }));
    expect(pill).toHaveAttribute('aria-pressed', 'false');
  });

  it('a temporary chat does not outlive the session', async () => {
    sessionStorage.clear();
    useWorkspaceStore.setState({ isEphemeral: true });
    openAt('/');
    await waitFor(() => expect(useWorkspaceStore.getState().isEphemeral).toBe(false));
  });

  it('a temporary chat survives a reload in the same session', async () => {
    sessionStorage.setItem('munin_session_started', '1');
    useWorkspaceStore.setState({ isEphemeral: true });
    openAt('/');
    await screen.findByText(PROJECT.name);
    expect(useWorkspaceStore.getState().isEphemeral).toBe(true);
  });

  it('the project banner names the project', async () => {
    useWorkspaceStore.setState({ activeProjectId: PROJECT.id });
    openAt('/');
    expect(await screen.findByText(`This chat will be saved in ${PROJECT.name}`)).toBeInTheDocument();
  });
});

describe('error banner', () => {
  const failedTurn = (content: string) => ({
    conversationId: 'c1',
    error: 'upstream died',
    messages: [
      { id: 'u', role: 'user' as const, content, created_at: '' },
      { id: 'a', role: 'assistant' as const, content: '_(stream interrupted: upstream died)_', interrupted: true, created_at: '' },
    ],
  });

  it('does not repeat an interrupted reply\'s reason, and can be dismissed', async () => {
    const user = userEvent.setup();
    useChatStore.setState(failedTurn('Question'));
    openAt('/');

    const notice = await screen.findByRole('alert');
    expect(notice).toHaveTextContent('The reply was interrupted.');
    expect(notice).not.toHaveTextContent('upstream died');

    await user.click(within(notice).getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('Retry sends the question again without a second bubble', async () => {
    const user = userEvent.setup();
    const send = vi.fn();
    const realSend = useChatStore.getState().sendMessage;
    useChatStore.setState({ ...failedTurn('Question'), sendMessage: send });
    onTestFinished(() => useChatStore.setState({ sendMessage: realSend }));
    openAt('/');

    await user.click(within(await screen.findByRole('alert')).getByRole('button', { name: 'Retry' }));

    expect(send).toHaveBeenCalledTimes(1);
    expect(send.mock.calls[0][0]).toBe('Question');
    expect(useChatStore.getState().messages).toEqual([]);
  });

  it('offers no Retry when there is only an attachment placeholder to resend', async () => {
    useChatStore.setState(failedTurn('[Image]'));
    openAt('/');

    const notice = await screen.findByRole('alert');
    expect(within(notice).queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
  });

  it('a chat that cannot load says so in words, and offers a new chat', async () => {
    const user = userEvent.setup();
    openAt('/c/missing');

    expect(await screen.findByRole('alert')).toHaveTextContent('This chat could not be opened.');
    await user.click(screen.getByRole('button', { name: 'Start a new chat' }));

    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(window.location.pathname).toBe('/');
  });

  it('a failed chat load is not an interrupted reply, whatever is still on screen', async () => {
    useChatStore.setState(failedTurn('Question'));
    openAt('/');
    await screen.findByText('The reply was interrupted.');

    await act(async () => { await useChatStore.getState().loadConversation('missing'); });

    const notice = screen.getByRole('alert');
    expect(notice).toHaveTextContent('This chat could not be opened.');
    expect(within(notice).queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start a new chat' })).toBeInTheDocument();
  });

  it.each(['NetworkError when attempting to fetch resource.', 'Load failed', 'Failed to fetch'])(
    'an unreachable server reads the same in every browser: %s',
    async (message) => {
      const realFetch = globalThis.fetch;
      vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
        if (String(input instanceof Request ? input.url : input).includes('/api/chats/gone')) throw new TypeError(message);
        return realFetch(input, init);
      });
      openAt('/c/gone');

      expect(await screen.findByText('This chat could not be opened.')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Start a new chat' })).toBeInTheDocument();
    },
  );

  it('a later load that works clears the failure', async () => {
    await act(async () => { await useChatStore.getState().loadConversation('missing'); });
    expect(useChatStore.getState().loadFailed).toBe(true);
    await act(async () => { await useChatStore.getState().loadConversation('c1'); });
    expect(useChatStore.getState().loadFailed).toBe(false);
  });
});

describe('a session that ends while the tab is open', () => {
  it('shows a notice that stays, with a link back through the login page, and does not navigate', async () => {
    const assign = vi.fn();
    vi.spyOn(window, 'location', 'get').mockReturnValue({ ...window.location, assign, href: 'http://localhost:3000/c/c1' });
    openAt('/');
    await screen.findByText('Test Chat 2');

    server.use(http.get(`${AUTH_BASE}/auth/me`, () => new HttpResponse(null, { status: 401 })));
    act(() => reportApiFailure('network'));

    const notice = await screen.findByTestId('session-notice');
    expect(notice).toHaveAttribute('role', 'alert');
    expect(notice).toHaveTextContent('Your session has ended.');
    expect(within(notice).getByRole('link', { name: 'Sign in again' })).toHaveAttribute(
      'href',
      `${AUTH_BASE}/login?redirect=${encodeURIComponent('http://localhost:3000/c/c1')}`,
    );
    expect(assign).not.toHaveBeenCalled();
  });

  it('shows nothing when the session is still good', async () => {
    openAt('/');
    await screen.findByText('Test Chat 2');

    act(() => reportApiFailure('network'));
    await new Promise(r => setTimeout(r, 50));

    expect(screen.queryByTestId('session-notice')).not.toBeInTheDocument();
  });
});

describe('the toast', () => {
  it('appears inside a status region that was already there, so it is announced', async () => {
    openAt('/');
    await screen.findByText('Test Chat 2');
    const region = screen.getAllByRole('status').find(el => el.textContent === '');
    expect(region).toBeDefined();

    act(() => useUiStore.getState().showToast('Saved'));

    expect(region).toHaveTextContent('Saved');
    expect(within(region!).getByRole('button', { name: 'Dismiss' })).toBeInTheDocument();
  });

  it('a newer toast is not cleared early by the timer of the report toast', async () => {
    // Fake time that also runs on its own, so the waits below still work.
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'], shouldAdvanceTime: true });
    onTestFinished(() => { vi.useRealTimers(); });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    openAt('/c/c1');
    await user.click(await screen.findByRole('button', { name: 'Report this chat' }));
    await user.click(screen.getByRole('button', { name: 'Send report' }));
    expect(await screen.findByText('Chat reported. Thank you for helping us improve.')).toBeInTheDocument();

    act(() => useUiStore.getState().showToast('Newer', 10_000));
    act(() => { vi.advanceTimersByTime(5000); });

    expect(screen.getByText('Newer')).toBeInTheDocument();
  });
});
