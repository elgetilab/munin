import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../test/msw-server';
import { groupByTime, Sidebar } from './Sidebar';
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
  it('three-dots menu opens with Rename / Move to project / Delete items', async () => {
    const user = userEvent.setup();
    renderSidebar();

    await waitFor(() => {
      expect(screen.getByText('Test Chat 1')).toBeInTheDocument();
    });

    // Find the kebab button on the first chat row by its title attribute.
    // There's one per row; use the first.
    const moreButtons = screen.getAllByTitle('More');
    expect(moreButtons.length).toBeGreaterThan(0);

    // No menu items visible before opening.
    expect(screen.queryByText('Rename')).not.toBeInTheDocument();

    await user.click(moreButtons[0]);

    await waitFor(() => {
      expect(screen.getByText('Rename')).toBeInTheDocument();
    });
    expect(screen.getByText('Move to project')).toBeInTheDocument();
    expect(screen.getByText('Delete')).toBeInTheDocument();
  });

  it('three-dots menu: clicking Rename opens the inline editor and closes the menu', async () => {
    const user = userEvent.setup();
    renderSidebar();

    await waitFor(() => {
      expect(screen.getByText('Test Chat 1')).toBeInTheDocument();
    });

    const moreButtons = screen.getAllByTitle('More');
    await user.click(moreButtons[0]);

    await waitFor(() => {
      expect(screen.getByText('Rename')).toBeInTheDocument();
    });
    await user.click(screen.getByText('Rename'));

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

    const moreButtons = screen.getAllByTitle('More');
    await user.click(moreButtons[0]);

    await waitFor(() => {
      expect(screen.getByText('Rename')).toBeInTheDocument();
    });

    // Click on something outside the menu (the "New chat" button).
    await user.click(screen.getByText('New chat'));

    await waitFor(() => {
      expect(screen.queryByText('Rename')).not.toBeInTheDocument();
    });
  });
});
