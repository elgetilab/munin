import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../test/msw-server';
import { MOCK_CHATS } from '../test/msw-handlers';
import { useWorkspaceStore, _resetWorkspaceStoreForTests } from '../stores/workspaceStore';
import { Sidebar } from './Sidebar';
import { groupByTime } from '../lib/chatGrouping';
import type { ConversationSummary } from '../lib/types';

// ── groupByTime tests ───────────────────────────────────────────────────────

function makeChat(overrides: Partial<ConversationSummary>): ConversationSummary {
  return {
    id: 'c-default',
    title: 'Default',
    persona: 'chat',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    message_count: 1,
    preview: '',
    pinned: false,
    pinned_at: null,
    ...overrides,
  };
}

describe('groupByTime', () => {
  it('places pinned chats in "Pinned" group regardless of date', () => {
    const chat = makeChat({ id: 'pinned1', pinned: true, updated_at: '2020-01-01T00:00:00Z' });
    const groups = groupByTime([chat]);
    expect(groups).toHaveLength(1);
    expect(groups[0].label).toBe('Pinned');
    expect(groups[0].chats).toHaveLength(1);
  });

  it('places today\'s chats in "Today"', () => {
    const chat = makeChat({ id: 'today1', updated_at: new Date().toISOString() });
    const groups = groupByTime([chat]);
    expect(groups).toHaveLength(1);
    expect(groups[0].label).toBe('Today');
  });

  it('places yesterday\'s chats in "Yesterday"', () => {
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    // A timestamp in the middle of yesterday
    const yesterday = new Date(today.getTime() - 43200000); // 12 hours before midnight
    const chat = makeChat({ id: 'y1', updated_at: yesterday.toISOString() });
    const groups = groupByTime([chat]);
    expect(groups).toHaveLength(1);
    expect(groups[0].label).toBe('Yesterday');
  });

  it('older chats go to "Older"', () => {
    const chat = makeChat({ id: 'old1', updated_at: '2020-06-15T12:00:00Z' });
    const groups = groupByTime([chat]);
    expect(groups).toHaveLength(1);
    expect(groups[0].label).toBe('Older');
  });

  it('empty groups are filtered out', () => {
    // Only a today chat — all other groups should be absent
    const chat = makeChat({ id: 't1', updated_at: new Date().toISOString() });
    const groups = groupByTime([chat]);
    const labels = groups.map(g => g.label);
    expect(labels).not.toContain('Pinned');
    expect(labels).not.toContain('Yesterday');
    expect(labels).not.toContain('Older');
  });
});

// ── Sidebar component tests ─────────────────────────────────────────────────

function renderSidebar(overrides: Partial<Parameters<typeof Sidebar>[0]> = {}) {
  // P2 #26 commit 3: userEmail / userName / userAvatar dropped
  // from props (Sidebar pulls userProfile from userStore now).
  // Empty userStore -> userProfile=null -> Sidebar falls back to ''
  // for each field, same effective rendering as the previous
  // hard-coded test props.
  const props = {
    currentId: null,
    onSelect: vi.fn(),
    onNewChat: vi.fn(),
    refreshKey: 0,
    onOpenSettings: vi.fn(),
    ...overrides,
  };
  return { ...render(<Sidebar {...props} />), props };
}

