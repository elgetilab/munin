import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { onTestFinished } from 'vitest';
import { http, HttpResponse } from 'msw';
import { server } from '../test/msw-server';
import { MOCK_CHATS, MOCK_PROJECTS } from '../test/msw-handlers';
import { useUiStore } from '../stores/uiStore';
import { useUserStore, _resetUserStoreForTests } from '../stores/userStore';
import { useWorkspaceStore, _resetWorkspaceStoreForTests } from '../stores/workspaceStore';
import { Sidebar } from './Sidebar';
import { groupByTime } from '../lib/chatGrouping';
import { AUTH_BASE, DOCS_URL, SEARCH_URL } from '../lib/urls';
import type { ConversationSummary, Project } from '../lib/types';
import type { UserProfile } from '../lib/api';

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

    await user.click(screen.getByText('Search chats'));

    await waitFor(() => {
      expect(screen.getByPlaceholderText('Search conversations...')).toBeInTheDocument();
    });
  });

  it('dims behind the search panel with the scrim in light', async () => {
    const user = userEvent.setup();
    renderSidebar();
    await user.click(screen.getByText('Search chats'));
    const input = await screen.findByPlaceholderText('Search conversations...');
    const backdrop = input.closest('.fixed')!.firstElementChild;
    expect(backdrop).toHaveClass('bg-bg-primary/70', '[[data-theme=light]_&]:bg-scrim');
  });

  // The logo file is a black raven; --logo-filter turns it white in dark only.
  it('takes the logo filter from the theme', () => {
    renderSidebar();
    const logo = screen.getAllByAltText('Munin')[0];
    expect(logo).toHaveClass('[filter:var(--logo-filter)]');
    expect(logo).not.toHaveClass('invert');
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
    await user.click(screen.getByText('Search chats'));

    // "Hello" is c1's preview, rendered only inside the search overlay,
    // so it identifies the overlay row without colliding with the
    // sidebar row of the same title.
    await waitFor(() => expect(screen.getByText('Hello')).toBeInTheDocument());
    expect(linkFor('Hello')).toHaveAttribute('href', '/c/c1');
  });

  it('leaves out a preview that only repeats the title', async () => {
    const user = userEvent.setup();
    server.use(http.get('/api/chats', () => HttpResponse.json({
      conversations: [
        makeChat({ id: 'same', title: 'Kinase  inhibitors', preview: ' Kinase inhibitors\n' }),
        makeChat({ id: 'differs', title: 'Folding', preview: 'How does AlphaFold work?' }),
      ],
      total: 2,
    })));
    renderSidebar();
    await user.click(screen.getByText('Search chats'));
    const dialog = await screen.findByRole('dialog', { name: 'Search your chats' });

    await waitFor(() => expect(within(dialog).getByText('How does AlphaFold work?')).toBeInTheDocument());
    const row = within(dialog).getByRole('link', { name: /^Kinase/ });
    expect(row).toHaveTextContent(/^Kinase inhibitors$/);
  });

  it('marks the search box for the line under it, not a ring of its own', async () => {
    const user = userEvent.setup();
    renderSidebar();
    await user.click(screen.getByText('Search chats'));
    const input = await screen.findByRole('textbox', { name: 'Search conversations' });
    expect(input).toHaveFocus();
    // index.css drops the ring on #search-chats-input; the row draws focus instead.
    expect(input).toHaveAttribute('id', 'search-chats-input');
    expect(input.parentElement).toHaveClass('has-[#search-chats-input:focus]:border-focus-ring');
  });

  describe('Ctrl+K', () => {
    afterEach(() => vi.restoreAllMocks());

    const press = (init: KeyboardEventInit, target: EventTarget = document.activeElement ?? document.body) => {
      const e = new KeyboardEvent('keydown', { key: 'k', bubbles: true, cancelable: true, ...init });
      act(() => { target.dispatchEvent(e); });
      return e;
    };

    it('names the shortcut on the Search button', () => {
      renderSidebar();
      const button = screen.getByRole('button', { name: 'Search chats' });
      expect(button).toHaveAttribute('aria-keyshortcuts', 'Control+K');
      expect(button).toHaveAttribute('title', 'Search chats (Ctrl+K)');
    });

    it('names ⌘K on a Mac', () => {
      vi.spyOn(navigator, 'platform', 'get').mockReturnValue('MacIntel');
      renderSidebar();
      const button = screen.getByRole('button', { name: 'Search chats' });
      expect(button).toHaveAttribute('aria-keyshortcuts', 'Meta+K');
      expect(button).toHaveAttribute('title', 'Search chats (⌘K)');
    });

    it('opens search from a text field, focused, and keeps the key from the browser', async () => {
      renderSidebar();
      const outside = document.createElement('textarea');
      document.body.appendChild(outside);
      onTestFinished(() => outside.remove());
      outside.focus();

      const e = press({ ctrlKey: true });
      expect(e.defaultPrevented).toBe(true);
      const input = await screen.findByRole('textbox', { name: 'Search conversations' });
      expect(input).toHaveFocus();

      // Pressed again inside, it stays here rather than going to the browser.
      expect(press({ ctrlKey: true }).defaultPrevented).toBe(true);
      expect(input).toHaveFocus();

      // Escape gives focus back to where the key was pressed.
      await userEvent.keyboard('{Escape}');
      expect(screen.queryByRole('dialog', { name: 'Search your chats' })).not.toBeInTheDocument();
      expect(outside).toHaveFocus();
    });

    it('opens with ⌘K and with K in capitals (Caps Lock)', async () => {
      renderSidebar();
      press({ metaKey: true, key: 'K' });
      expect(await screen.findByRole('dialog', { name: 'Search your chats' })).toBeInTheDocument();
    });

    it('leaves other K chords alone', () => {
      renderSidebar();
      for (const init of [{}, { ctrlKey: true, shiftKey: true }, { ctrlKey: true, altKey: true }, { ctrlKey: true, metaKey: true }]) {
        expect(press(init).defaultPrevented).toBe(false);
      }
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    it('on a Mac leaves Ctrl+K to a text field, where it deletes to the end of the line', async () => {
      vi.spyOn(navigator, 'platform', 'get').mockReturnValue('MacIntel');
      renderSidebar();
      const outside = document.createElement('textarea');
      document.body.appendChild(outside);
      onTestFinished(() => outside.remove());
      outside.focus();

      expect(press({ ctrlKey: true }).defaultPrevented).toBe(false);
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

      expect(press({ metaKey: true }).defaultPrevented).toBe(true);
      expect(await screen.findByRole('dialog', { name: 'Search your chats' })).toBeInTheDocument();
    });

    it('does not open over another dialog', () => {
      renderSidebar();
      const other = document.createElement('div');
      other.setAttribute('role', 'dialog');
      other.setAttribute('aria-modal', 'true');
      const field = document.createElement('input');
      other.appendChild(field);
      document.body.appendChild(other);
      onTestFinished(() => other.remove());
      field.focus();

      expect(press({ ctrlKey: true }).defaultPrevented).toBe(false);
      expect(screen.queryByRole('dialog', { name: 'Search your chats' })).not.toBeInTheDocument();
    });

    it('leaves the key to the report dialog while it is open', () => {
      useUiStore.setState({ showReportDialog: true });
      onTestFinished(() => useUiStore.setState({ showReportDialog: false }));
      renderSidebar();

      expect(press({ ctrlKey: true }).defaultPrevented).toBe(false);
      expect(screen.queryByRole('dialog', { name: 'Search your chats' })).not.toBeInTheDocument();
    });

    it('opens from inside the phone drawer that holds the sidebar', async () => {
      render(
        <div role="dialog" aria-modal="true" aria-label="Menu">
          <Sidebar currentId={null} onSelect={vi.fn()} onNewChat={vi.fn()} refreshKey={0} onOpenSettings={vi.fn()} />
        </div>,
      );
      screen.getByRole('button', { name: 'Search chats' }).focus();
      expect(press({ ctrlKey: true }).defaultPrevented).toBe(true);
      expect(await screen.findByRole('dialog', { name: 'Search your chats' })).toBeInTheDocument();
    });
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

    await user.click(screen.getByText('Search chats'));
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

    await user.click(screen.getByText('Search chats'));
    await user.type(await screen.findByPlaceholderText('Search conversations...'), 'abc');
    await user.click(screen.getByText('Esc'));

    await waitFor(() =>
      expect(screen.queryByPlaceholderText('Search conversations...')).not.toBeInTheDocument());

    await user.click(screen.getByText('Search chats'));
    const reopened = await screen.findByPlaceholderText('Search conversations...');
    expect(reopened).toHaveValue('');
  });

  it('re-reads the chat list after a conversation is deleted', async () => {
    const user = userEvent.setup();
    let deleted = false;
    server.use(
      http.get(ADMIN_CHATS, ({ request }) => {
        if (new URL(request.url).searchParams.get('project_id') === 'p1') {
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
    await user.click(screen.getByRole('button', { name: 'Delete chat' }));

    await waitFor(() => expect(screen.queryByText('Test Chat 1')).not.toBeInTheDocument());
  });
});

describe('Sidebar profile menu', () => {
  const support = window as Window & { MuninSupport?: { open: () => void } };

  beforeEach(() => {
    useUserStore.setState({ userProfile: { email: 'a@b.c', name: 'A' } as UserProfile });
  });

  afterEach(() => {
    _resetUserStoreForTests();
    delete support.MuninSupport;
  });

  it('Help opens the support widget and closes the menu', async () => {
    const user = userEvent.setup();
    const open = vi.fn();
    support.MuninSupport = { open };
    renderSidebar();

    await user.click(screen.getByText('a@b.c'));
    await user.click(screen.getByRole('menuitem', { name: 'Help' }));

    expect(open).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('menuitem', { name: 'Help' })).not.toBeInTheDocument();
  });

  it('Help closes the phone drawer', async () => {
    const user = userEvent.setup();
    const onCloseDrawer = vi.fn();
    support.MuninSupport = { open: vi.fn() };
    renderSidebar({ onCloseDrawer });

    await user.click(screen.getByText('a@b.c'));
    await user.click(screen.getByRole('menuitem', { name: 'Help' }));

    expect(onCloseDrawer).toHaveBeenCalledTimes(1);
  });

  it('Help says so and opens the docs when the widget has not loaded', async () => {
    const user = userEvent.setup();
    const opened = vi.spyOn(window, 'open').mockReturnValue(null);
    renderSidebar();

    await user.click(screen.getByText('a@b.c'));
    await user.click(screen.getByRole('menuitem', { name: 'Help' }));

    expect(screen.queryByRole('menuitem', { name: 'Help' })).not.toBeInTheDocument();
    expect(opened).toHaveBeenCalledWith(DOCS_URL, '_blank', 'noopener');
    expect(useUiStore.getState().toast).toMatch(/could not load/i);
    opened.mockRestore();
    useUiStore.getState().setToast(null);
  });

  it('Sign out is the last item and posts to the auth service', async () => {
    const user = userEvent.setup();
    renderSidebar();
    await user.click(screen.getByText('a@b.c'));

    const items = screen.getAllByRole('menuitem');
    const signOut = items[items.length - 1];
    expect(signOut).toHaveAccessibleName('Sign out');
    expect(signOut).toHaveAttribute('type', 'submit');
    const form = signOut.closest('form')!;
    expect(form).toHaveAttribute('method', 'post');
    expect(form).toHaveAttribute('action', `${AUTH_BASE}/logout`);
  });

  it('offers one docs link, no dead language row, and paper search', async () => {
    const user = userEvent.setup();
    renderSidebar();
    await user.click(screen.getByText('a@b.c'));

    expect(screen.getAllByRole('menuitem', { name: 'Docs' })).toHaveLength(1);
    expect(screen.queryByText('Learn more')).not.toBeInTheDocument();
    expect(screen.queryByText('English')).not.toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: 'Search papers' })).toHaveAttribute('href', SEARCH_URL);
  });

  it('Escape closes the menu and returns focus to its trigger', async () => {
    const user = userEvent.setup();
    renderSidebar();
    const trigger = screen.getByText('a@b.c').closest('button')!;
    await user.click(trigger);
    expect(screen.getByRole('menuitem', { name: 'Settings' })).toHaveFocus();

    await user.keyboard('{Escape}');

    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('Knowledge is a real link that stays in the app on a plain click', async () => {
    const user = userEvent.setup();
    const onOpenKnowledge = vi.fn();
    renderSidebar({ onOpenKnowledge });

    await user.click(screen.getByText('a@b.c'));
    const link = screen.getByRole('menuitem', { name: 'Knowledge' });
    expect(link).toHaveAttribute('href', '/knowledge');
    await user.click(link);

    expect(onOpenKnowledge).toHaveBeenCalledTimes(1);
  });
});

// ── Filed chats live in their project (findings 3, 4, 5, 6) ─────────────────

describe('Sidebar filed chats and projects', () => {
  const FILED = makeChat({ id: 'pc1', title: 'Filed Chat', pinned: false });

  beforeEach(() => _resetWorkspaceStoreForTests());
  afterEach(() => vi.restoreAllMocks());

  // Behaves like the real endpoint: without __unfiled__ a filed chat is listed too.
  function mockBackendChats() {
    const seen: (string | null)[] = [];
    server.use(
      http.get('/api/chats', ({ request }) => {
        const pid = new URL(request.url).searchParams.get('project_id');
        seen.push(pid);
        if (pid === 'p1') return HttpResponse.json({ conversations: [FILED], total: 1 });
        if (pid === '__unfiled__') return HttpResponse.json(MOCK_CHATS);
        return HttpResponse.json({
          conversations: [...MOCK_CHATS.conversations, FILED],
          total: 4,
        });
      }),
    );
    return seen;
  }

  const rowOf = (title: string) => screen.getByText(title).closest('.relative') as HTMLElement;

  it('asks for unfiled chats only, so a filed chat is listed once', async () => {
    const user = userEvent.setup();
    const seen = mockBackendChats();
    renderSidebar();

    await waitFor(() => expect(screen.getByText('Test Chat 1')).toBeInTheDocument());
    expect(seen).toContain('__unfiled__');
    expect(screen.queryByText('Filed Chat')).not.toBeInTheDocument();

    await user.click(screen.getByText('ML Research'));
    await waitFor(() => expect(screen.getAllByText('Filed Chat')).toHaveLength(1));
  });

  it('the search overlay still offers filed chats as recent', async () => {
    const user = userEvent.setup();
    mockBackendChats();
    renderSidebar();
    await waitFor(() => expect(screen.getByText('Test Chat 1')).toBeInTheDocument());

    await user.click(screen.getByText('Search chats'));
    await waitFor(() => expect(screen.getByText('Filed Chat')).toBeInTheDocument());
  });

  it('pins a project chat that the main list does not contain', async () => {
    const user = userEvent.setup();
    mockBackendChats();
    const pinned: string[] = [];
    server.use(http.post('/api/chats/:id/pin', ({ params }) => {
      pinned.push(params.id as string);
      return new HttpResponse(null, { status: 200 });
    }));
    renderSidebar();
    await user.click(await screen.findByText('ML Research'));
    await screen.findByText('Filed Chat');

    await user.click(within(rowOf('Filed Chat')).getByTitle('More'));
    await user.click(screen.getByRole('menuitem', { name: 'Pin' }));

    await waitFor(() => expect(pinned).toEqual(['pc1']));
  });

  describe('a pinned filed chat', () => {
    const PINNED = { ...FILED, pinned: true, project_id: 'p1' };

    function mockPinnedFiled() {
      server.use(http.get('/api/chats', ({ request }) => {
        const params = new URL(request.url).searchParams;
        if (params.get('pinned_only') === 'true') return HttpResponse.json({ conversations: [PINNED], total: 1 });
        return params.get('project_id') === 'p1'
          ? HttpResponse.json({ conversations: [PINNED], total: 1 })
          : HttpResponse.json({ conversations: [MOCK_CHATS.conversations[1]], total: 1 });
      }));
    }

    it('shows in the Pinned group with its folder closed', async () => {
      const user = userEvent.setup();
      mockPinnedFiled();
      renderSidebar();

      await screen.findByText('Pinned');
      await user.click(within(rowOf('Filed Chat')).getByTitle('More'));
      expect(screen.getByRole('menuitem', { name: 'Unpin' })).toBeInTheDocument();
      await user.keyboard('{Escape}');
      expect(screen.getAllByText('Filed Chat')).toHaveLength(1);
    });

    it('can be removed from its project there', async () => {
      const user = userEvent.setup();
      mockPinnedFiled();
      const removed: string[] = [];
      server.use(http.delete('/api/projects/:pid/conversations/:cid', ({ params }) => {
        removed.push(`${params.pid}/${params.cid}`);
        return HttpResponse.json({ ok: true });
      }));
      renderSidebar();
      await screen.findByText('Pinned');

      const row = rowOf('Filed Chat');
      await user.click(within(row).getByTitle('More'));
      await user.click(within(row).getByText('Remove from project'));

      await waitFor(() => expect(removed).toEqual(['p1/pc1']));
    });

    it('also stays in its project, where a rename or menu opens on that row alone', async () => {
      const user = userEvent.setup();
      mockPinnedFiled();
      renderSidebar();
      await screen.findByText('Pinned');
      await user.click(screen.getByText('ML Research'));
      await waitFor(() => expect(screen.getAllByText('Filed Chat')).toHaveLength(2));

      const [first] = screen.getAllByText('Filed Chat').map(el => el.closest('.relative') as HTMLElement);
      await user.click(within(first).getByTitle('More'));
      expect(screen.getAllByRole('menuitem', { name: 'Rename' })).toHaveLength(1);

      await user.click(within(first).getByText('Rename'));
      expect(screen.getAllByRole('textbox', { name: 'Rename chat' })).toHaveLength(1);
      expect(screen.getByRole('textbox', { name: 'Rename chat' })).toHaveFocus();
    });

    it('leaves the date groups alone when unpinned', async () => {
      server.use(http.get('/api/chats', ({ request }) => {
        const params = new URL(request.url).searchParams;
        if (params.get('pinned_only') === 'true') return HttpResponse.json({ conversations: [MOCK_CHATS.conversations[0]], total: 1 });
        return HttpResponse.json({ conversations: [MOCK_CHATS.conversations[0], MOCK_CHATS.conversations[1]], total: 2 });
      }));
      renderSidebar();

      await screen.findByText('Test Chat 2');
      expect(screen.getAllByText('Test Chat 1')).toHaveLength(1);
      expect(screen.queryByText('Filed Chat')).not.toBeInTheDocument();
    });
  });

  it('renames a project chat starting from its own title', async () => {
    const user = userEvent.setup();
    mockBackendChats();
    renderSidebar();
    await user.click(await screen.findByText('ML Research'));
    await screen.findByText('Filed Chat');

    const row = rowOf('Filed Chat');
    await user.click(within(row).getByTitle('More'));
    await user.click(within(row).getByText('Rename'));

    expect(screen.getByDisplayValue('Filed Chat')).toBeInTheDocument();
  });

  it('deleting the active project clears it and any open settings for it', async () => {
    const user = userEvent.setup();
    useWorkspaceStore.setState({ activeProjectId: 'p1', editingProject: { id: 'p1' } as unknown as Project });
    renderSidebar();
    await screen.findByText('ML Research');

    const row = rowOf('ML Research');
    await user.click(within(row).getByTitle('More'));
    await user.click(within(row).getByText('Delete project'));
    await user.click(screen.getByRole('button', { name: 'Delete project' }));

    await waitFor(() => expect(useWorkspaceStore.getState().activeProjectId).toBeNull());
    expect(useWorkspaceStore.getState().editingProject).toBeNull();
  });

  describe('archived projects', () => {
    const ARCHIVED = { ...MOCK_PROJECTS.projects[1], id: 'p9', name: 'Old Work', archived: true };

    function mockArchived() {
      let archived = true;
      const patches: unknown[] = [];
      server.use(
        http.get('/api/projects', ({ request }) => {
          const all = new URL(request.url).searchParams.get('archived') === 'true';
          const list = [MOCK_PROJECTS.projects[0], ...(all || !archived ? [{ ...ARCHIVED, archived }] : [])];
          return HttpResponse.json({ projects: list, total: list.length });
        }),
        http.patch('/api/projects/p9', async ({ request }) => {
          patches.push(await request.json());
          archived = false;
          return HttpResponse.json({ ...ARCHIVED, archived: false });
        }),
      );
      return patches;
    }

    it('are listed in a collapsed group and can be unarchived', async () => {
      const user = userEvent.setup();
      const patches = mockArchived();
      renderSidebar();

      const group = await screen.findByRole('button', { name: 'Archived (1)' });
      expect(group).toHaveAttribute('aria-expanded', 'false');
      expect(screen.queryByText('Old Work')).not.toBeInTheDocument();

      await user.click(group);
      await user.click(screen.getByRole('button', { name: 'Unarchive Old Work' }));

      await waitFor(() => expect(patches).toEqual([{ archived: false }]));
      await waitFor(() => expect(screen.queryByRole('button', { name: /^Archived/ })).not.toBeInTheDocument());
      expect(screen.getByText('Old Work')).toBeInTheDocument();
    });

    it('open their settings from the row', async () => {
      const user = userEvent.setup();
      mockArchived();
      const onOpenProjectSettings = vi.fn();
      renderSidebar({ onOpenProjectSettings });

      await user.click(await screen.findByRole('button', { name: 'Archived (1)' }));
      await user.click(screen.getByRole('button', { name: 'Old Work' }));

      expect(onOpenProjectSettings).toHaveBeenCalledWith(expect.objectContaining({ id: 'p9' }));
    });

    it('list their chats when expanded, one folder at a time', async () => {
      const user = userEvent.setup();
      mockArchived();
      const OLD = makeChat({ id: 'old1', title: 'Archived Chat', project_id: 'p9' });
      server.use(http.get('/api/chats', ({ request }) => {
        const pid = new URL(request.url).searchParams.get('project_id');
        return pid === 'p9'
          ? HttpResponse.json({ conversations: [OLD], total: 1 })
          : HttpResponse.json(MOCK_CHATS);
      }));
      const onSelect = vi.fn();
      renderSidebar({ onSelect });

      await user.click(await screen.findByRole('button', { name: 'Archived (1)' }));
      const toggle = screen.getByRole('button', { name: 'Chats in Old Work' });
      expect(toggle).toHaveAttribute('aria-expanded', 'false');
      expect(screen.queryByText('Archived Chat')).not.toBeInTheDocument();

      await user.click(toggle);
      expect(toggle).toHaveAttribute('aria-expanded', 'true');
      await user.click(await screen.findByText('Archived Chat'));
      expect(onSelect).toHaveBeenCalledWith('old1');

      await user.click(toggle);
      expect(screen.queryByText('Archived Chat')).not.toBeInTheDocument();
    });

    it('stay reachable when every project is archived', async () => {
      server.use(http.get('/api/projects', () =>
        HttpResponse.json({ projects: [ARCHIVED], total: 1 })));
      renderSidebar();

      expect(await screen.findByRole('button', { name: 'Archived (1)' })).toBeInTheDocument();
      expect(screen.getByText('New project')).toBeInTheDocument();
    });
  });
});

// ── Loading, failure and feedback (findings 8, 9, 10, 17, 18) ───────────────

describe('Sidebar loading and feedback', () => {
  beforeEach(() => {
    _resetWorkspaceStoreForTests();
    useUiStore.getState().setToast(null);
  });
  afterEach(() => {
    vi.restoreAllMocks();
    useUiStore.getState().setToast(null);
  });

  const none = () => HttpResponse.json({ conversations: [], total: 0 });
  const noProjects = () => HttpResponse.json({ projects: [], total: 0 });

  it('shows a loading state first, and the empty text only after an empty answer', async () => {
    let release = () => {};
    const gate = new Promise<void>(resolve => { release = resolve; });
    server.use(
      http.get('/api/chats', async () => { await gate; return none(); }),
      http.get('/api/projects', noProjects),
    );
    renderSidebar();

    expect(screen.getByTestId('chats-loading')).toBeInTheDocument();
    expect(screen.queryByText('No conversations yet')).not.toBeInTheDocument();

    release();
    expect(await screen.findByText('No conversations yet')).toBeInTheDocument();
    expect(screen.queryByTestId('chats-loading')).not.toBeInTheDocument();
  });

  it('says so when chats fail to load, and Retry reads them again', async () => {
    const user = userEvent.setup();
    let fail = true;
    server.use(
      http.get('/api/chats', () => (fail ? new HttpResponse(null, { status: 500 }) : HttpResponse.json(MOCK_CHATS))),
      http.get('/api/projects', noProjects),
    );
    renderSidebar();

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load your chats');
    expect(screen.queryByText('No conversations yet')).not.toBeInTheDocument();

    fail = false;
    await user.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Test Chat 1')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('Show more appends the next page, and a refresh keeps it', async () => {
    const user = userEvent.setup();
    const all = Array.from({ length: 60 }, (_, i) => makeChat({ id: `c${i}`, title: `Chat ${i}` }));
    server.use(http.get('/api/chats', ({ request }) => {
      const url = new URL(request.url);
      const limit = Number(url.searchParams.get('limit'));
      const offset = Number(url.searchParams.get('offset') ?? 0);
      return HttpResponse.json({ conversations: all.slice(offset, offset + limit), total: all.length });
    }));
    const { rerender, props } = renderSidebar();

    await screen.findByText('Chat 49');
    expect(screen.queryByText('Chat 50')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Show more' }));
    await screen.findByText('Chat 59');
    expect(screen.queryByRole('button', { name: 'Show more' })).not.toBeInTheDocument();

    rerender(<Sidebar {...props} refreshKey={1} />);
    await new Promise(resolve => setTimeout(resolve, 50));
    expect(screen.getByText('Chat 59')).toBeInTheDocument();
  });

  describe('Show more overlapping a refresh', () => {
    const rows = (n: number) => Array.from({ length: n }, (_, i) => makeChat({ id: `c${i}`, title: `Chat ${i}` }));

    it('a refresh still on its way at the old length cannot shrink the list', async () => {
      const user = userEvent.setup();
      const all = rows(120);
      let release = () => {};
      const gate = new Promise<void>(resolve => { release = resolve; });
      let holdFirstPage = false;
      const firstPageLimits: number[] = [];
      server.use(http.get('/api/chats', async ({ request }) => {
        const url = new URL(request.url);
        if (url.searchParams.get('pinned_only')) return none();
        const limit = Number(url.searchParams.get('limit'));
        const offset = Number(url.searchParams.get('offset') ?? 0);
        if (offset === 0) {
          firstPageLimits.push(limit);
          if (holdFirstPage && limit === 50) await gate;
        }
        return HttpResponse.json({ conversations: all.slice(offset, offset + limit), total: all.length });
      }));
      const { rerender, props } = renderSidebar();
      await screen.findByText('Chat 49');

      holdFirstPage = true;
      rerender(<Sidebar {...props} refreshKey={1} />);
      await waitFor(() => expect(firstPageLimits.at(-1)).toBe(50));
      await user.click(screen.getByRole('button', { name: 'Show more' }));
      await screen.findByText('Chat 99');

      release();
      await waitFor(() => expect(firstPageLimits.at(-1)).toBe(100));
      await new Promise(resolve => setTimeout(resolve, 50));
      expect(screen.getByText('Chat 99')).toBeInTheDocument();
    });

    it('a chat a refresh removed during the wait does not come back', async () => {
      const user = userEvent.setup();
      let all = rows(120);
      let release = () => {};
      const gate = new Promise<void>(resolve => { release = resolve; });
      server.use(
        http.get('/api/chats', async ({ request }) => {
          const url = new URL(request.url);
          if (url.searchParams.get('pinned_only')) return none();
          const limit = Number(url.searchParams.get('limit'));
          const offset = Number(url.searchParams.get('offset') ?? 0);
          // The page Show more asks for, read before the delete.
          const answer = all.slice(offset, offset + limit);
          if (offset > 0) await gate;
          return HttpResponse.json({ conversations: answer, total: all.length });
        }),
        http.delete('/api/chats/:id', ({ params }) => {
          all = all.filter(c => c.id !== params.id);
          return new HttpResponse(null, { status: 204 });
        }),
      );
      renderSidebar();
      await screen.findByText('Chat 49');

      await user.click(screen.getByRole('button', { name: 'Show more' }));

      const row = screen.getByText('Chat 3').closest('.relative') as HTMLElement;
      await user.click(within(row).getByTitle('More'));
      await user.click(within(row).getByText('Delete'));
      await user.click(screen.getByRole('button', { name: 'Delete chat' }));
      await waitFor(() => expect(screen.queryByText('Chat 3')).not.toBeInTheDocument());

      release();
      await screen.findByText('Chat 99');
      await new Promise(resolve => setTimeout(resolve, 50));
      expect(screen.queryByText('Chat 3')).not.toBeInTheDocument();
    });
  });

  it('Show more works inside a project', async () => {
    const user = userEvent.setup();
    const all = Array.from({ length: 51 }, (_, i) => makeChat({ id: `pc${i}`, title: `Filed ${i}` }));
    server.use(http.get('/api/chats', ({ request }) => {
      const url = new URL(request.url);
      if (url.searchParams.get('project_id') !== 'p1') return none();
      const limit = Number(url.searchParams.get('limit'));
      const offset = Number(url.searchParams.get('offset') ?? 0);
      return HttpResponse.json({ conversations: all.slice(offset, offset + limit), total: all.length });
    }));
    renderSidebar();

    await user.click(await screen.findByText('ML Research'));
    await screen.findByText('Filed 49');
    await user.click(screen.getByRole('button', { name: 'Show more' }));
    expect(await screen.findByText('Filed 50')).toBeInTheDocument();
  });

  it('a failed rename says so and closes the editor', async () => {
    const user = userEvent.setup();
    server.use(http.patch('/api/chats/:id', () => new HttpResponse(null, { status: 500 })));
    renderSidebar();
    await screen.findByText('Test Chat 2');

    const row = screen.getByText('Test Chat 2').closest('.relative') as HTMLElement;
    await user.click(within(row).getByTitle('More'));
    await user.click(within(row).getByText('Rename'));
    await user.clear(screen.getByRole('textbox', { name: 'Rename chat' }));
    await user.type(screen.getByRole('textbox', { name: 'Rename chat' }), 'New name{Enter}');

    await waitFor(() => expect(useUiStore.getState().toast).toBe('Could not rename the chat'));
    expect(screen.queryByRole('textbox', { name: 'Rename chat' })).not.toBeInTheDocument();
  });

  it('a failed delete says so instead of rejecting unhandled', async () => {
    const user = userEvent.setup();
    server.use(http.delete('/api/chats/:id', () => new HttpResponse(null, { status: 500 })));
    renderSidebar();
    await screen.findByText('Test Chat 2');

    const row = screen.getByText('Test Chat 2').closest('.relative') as HTMLElement;
    await user.click(within(row).getByTitle('More'));
    await user.click(within(row).getByText('Delete'));
    await user.click(screen.getByRole('button', { name: 'Delete chat' }));

    await waitFor(() => expect(useUiStore.getState().toast).toBe('Could not delete the chat'));
    // The chat is still there, and so is the ⋮ button the menu opened from.
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'More actions for Test Chat 2' })).toHaveFocus();
  });

  it('Escape cancels a rename without saving, and so does the blur that follows', async () => {
    const user = userEvent.setup();
    const saved: unknown[] = [];
    server.use(http.patch('/api/chats/:id', async ({ request }) => {
      saved.push(await request.json());
      return new HttpResponse(null, { status: 200 });
    }));
    renderSidebar();
    await screen.findByText('Test Chat 2');

    const row = screen.getByText('Test Chat 2').closest('.relative') as HTMLElement;
    await user.click(within(row).getByTitle('More'));
    await user.click(within(row).getByText('Rename'));
    await user.type(screen.getByRole('textbox', { name: 'Rename chat' }), ' edited');
    await user.keyboard('{Escape}');

    expect(screen.queryByRole('textbox', { name: 'Rename chat' })).not.toBeInTheDocument();
    await new Promise(resolve => setTimeout(resolve, 20));
    expect(saved).toEqual([]);
  });

  it('the new project input can be cancelled by touch, and forgets what was typed', async () => {
    const user = userEvent.setup();
    server.use(http.get('/api/projects', noProjects));
    renderSidebar();

    await user.click(await screen.findByText('New project'));
    await user.type(screen.getByRole('textbox', { name: 'Project name' }), 'Draft');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('textbox', { name: 'Project name' })).not.toBeInTheDocument();

    await user.click(screen.getByText('New project'));
    expect(screen.getByRole('textbox', { name: 'Project name' })).toHaveValue('');
  });

  it('an empty new project input closes when it loses focus', async () => {
    const user = userEvent.setup();
    server.use(http.get('/api/projects', noProjects));
    renderSidebar();

    await user.click(await screen.findByText('New project'));
    await user.click(screen.getByText('Search chats'));
    expect(screen.queryByRole('textbox', { name: 'Project name' })).not.toBeInTheDocument();
  });

  it('a failed create says so and keeps what was typed', async () => {
    const user = userEvent.setup();
    server.use(
      http.get('/api/projects', noProjects),
      http.post('/api/projects', () => new HttpResponse(null, { status: 500 })),
    );
    renderSidebar();

    await user.click(await screen.findByText('New project'));
    await user.type(screen.getByRole('textbox', { name: 'Project name' }), 'Keep me{Enter}');

    await waitFor(() => expect(useUiStore.getState().toast).toBe('Could not create the project'));
    expect(screen.getByRole('textbox', { name: 'Project name' })).toHaveValue('Keep me');
  });

  describe('search', () => {
    it('says Searching until the current query has answered', async () => {
      const user = userEvent.setup();
      let release = () => {};
      const gate = new Promise<void>(resolve => { release = resolve; });
      renderSidebar();
      await screen.findByText('Test Chat 1');
      server.use(http.get('/api/chats', async ({ request }) => {
        if (new URL(request.url).searchParams.get('search')) await gate;
        return none();
      }));

      await user.click(screen.getByText('Search chats'));
      await user.type(await screen.findByPlaceholderText('Search conversations...'), 'zzz');

      expect(screen.getByText('Searching...')).toBeInTheDocument();
      expect(screen.queryByText('No matching conversations')).not.toBeInTheDocument();

      release();
      expect(await screen.findByText('No matching conversations', {}, { timeout: 2000 })).toBeInTheDocument();
    });

    it('ignores the answer to a query that has been replaced', async () => {
      const user = userEvent.setup();
      let releaseOld = () => {};
      const oldGate = new Promise<void>(resolve => { releaseOld = resolve; });
      renderSidebar();
      await screen.findByText('Test Chat 1');
      server.use(http.get('/api/chats', async ({ request }) => {
        const q = new URL(request.url).searchParams.get('search');
        if (q === 'old*') {
          await oldGate;
          return HttpResponse.json({ conversations: [makeChat({ id: 'o', title: 'Old answer' })], total: 1 });
        }
        if (q === 'new*') return HttpResponse.json({ conversations: [makeChat({ id: 'n', title: 'New answer' })], total: 1 });
        return none();
      }));

      await user.click(screen.getByText('Search chats'));
      const input = await screen.findByPlaceholderText('Search conversations...');
      await user.type(input, 'old');
      await new Promise(resolve => setTimeout(resolve, 350));
      await user.clear(input);
      await user.type(input, 'new');
      await screen.findByText('New answer', {}, { timeout: 2000 });

      releaseOld();
      await new Promise(resolve => setTimeout(resolve, 50));
      expect(screen.getByText('New answer')).toBeInTheDocument();
      expect(screen.queryByText('Old answer')).not.toBeInTheDocument();
    });

    it('finds a loaded chat by title when the server only matched message text', async () => {
      const user = userEvent.setup();
      renderSidebar();
      await screen.findByText('Test Chat 2');
      server.use(http.get('/api/chats', ({ request }) => (
        new URL(request.url).searchParams.get('search') ? none() : HttpResponse.json(MOCK_CHATS)
      )));

      await user.click(screen.getByText('Search chats'));
      await user.type(await screen.findByPlaceholderText('Search conversations...'), 'Old Chat');

      const dialog = await screen.findByRole('dialog');
      await waitFor(() => expect(within(dialog).getByText('Old Chat')).toBeInTheDocument(), { timeout: 2000 });
    });

    it('with no chats at all, says so rather than no match', async () => {
      const user = userEvent.setup();
      server.use(http.get('/api/chats', none));
      renderSidebar();

      await user.click(screen.getByText('Search chats'));
      const dialog = await screen.findByRole('dialog');
      expect(within(dialog).getByText('No conversations yet')).toBeInTheDocument();
    });

    it('is a labelled dialog that Escape closes, returning focus', async () => {
      const user = userEvent.setup();
      renderSidebar();
      const opener = screen.getByText('Search chats').closest('button')!;
      await user.click(opener);

      const dialog = await screen.findByRole('dialog', { name: 'Search your chats' });
      expect(within(dialog).getByRole('textbox', { name: 'Search conversations' })).toHaveFocus();

      await user.keyboard('{Escape}');
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      expect(opener).toHaveFocus();
    });
  });
});

