import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from './test/msw-server';
import { MOCK_CONVERSATION, MOCK_PROJECTS } from './test/msw-handlers';
import App from './App';
import { _resetChatStoreForTests, useChatStore } from './stores/chatStore';
import { _resetUiStoreForTests, useUiStore } from './stores/uiStore';
import { _resetUserStoreForTests, useUserStore } from './stores/userStore';
import { _resetWorkspaceStoreForTests, useWorkspaceStore } from './stores/workspaceStore';

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