describe('Sidebar', () => {
  it('renders "New chat" button that calls onNewChat', async () => {
    const user = userEvent.setup();
    const { props } = renderSidebar();

    const newChatBtn = screen.getByText('New chat');
    expect(newChatBtn).toBeInTheDocument();

    await user.click(newChatBtn);
    expect(props.onNewChat).toHaveBeenCalledTimes(1);
  });

  it('search overlay opens on Search click', async () => {
    const user = userEvent.setup();
    renderSidebar();

    await user.click(screen.getByText('Search'));

    await waitFor(() => {
      expect(screen.getByPlaceholderText('Search conversations...')).toBeInTheDocument();
    });
  });

  it('creates a project via input', async () => {
    const user = userEvent.setup();

    // Start with no projects so the "New project" button appears
    server.use(
      http.get('/api/projects', () => HttpResponse.json({ projects: [], total: 0 })),
    );

    renderSidebar();

    // Wait for projects to load (empty)
    await waitFor(() => {
      expect(screen.getByText('New project')).toBeInTheDocument();
    });

    await user.click(screen.getByText('New project'));

    const input = screen.getByPlaceholderText('Project name...');
    expect(input).toBeInTheDocument();

    await user.type(input, 'My New Project{Enter}');

    // After creation, the project list reloads. The MSW handler for POST /api/projects
    // returns the created project. The subsequent GET /api/projects will re-fetch.
    // We just verify the input goes away (creation succeeded).
    await waitFor(() => {
      expect(screen.queryByPlaceholderText('Project name...')).not.toBeInTheDocument();
    });
  });

  it('renders project folders', async () => {
    // Default MSW handlers return MOCK_PROJECTS with 'ML Research' and 'Code Review'
    renderSidebar();

    await waitFor(() => {
      expect(screen.getByText('ML Research')).toBeInTheDocument();
    });
    expect(screen.getByText('Code Review')).toBeInTheDocument();
  });

  // Claude-style three-dots menu (2026-06-01).
  //
  // The kebab button replaces the previous inline rename/move/delete
  // row. The old design used `hidden group-hover:flex` which caused
  // the chat title to truncate harder on hover -- the user-visible
  // "pop out" symptom. The new design: a single button that fades in
  // on hover, clicking it opens a dropdown with the three actions.
  //
  // We can't realistically test the visual fade-in (no layout shift)
  // in vitest+jsdom. What we CAN test is the menu shape: clicking
  // the kebab opens a dropdown whose items map to the right
  // callbacks, and the dropdown disappears after an action.
  //
  // Both chat rows AND project rows now use the same kebab button
  // with title="More", so scope-by-row queries (find the title text,
  // walk up to the .relative wrapper) are required to avoid hitting
  // a project's kebab when we meant a chat's.

  // Walk from a row's title text to the `.relative` wrapper that
  // anchors both the click target and its dropdown.
  const rowWrapperOf = (title: string): HTMLElement => {
    const titleEl = screen.getByText(title);
    const wrapper = titleEl.closest('.relative');
    if (!wrapper) throw new Error(`No .relative ancestor for ${title}`);
    return wrapper as HTMLElement;
  };

  it('three-dots menu opens with Rename / Move to project / Delete items', async () => {
    const user = userEvent.setup();
    renderSidebar();

    await waitFor(() => {
      expect(screen.getByText('Test Chat 1')).toBeInTheDocument();
    });

    const chatRow = rowWrapperOf('Test Chat 1');
    const kebab = within(chatRow).getByTitle('More');

    // No menu items visible before opening.
    expect(screen.queryByText('Rename')).not.toBeInTheDocument();

    await user.click(kebab);

    await waitFor(() => {
      expect(within(chatRow).getByText('Rename')).toBeInTheDocument();
    });
    expect(within(chatRow).getByText('Move to project')).toBeInTheDocument();
    expect(within(chatRow).getByText('Delete')).toBeInTheDocument();
  });

  it('three-dots menu: clicking Rename opens the inline editor and closes the menu', async () => {
    const user = userEvent.setup();
    renderSidebar();

    await waitFor(() => {
      expect(screen.getByText('Test Chat 1')).toBeInTheDocument();
    });

    const chatRow = rowWrapperOf('Test Chat 1');
    await user.click(within(chatRow).getByTitle('More'));

    await waitFor(() => {
      expect(within(chatRow).getByText('Rename')).toBeInTheDocument();
    });
    await user.click(within(chatRow).getByText('Rename'));

    // Menu dismissed; inline editor visible with the chat's title.
    await waitFor(() => {
      expect(screen.queryByText('Rename')).not.toBeInTheDocument();
    });
    // The inline editor is an <input> with the chat's current title.
    expect(screen.getByDisplayValue('Test Chat 1')).toBeInTheDocument();
  });

  it('three-dots menu closes on outside click', async () => {
    const user = userEvent.setup();
    renderSidebar();

    await waitFor(() => {
      expect(screen.getByText('Test Chat 1')).toBeInTheDocument();
    });

    const chatRow = rowWrapperOf('Test Chat 1');
    await user.click(within(chatRow).getByTitle('More'));

    await waitFor(() => {
      expect(within(chatRow).getByText('Rename')).toBeInTheDocument();
    });

    // Click on something outside the menu (the "New chat" button).
    await user.click(screen.getByText('New chat'));

    await waitFor(() => {
      expect(screen.queryByText('Rename')).not.toBeInTheDocument();
    });
  });

  it('project rows have the same kebab + dropdown shape (New chat / Settings / Delete)', async () => {
    const user = userEvent.setup();
    renderSidebar();

    await waitFor(() => {
      expect(screen.getByText('ML Research')).toBeInTheDocument();
    });

    const projectRow = rowWrapperOf('ML Research');
    const kebab = within(projectRow).getByTitle('More');

    expect(screen.queryByText('New chat in project')).not.toBeInTheDocument();

    await user.click(kebab);

    await waitFor(() => {
      expect(within(projectRow).getByText('Delete project')).toBeInTheDocument();
    });
    // onNewChatInProject / onOpenProjectSettings are not wired in
    // renderSidebar(), so those rows shouldn't render. Only Delete
    // is unconditional.
    expect(within(projectRow).queryByText('New chat in project')).not.toBeInTheDocument();
    expect(within(projectRow).queryByText('Project settings')).not.toBeInTheDocument();
  });
});