// ── Keyboard and folders (findings 12, 13, 26) ──────────────────────────────

describe('Sidebar keyboard and folders', () => {
  beforeEach(() => _resetWorkspaceStoreForTests());

  const filed = makeChat({ id: 'pc1', title: 'Filed Chat' });
  const mockFiled = () => server.use(http.get('/api/chats', ({ request }) => (
    new URL(request.url).searchParams.get('project_id') === 'p1'
      ? HttpResponse.json({ conversations: [filed], total: 1 })
      : HttpResponse.json(MOCK_CHATS)
  )));

  it('a project folder opens from the keyboard and reports its state', async () => {
    const user = userEvent.setup();
    mockFiled();
    renderSidebar();

    const folder = await screen.findByRole('button', { name: 'ML Research, 2 chats' });
    expect(folder).toHaveAttribute('aria-expanded', 'false');
    folder.focus();
    await user.keyboard('{Enter}');

    expect(folder).toHaveAttribute('aria-expanded', 'true');
    expect(await screen.findByText('Filed Chat')).toBeInTheDocument();
  });

  it('row menus name their chat, take focus, and follow arrow keys and Escape', async () => {
    const user = userEvent.setup();
    renderSidebar();
    const trigger = await screen.findByRole('button', { name: 'More actions for Test Chat 2' });
    expect(trigger).toHaveAttribute('aria-haspopup', 'menu');

    await user.click(trigger);
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('menuitem', { name: 'Pin' })).toHaveFocus();

    await user.keyboard('{ArrowDown}');
    expect(screen.getByRole('menuitem', { name: 'Rename' })).toHaveFocus();
    await user.keyboard('{ArrowDown}');
    expect(screen.getByRole('menuitem', { name: 'Move to project' })).toHaveFocus();

    await user.keyboard('{Enter}');
    expect(screen.getByRole('menuitem', { name: 'ML Research' })).toHaveFocus();

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('a chat row has one action, its ⋮ menu, which pins and unpins', async () => {
    const user = userEvent.setup();
    const calls: string[] = [];
    server.use(
      http.post('/api/chats/:id/pin', ({ params }) => { calls.push(`pin ${params.id}`); return new HttpResponse(null, { status: 200 }); }),
      http.delete('/api/chats/:id/pin', ({ params }) => { calls.push(`unpin ${params.id}`); return new HttpResponse(null, { status: 200 }); }),
    );
    renderSidebar();
    await screen.findByText('Test Chat 2');
    const row = screen.getByText('Test Chat 2').closest('[data-testid="chat-row"]') as HTMLElement;
    expect(within(row).getAllByRole('button')).toHaveLength(1);

    await user.click(within(row).getByRole('button', { name: 'More actions for Test Chat 2' }));
    await user.click(screen.getByRole('menuitem', { name: 'Pin' }));
    await user.click(screen.getByRole('button', { name: 'More actions for Test Chat 1' }));
    await user.click(screen.getByRole('menuitem', { name: 'Unpin' }));

    await waitFor(() => expect(calls).toEqual(['pin c2', 'unpin c1']));
  });

  it('the chat list is one Tab stop that the arrow keys, Home and End move through', async () => {
    const user = userEvent.setup();
    const { props } = renderSidebar({ currentId: 'c2' });
    const first = await screen.findByRole('link', { name: 'Test Chat 1' });
    const second = screen.getByRole('link', { name: 'Test Chat 2' });
    const old = screen.getByRole('link', { name: 'Old Chat' });

    // The open chat holds the stop, and only its row's ⋮ joins the Tab order.
    expect(second).toHaveAttribute('tabindex', '0');
    expect(second).toHaveAttribute('aria-current', 'page');
    for (const el of [first, old]) expect(el).toHaveAttribute('tabindex', '-1');
    expect(screen.getByRole('button', { name: 'More actions for Test Chat 2' })).toHaveAttribute('tabindex', '0');
    expect(screen.getByRole('button', { name: 'More actions for Old Chat' })).toHaveAttribute('tabindex', '-1');

    second.focus();
    await user.keyboard('{ArrowDown}');
    expect(old).toHaveFocus();
    await user.keyboard('{ArrowDown}');
    expect(old).toHaveFocus();
    await user.keyboard('{Home}');
    expect(first).toHaveFocus();
    await user.keyboard('{ArrowUp}');
    expect(first).toHaveFocus();
    await user.keyboard('{End}');
    expect(old).toHaveFocus();
    await user.keyboard('{ArrowUp}');
    expect(second).toHaveFocus();
    await user.keyboard('{ArrowUp}');
    expect(first).toHaveFocus();

    // The stop follows focus, so Tab comes back to the row left last.
    expect(first).toHaveAttribute('tabindex', '0');
    expect(second).toHaveAttribute('tabindex', '-1');

    // Tab goes to this row's ⋮ and then out of the list.
    await user.tab();
    expect(screen.getByRole('button', { name: 'More actions for Test Chat 1' })).toHaveFocus();
    await user.tab();
    expect(document.activeElement?.closest('[data-testid="chat-row"]')).toBeNull();

    first.focus();
    await user.keyboard('{Enter}');
    expect(props.onSelect).toHaveBeenCalledWith('c1');
  });

  it('the arrow keys from a row\'s ⋮ button move to the next row', async () => {
    const user = userEvent.setup();
    renderSidebar();
    const trigger = await screen.findByRole('button', { name: 'More actions for Test Chat 1' });
    trigger.focus();
    await user.keyboard('{ArrowDown}');
    expect(screen.getByRole('link', { name: 'Test Chat 2' })).toHaveFocus();
  });

  it('Shift+F10 or the ContextMenu key on a chat opens its menu, and Escape returns to the row', async () => {
    const user = userEvent.setup();
    renderSidebar();
    const link = await screen.findByRole('link', { name: 'Test Chat 2' });
    link.focus();

    await user.keyboard('{Shift>}{F10}{/Shift}');
    expect(screen.getByRole('menu')).toHaveAccessibleName('Actions for Test Chat 2');
    expect(screen.getByRole('menuitem', { name: 'Pin' })).toHaveFocus();
    // The arrow keys inside the menu stay in the menu.
    await user.keyboard('{ArrowDown}');
    expect(screen.getByRole('menuitem', { name: 'Rename' })).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'More actions for Test Chat 2' })).toHaveFocus();

    link.focus();
    await user.keyboard('{ContextMenu}');
    expect(screen.getByRole('menu')).toHaveAccessibleName('Actions for Test Chat 2');
  });

  it('pinning from the keyboard leaves focus on the chat in its new group', async () => {
    const user = userEvent.setup();
    let pinned = false;
    server.use(
      http.post('/api/chats/:id/pin', () => { pinned = true; return new HttpResponse(null, { status: 200 }); }),
      http.get('/api/chats', () => HttpResponse.json({
        ...MOCK_CHATS,
        conversations: MOCK_CHATS.conversations.map(c => (c.id === 'c3' ? { ...c, pinned } : c)),
      })),
    );
    renderSidebar();
    const link = await screen.findByRole('link', { name: 'Old Chat' });
    link.focus();
    await user.keyboard('{Shift>}{F10}{/Shift}');
    await user.keyboard('{Enter}');

    await waitFor(() => expect(within(screen.getByRole('list', { name: 'Pinned' })).getByRole('link', { name: 'Old Chat' })).toHaveFocus());
  });

  it('lists each date group as a list named by its label', async () => {
    renderSidebar();
    await screen.findByText('Test Chat 1');
    expect(within(screen.getByRole('list', { name: 'Pinned' })).getByRole('link', { name: 'Test Chat 1' })).toBeInTheDocument();
    expect(within(screen.getByRole('list', { name: 'Yesterday' })).getAllByRole('listitem')).toHaveLength(1);
  });

  it('a row menu near the bottom of the window opens upward, outside the scrolling list', async () => {
    const user = userEvent.setup();
    renderSidebar();
    const trigger = await screen.findByRole('button', { name: 'More actions for Old Chat' });
    const bottom = window.innerHeight - 10;
    vi.spyOn(trigger, 'getBoundingClientRect').mockReturnValue({ top: bottom - 20, bottom, right: 250 } as DOMRect);
    const height = vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(120);
    onTestFinished(() => { height.mockRestore(); });

    await user.click(trigger);

    const menu = screen.getByRole('menu');
    expect(menu).toHaveClass('fixed');
    expect(menu.style.top).toBe(`${bottom - 20 - 4 - 120}px`);
  });

  it('opening another row menu closes the first', async () => {
    const user = userEvent.setup();
    renderSidebar();
    await user.click(await screen.findByRole('button', { name: 'More actions for Test Chat 2' }));
    await user.click(screen.getByRole('button', { name: 'More actions for Old Chat' }));

    expect(screen.getAllByRole('menu')).toHaveLength(1);
    expect(screen.getByRole('menu')).toHaveAccessibleName('Actions for Old Chat');
  });

  it('keeps a filed chat\'s folder open when the active project clears', async () => {
    mockFiled();
    useWorkspaceStore.setState({ activeProjectId: 'p1' });
    const { rerender, props } = renderSidebar({ currentId: 'pc1' });
    await screen.findByText('Filed Chat');

    useWorkspaceStore.setState({ activeProjectId: null });
    rerender(<Sidebar {...props} currentId="pc1" />);

    await waitFor(() => expect(screen.getByRole('button', { name: /^ML Research/ })).toHaveAttribute('aria-expanded', 'true'));
  });

  it('opens the folder of a chat that no list holds, such as a direct link', async () => {
    mockFiled();
    server.use(http.get('/api/chats/pc1', () => HttpResponse.json({ ...filed, messages: [], project_id: 'p1' })));
    renderSidebar({ currentId: 'pc1' });

    expect(await screen.findByText('Filed Chat')).toBeInTheDocument();
  });
});