// ── Open-in-new-tab affordances (2026-09-21) ────────────────────────────────
//
// The sidebar entries used to be <button>s and <div onClick>s, which gave the
// browser nothing to work with: right-click offered no "Open link in new tab"
// or "... new window", and ctrl/cmd/middle-click did nothing at all. They are
// real anchors now. The contract under test is the split of responsibility:
// a plain left click is the app's (preventDefault, then the callback), and a
// modified click is the browser's (default action left intact, and crucially
// NO callback, so the current tab doesn't follow along).
//
// Right-click itself needs no test: the context menu is the browser's, and it
// appears for any element carrying an href. Asserting the href is the whole
// of what we control.
//
// jsdom logs "Not implemented: navigation to another Document" while these
// run. That is the pass condition, not a failure: it means a modified click
// reached jsdom's link-activation with its default action intact.

describe('Sidebar open-in-new-tab links', () => {
  // Dispatched natively rather than via userEvent so the test can read
  // `defaultPrevented` back off the event afterwards.
  function clickWith(el: Element, init: MouseEventInit = {}): MouseEvent {
    const ev = new MouseEvent('click', { bubbles: true, cancelable: true, ...init });
    el.dispatchEvent(ev);
    return ev;
  }

  const linkFor = (text: string): HTMLAnchorElement => {
    const anchor = screen.getByText(text).closest('a');
    if (!anchor) throw new Error(`"${text}" is not inside a link`);
    return anchor as HTMLAnchorElement;
  };

  it('"New chat" is a link to /', () => {
    renderSidebar();
    expect(linkFor('New chat')).toHaveAttribute('href', '/');
  });

  it('plain click on "New chat" starts a chat in place, without navigating', () => {
    const { props } = renderSidebar();
    const ev = clickWith(linkFor('New chat'));
    expect(props.onNewChat).toHaveBeenCalledTimes(1);
    expect(ev.defaultPrevented).toBe(true);
  });

  it.each([
    ['ctrl', { ctrlKey: true }],
    ['meta', { metaKey: true }],
    ['shift', { shiftKey: true }],
    ['middle', { button: 1 }],
  ])('%s-click on "New chat" is left to the browser', (_label, init) => {
    const { props } = renderSidebar();
    const ev = clickWith(linkFor('New chat'), init);
    expect(props.onNewChat).not.toHaveBeenCalled();
    expect(ev.defaultPrevented).toBe(false);
  });

  it('chat row titles are links to /c/<id>', async () => {
    renderSidebar();
    await waitFor(() => expect(screen.getByText('Test Chat 1')).toBeInTheDocument());
    expect(linkFor('Test Chat 1')).toHaveAttribute('href', '/c/c1');
  });

  it('plain click on a chat row selects it in place', async () => {
    const { props } = renderSidebar();
    await waitFor(() => expect(screen.getByText('Test Chat 1')).toBeInTheDocument());
    const ev = clickWith(linkFor('Test Chat 1'));
    expect(props.onSelect).toHaveBeenCalledWith('c1');
    expect(ev.defaultPrevented).toBe(true);
  });

  it('modified click on a chat row does not switch the current conversation', async () => {
    const { props } = renderSidebar();
    await waitFor(() => expect(screen.getByText('Test Chat 1')).toBeInTheDocument());
    const ev = clickWith(linkFor('Test Chat 1'), { ctrlKey: true });
    expect(props.onSelect).not.toHaveBeenCalled();
    expect(ev.defaultPrevented).toBe(false);
  });

  it('a modified click on the row outside the title is inert', async () => {
    const { props } = renderSidebar();
    await waitFor(() => expect(screen.getByText('Test Chat 1')).toBeInTheDocument());
    const row = linkFor('Test Chat 1').closest('[data-testid="chat-row"]')!;
    clickWith(row, { ctrlKey: true });
    expect(props.onSelect).not.toHaveBeenCalled();
  });

  it('search results are links to their conversation', async () => {
    const user = userEvent.setup();
    renderSidebar();
    await user.click(screen.getByText('Search'));

    // "Hello" is c1's preview, rendered only inside the search overlay,
    // so it identifies the overlay row without colliding with the
    // sidebar row of the same title.
    await waitFor(() => expect(screen.getByText('Hello')).toBeInTheDocument());
    expect(linkFor('Hello')).toHaveAttribute('href', '/c/c1');
  });

  it('"New chat in project" links to /?project=<id>', async () => {
    const user = userEvent.setup();
    const onNewChatInProject = vi.fn();
    renderSidebar({ onNewChatInProject });

    await waitFor(() => expect(screen.getByText('ML Research')).toBeInTheDocument());
    const projectRow = screen.getByText('ML Research').closest('.relative')!;
    await user.click(within(projectRow as HTMLElement).getByTitle('More'));
    await waitFor(() => expect(screen.getByText('New chat in project')).toBeInTheDocument());

    const link = linkFor('New chat in project');
    expect(link).toHaveAttribute('href', '/?project=p1');

    // Modified click: the browser opens the link, which carries the
    // project, and this tab stays put.
    const ev = clickWith(link, { ctrlKey: true });
    expect(onNewChatInProject).not.toHaveBeenCalled();
    expect(ev.defaultPrevented).toBe(false);
  });
});