describe('Sidebar polish', () => {
  beforeEach(() => _resetWorkspaceStoreForTests());

  const rowFor = (title: string) => screen.getByText(title).closest('.relative') as HTMLElement;

  it('shows the full title of a chat and a project on hover', async () => {
    renderSidebar();
    expect(await screen.findByText('Test Chat 2')).toHaveAttribute('title', 'Test Chat 2');
    expect(screen.getByText('ML Research')).toHaveAttribute('title', 'ML Research');
  });

  describe('a chat that is generating in the background', () => {
    afterEach(() => vi.useRealTimers());

    it('has a label a screen reader can read, and its dot goes out once the turn ends', async () => {
      vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
      let generating = true;
      let reads = 0;
      server.use(http.get('/api/chats', ({ request }) => {
        const params = new URL(request.url).searchParams;
        if (params.get('pinned_only') || params.get('project_id') !== '__unfiled__') {
          return HttpResponse.json({ conversations: [], total: 0 });
        }
        reads += 1;
        return HttpResponse.json({ conversations: [makeChat({ id: 'g1', title: 'Busy Chat', generating })], total: 1 });
      }));
      renderSidebar();

      expect(await within(await waitFor(() => rowFor('Busy Chat'))).findByText('Generating')).toBeInTheDocument();
      const before = reads;

      generating = false;
      await act(async () => { vi.advanceTimersByTime(12_000); });
      await waitFor(() => expect(reads).toBeGreaterThan(before));
      await waitFor(() => expect(screen.queryByTestId('chat-generating')).not.toBeInTheDocument());

      // Nothing is generating, so the polling has stopped.
      const after = reads;
      await act(async () => { vi.advanceTimersByTime(60_000); });
      expect(reads).toBe(after);
    });

    it('does not poll when nothing is generating', async () => {
      vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
      let reads = 0;
      server.use(http.get('/api/chats', () => {
        reads += 1;
        return HttpResponse.json(MOCK_CHATS);
      }));
      renderSidebar();
      await screen.findByText('Test Chat 2');
      await waitFor(() => expect(reads).toBeGreaterThan(0));
      const settled = reads;

      await act(async () => { vi.advanceTimersByTime(60_000); });
      expect(reads).toBe(settled);
    });
  });

  describe('Move to project', () => {
    const filed = makeChat({ id: 'pc1', title: 'Filed Chat', project_id: 'p1' } as Partial<ConversationSummary>);
    const mockFiled = () => server.use(http.get('/api/chats', ({ request }) => (
      new URL(request.url).searchParams.get('project_id') === 'p1'
        ? HttpResponse.json({ conversations: [filed], total: 1 })
        : HttpResponse.json(MOCK_CHATS)
    )));

    it('leaves out the project the chat is in, and offers Remove from project once', async () => {
      const user = userEvent.setup();
      mockFiled();
      renderSidebar();
      await user.click(await screen.findByText('ML Research'));
      const row = await waitFor(() => rowFor('Filed Chat'));

      await user.click(within(row).getByTitle('More'));
      expect(within(row).getAllByRole('menuitem', { name: 'Remove from project' })).toHaveLength(1);
      await user.click(within(row).getByRole('menuitem', { name: 'Move to project' }));

      const menu = within(row).getByRole('menu');
      expect(within(menu).getByText('Move to')).toBeInTheDocument();
      expect(within(menu).queryByRole('menuitem', { name: 'ML Research' })).not.toBeInTheDocument();
      expect(within(menu).getByRole('menuitem', { name: 'Code Review' })).toHaveFocus();
      expect(within(menu).queryByRole('menuitem', { name: 'Remove from project' })).not.toBeInTheDocument();
    });

    it('has no Move to project item when the chat is already in the only project', async () => {
      const user = userEvent.setup();
      server.use(http.get('/api/projects', () => HttpResponse.json({ projects: [MOCK_PROJECTS.projects[0]], total: 1 })));
      mockFiled();
      renderSidebar();
      await user.click(await screen.findByText('ML Research'));
      const row = await waitFor(() => rowFor('Filed Chat'));

      await user.click(within(row).getByTitle('More'));
      expect(within(row).queryByRole('menuitem', { name: 'Move to project' })).not.toBeInTheDocument();
    });

    it('Back returns to the actions, with focus on Move to project', async () => {
      const user = userEvent.setup();
      renderSidebar();
      await user.click(await screen.findByRole('button', { name: 'More actions for Test Chat 2' }));
      await user.click(screen.getByRole('menuitem', { name: 'Move to project' }));

      await user.click(screen.getByRole('menuitem', { name: 'Back' }));

      expect(screen.getByRole('menuitem', { name: 'Rename' })).toBeInTheDocument();
      expect(screen.getByRole('menuitem', { name: 'Move to project' })).toHaveFocus();
    });
  });

  describe('the delete confirmation', () => {
    it('asks about a project in an alertdialog that says its chats stay in the chat list', async () => {
      const user = userEvent.setup();
      const deleted: string[] = [];
      server.use(http.delete('/api/projects/:id', ({ params }) => {
        deleted.push(params.id as string);
        return new HttpResponse(null, { status: 204 });
      }));
      renderSidebar();
      await user.click(await screen.findByRole('button', { name: 'More actions for ML Research' }));
      await user.click(screen.getByRole('menuitem', { name: 'Delete project' }));

      const dialog = screen.getByRole('alertdialog', { name: 'Delete project?' });
      expect(dialog).toHaveAttribute('aria-modal', 'true');
      expect(dialog).toHaveAccessibleDescription('“ML Research” will be deleted. Chats in this project will stay in your chat list.');
      expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus();
      expect(within(dialog).getByRole('button', { name: 'Delete project' })).toBeInTheDocument();
      expect(deleted).toEqual([]);
    });

    it('asks about a chat by its title, and Cancel deletes nothing and goes back to the ⋮ button', async () => {
      const user = userEvent.setup();
      const deleted: string[] = [];
      server.use(http.delete('/api/chats/:id', ({ params }) => {
        deleted.push(params.id as string);
        return new HttpResponse(null, { status: 204 });
      }));
      renderSidebar();
      const more = await screen.findByRole('button', { name: 'More actions for Test Chat 2' });
      await user.click(more);
      await user.click(screen.getByRole('menuitem', { name: 'Delete' }));

      const dialog = screen.getByRole('alertdialog', { name: 'Delete chat?' });
      expect(dialog).toHaveAccessibleDescription('“Test Chat 2” and all its messages will be deleted. This cannot be undone.');
      await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));

      expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
      expect(more).toHaveFocus();
      expect(deleted).toEqual([]);
    });

    it('keeps Tab inside, and Escape cancels', async () => {
      const user = userEvent.setup();
      renderSidebar();
      const more = await screen.findByRole('button', { name: 'More actions for Test Chat 2' });
      await user.click(more);
      await user.click(screen.getByRole('menuitem', { name: 'Delete' }));

      const dialog = screen.getByRole('alertdialog');
      const cancel = within(dialog).getByRole('button', { name: 'Cancel' });
      const confirm = within(dialog).getByRole('button', { name: 'Delete chat' });
      await user.tab();
      expect(confirm).toHaveFocus();
      await user.tab();
      expect(cancel).toHaveFocus();
      await user.tab({ shift: true });
      expect(confirm).toHaveFocus();

      await user.keyboard('{Escape}');
      expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
      expect(screen.getByText('Test Chat 2')).toBeInTheDocument();
      expect(more).toHaveFocus();
    });

    it('after deleting a chat, focus goes to the next chat in the list', async () => {
      const user = userEvent.setup();
      let deleted = false;
      server.use(
        http.get('/api/chats', ({ request }) => {
          if (new URL(request.url).searchParams.get('project_id') === 'p1') {
            return HttpResponse.json({ conversations: [], total: 0 });
          }
          const list = MOCK_CHATS.conversations.filter(c => !deleted || c.id !== 'c1');
          return HttpResponse.json({ conversations: list, total: list.length });
        }),
        http.delete('/api/chats/:id', () => { deleted = true; return new HttpResponse(null, { status: 204 }); }),
      );
      renderSidebar();
      await user.click(await screen.findByRole('button', { name: 'More actions for Test Chat 1' }));
      await user.click(screen.getByRole('menuitem', { name: 'Delete' }));
      await user.click(screen.getByRole('button', { name: 'Delete chat' }));

      await waitFor(() => expect(screen.queryByText('Test Chat 1')).not.toBeInTheDocument());
      expect(screen.getByRole('link', { name: 'Test Chat 2' })).toHaveFocus();
    });

    it('after deleting the last project, focus goes to New chat', async () => {
      const user = userEvent.setup();
      let projects = [MOCK_PROJECTS.projects[0]];
      server.use(
        http.get('/api/projects', () => HttpResponse.json({ projects, total: projects.length })),
        http.delete('/api/projects/:id', () => { projects = []; return new HttpResponse(null, { status: 204 }); }),
      );
      renderSidebar();
      await user.click(await screen.findByRole('button', { name: 'More actions for ML Research' }));
      await user.click(screen.getByRole('menuitem', { name: 'Delete project' }));
      await user.click(screen.getByRole('button', { name: 'Delete project' }));

      await waitFor(() => expect(screen.queryByText('ML Research')).not.toBeInTheDocument());
      expect(screen.getByRole('link', { name: 'New chat' })).toHaveFocus();
    });
  });

  describe('an empty project', () => {
    const mockEmpty = () => server.use(http.get('/api/chats', ({ request }) => (
      new URL(request.url).searchParams.get('project_id') === 'p2'
        ? HttpResponse.json({ conversations: [], total: 0 })
        : HttpResponse.json(MOCK_CHATS)
    )));

    it('says what a project is for and offers a new chat in it', async () => {
      const user = userEvent.setup();
      mockEmpty();
      const onNewChatInProject = vi.fn();
      renderSidebar({ onNewChatInProject });
      await user.click(await screen.findByText('Code Review'));

      expect(await screen.findByText('Projects keep chats together and can share instructions.')).toBeInTheDocument();
      const link = screen.getByRole('link', { name: 'New chat in this project' });
      expect(link).toHaveAttribute('href', '/?project=p2');

      await user.click(link);
      expect(onNewChatInProject).toHaveBeenCalledWith('p2');
    });
  });
});