// ── Behaviours the fetch-on-mount restructure touches (2026-09-23) ──────────
//
// None of the tests above cover project expansion, the auto-expand of an
// active project, what the search overlay shows for an empty query, or the
// refresh after a mutation. Those are exactly the four things Phase 3 moves
// around, so they get pinned here first.

describe('Sidebar data loading', () => {
  const ADMIN_CHATS = '/api/chats';

  // The workspace store is module state shared by every test in this file.
  // Without this reset the auto-expand test below leaves activeProjectId set
  // and the later search tests render an expanded project they never asked
  // for.
  beforeEach(() => _resetWorkspaceStoreForTests());
  afterEach(() => vi.restoreAllMocks());

  it('loads a project\'s chats when it is expanded', async () => {
    const user = userEvent.setup();
    server.use(
      http.get(ADMIN_CHATS, ({ request }) => {
        const pid = new URL(request.url).searchParams.get('project_id');
        if (pid === 'p1') {
          return HttpResponse.json({
            conversations: [makeChat({ id: 'pc1', title: 'Filed Chat' })],
            total: 1,
          });
        }
        return HttpResponse.json(MOCK_CHATS);
      }),
    );

    renderSidebar();
    await waitFor(() => expect(screen.getByText('ML Research')).toBeInTheDocument());
    expect(screen.queryByText('Filed Chat')).not.toBeInTheDocument();

    await user.click(screen.getByText('ML Research'));
    await waitFor(() => expect(screen.getByText('Filed Chat')).toBeInTheDocument());
  });

  it('auto-expands the project the active conversation belongs to', async () => {
    useWorkspaceStore.setState({ activeProjectId: 'p1' });
    server.use(
      http.get(ADMIN_CHATS, ({ request }) => {
        const pid = new URL(request.url).searchParams.get('project_id');
        return pid === 'p1'
          ? HttpResponse.json({ conversations: [makeChat({ id: 'pc1', title: 'Filed Chat' })], total: 1 })
          : HttpResponse.json(MOCK_CHATS);
      }),
    );

    renderSidebar();
    // No click: the store's active project drives the expansion.
    await waitFor(() => expect(screen.getByText('Filed Chat')).toBeInTheDocument());
  });

  it('shows recent chats when the search box is empty, and server hits once typed', async () => {
    const user = userEvent.setup();
    renderSidebar();
    await waitFor(() => expect(screen.getByText('Test Chat 1')).toBeInTheDocument());

    await user.click(screen.getByText('Search'));
    const overlay = await screen.findByPlaceholderText('Search conversations...');
    // Empty query lists the recent conversations under a "Recent" heading.
    await waitFor(() => expect(screen.getByText('Recent')).toBeInTheDocument());

    server.use(http.get(ADMIN_CHATS, ({ request }) => {
      const q = new URL(request.url).searchParams.get('search');
      return q
        ? HttpResponse.json({ conversations: [makeChat({ id: 'sr', title: 'Search Hit' })], total: 1 })
        : HttpResponse.json(MOCK_CHATS);
    }));

    await user.type(overlay, 'hit');
    await waitFor(() => expect(screen.getByText('Search Hit')).toBeInTheDocument(), { timeout: 2000 });
    expect(screen.queryByText('Recent')).not.toBeInTheDocument();
  });

  it('forgets the query when the overlay is closed and reopened', async () => {
    const user = userEvent.setup();
    renderSidebar();
    await waitFor(() => expect(screen.getByText('Test Chat 1')).toBeInTheDocument());

    await user.click(screen.getByText('Search'));
    await user.type(await screen.findByPlaceholderText('Search conversations...'), 'abc');
    await user.click(screen.getByText('Esc'));

    await waitFor(() =>
      expect(screen.queryByPlaceholderText('Search conversations...')).not.toBeInTheDocument());

    await user.click(screen.getByText('Search'));
    const reopened = await screen.findByPlaceholderText('Search conversations...');
    expect(reopened).toHaveValue('');
  });

  it('re-reads the chat list after a conversation is deleted', async () => {
    const user = userEvent.setup();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    let deleted = false;
    server.use(
      http.get(ADMIN_CHATS, ({ request }) => {
        if (new URL(request.url).searchParams.get('project_id')) {
          return HttpResponse.json({ conversations: [], total: 0 });
        }
        return HttpResponse.json({
          conversations: deleted
            ? MOCK_CHATS.conversations.filter(c => c.id !== 'c1')
            : MOCK_CHATS.conversations,
          total: deleted ? 2 : 3,
        });
      }),
      http.delete('/api/chats/:id', () => { deleted = true; return new HttpResponse(null, { status: 204 }); }),
    );

    renderSidebar();
    await waitFor(() => expect(screen.getByText('Test Chat 1')).toBeInTheDocument());

    const row = screen.getByText('Test Chat 1').closest('.relative')!;
    await user.click(within(row as HTMLElement).getByTitle('More'));
    await user.click(within(row as HTMLElement).getByText('Delete'));

    await waitFor(() => expect(screen.queryByText('Test Chat 1')).not.toBeInTheDocument());
  });
});
