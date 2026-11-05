import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../test/msw-server';
import { Settings } from './Settings';
import type { UserProfile } from '../lib/api';
import { useUiStore, _resetUiStoreForTests } from '../stores/uiStore';
import { useUserStore, _resetUserStoreForTests } from '../stores/userStore';

// Characterization tests written before the fetch-on-mount restructure.
// Settings fans out to four independent reads on mount (keys, usage, the
// Munin profile, and the announcement for admins), each handling its own
// errors, so what matters is that one failing read does not take the others
// down with it.

const PROFILE: UserProfile = {
  email: 'test@test.com', name: 'Test User', full_name: 'Test User',
  nickname: 'tester', avatar: '',
} as UserProfile;

function renderSettings() {
  return render(<Settings profile={PROFILE} onUpdate={vi.fn()} />);
}

describe('Settings', () => {
  beforeEach(() => { _resetUiStoreForTests(); _resetUserStoreForTests(); });

  it('renders the account section from the profile prop', async () => {
    renderSettings();
    expect(screen.getByText('Settings')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByDisplayValue('Test User')).toBeInTheDocument());
  });

  it('lists the API keys returned by the server', async () => {
    renderSettings();
    await waitFor(() => expect(screen.getByText(/sk-munin-abc/)).toBeInTheDocument());
    expect(screen.getByText('laptop')).toBeInTheDocument();
  });

  it('still renders when the key list fails to load', async () => {
    server.use(http.get('/api/keys', () => new HttpResponse(null, { status: 500 })));
    renderSettings();
    // The failure stays in the API keys section; the rest of the page must survive.
    await waitFor(() => expect(screen.getByText('API Keys')).toBeInTheDocument());
    expect(screen.getByText('Munin Profile')).toBeInTheDocument();
    expect(screen.queryByText('laptop')).not.toBeInTheDocument();
  });

  it('still renders when the Munin profile has never been created', async () => {
    server.use(http.get('/api/profile', () => new HttpResponse(null, { status: 404 })));
    renderSettings();
    await waitFor(() => expect(screen.getByText('Munin Profile')).toBeInTheDocument());
    expect(screen.getByText('API Keys')).toBeInTheDocument();
  });

  describe('monthly usage bar', () => {
    const withUsage = (tokens_used: number) => server.use(http.get('/api/usage/me', () => HttpResponse.json({
      current_month: { tokens_used, tokens_limit: 1000000, tokens_remaining: 1000000 - tokens_used, requests: 3, tools_used: {} },
      api_keys: [],
    })));

    it.each([
      [420000, 42, 'bg-accent'],
      [750000, 75, 'bg-warning'],
      [950000, 95, 'bg-error'],
    ])('fills in a defined colour at %i tokens', async (used, percent, colour) => {
      withUsage(used);
      renderSettings();
      const bar = await screen.findByRole('progressbar', { name: 'Monthly token usage' });
      expect(bar).toHaveAttribute('aria-valuenow', String(percent));
      const fill = bar.firstElementChild as HTMLElement;
      expect(fill).toHaveClass(colour);
      expect(fill.style.width).toBe(`${percent}%`);
    });
  });

  describe('Munin profile load', () => {
    const saveButton = () => screen.getByRole('button', { name: 'Save profile' });

    it('keeps Save profile off and sends nothing when the load fails', async () => {
      const user = userEvent.setup();
      let saves = 0;
      server.use(
        http.get('/api/profile', () => new HttpResponse(null, { status: 500 })),
        http.put('/api/profile', () => { saves += 1; return HttpResponse.json({}); }),
      );
      renderSettings();
      expect(await screen.findByText(/Could not load your Munin profile/)).toBeInTheDocument();
      expect(saveButton()).toBeDisabled();
      await user.click(saveButton());
      expect(saves).toBe(0);
    });

    it('keeps Save profile off while the profile is still loading', async () => {
      let finish!: () => void;
      const held = new Promise<void>(resolve => { finish = resolve; });
      server.use(http.get('/api/profile', async () => {
        await held;
        return HttpResponse.json({ user_email: 'test@test.com', about_me: 'stored' });
      }));
      renderSettings();
      expect(saveButton()).toBeDisabled();
      finish();
      await waitFor(() => expect(saveButton()).toBeEnabled());
      expect(screen.getByDisplayValue('stored')).toBeInTheDocument();
    });

    it('allows Save profile when no profile exists yet', async () => {
      server.use(http.get('/api/profile', () => new HttpResponse(null, { status: 404 })));
      renderSettings();
      await waitFor(() => expect(saveButton()).toBeEnabled());
      expect(screen.queryByText(/Could not load your Munin profile/)).not.toBeInTheDocument();
    });

    it('Retry loads the stored values and turns saving back on', async () => {
      const user = userEvent.setup();
      let fail = true;
      server.use(http.get('/api/profile', () => (fail
        ? new HttpResponse(null, { status: 503 })
        : HttpResponse.json({ user_email: 'test@test.com', about_me: 'stored' }))));
      renderSettings();
      const notice = (await screen.findByText(/Could not load your Munin profile/)).closest('[role="alert"]') as HTMLElement;
      fail = false;
      await user.click(within(notice).getByRole('button', { name: 'Retry' }));
      await waitFor(() => expect(saveButton()).toBeEnabled());
      expect(screen.getByDisplayValue('stored')).toBeInTheDocument();
      expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });
  });

  describe('failures that used to be silent', () => {
    it('says the save failed', async () => {
      const user = userEvent.setup();
      server.use(http.patch('*/auth/me', () => new HttpResponse(null, { status: 500 })));
      renderSettings();
      await user.click(screen.getByRole('button', { name: 'Save changes' }));
      expect(await screen.findByText(/Could not save your changes/)).toBeInTheDocument();
    });

    it('says the reset failed', async () => {
      const user = userEvent.setup();
      vi.spyOn(window, 'confirm').mockReturnValue(true);
      server.use(http.delete('/api/profile', () => new HttpResponse(null, { status: 500 })));
      renderSettings();
      await user.click(await screen.findByRole('button', { name: 'Reset' }));
      expect(await screen.findByText(/Could not reset your profile/)).toBeInTheDocument();
    });

    it('says creating a key failed', async () => {
      const user = userEvent.setup();
      server.use(http.post('/api/keys', () => new HttpResponse(null, { status: 500 })));
      renderSettings();
      await user.click(screen.getByRole('button', { name: 'Create key' }));
      expect(await screen.findByText(/Could not create the key/)).toBeInTheDocument();
    });

    it('says revoking a key failed', async () => {
      const user = userEvent.setup();
      vi.spyOn(window, 'confirm').mockReturnValue(true);
      server.use(http.delete('/api/keys/:id', () => new HttpResponse(null, { status: 500 })));
      renderSettings();
      await screen.findByText('laptop');
      await user.click(screen.getByText('Revoke'));
      expect(await screen.findByText(/Could not revoke the key/)).toBeInTheDocument();
    });

    it('does not claim there are no keys when the list failed to load, and Retry recovers', async () => {
      const user = userEvent.setup();
      let fail = true;
      server.use(http.get('/api/keys', () => (fail
        ? new HttpResponse(null, { status: 500 })
        : HttpResponse.json({ keys: [] }))));
      renderSettings();
      const message = await screen.findByText('Could not load your API keys.');
      expect(screen.queryByText(/No API keys yet/)).not.toBeInTheDocument();
      fail = false;
      await user.click(within(message.closest('[role="alert"]') as HTMLElement).getByRole('button', { name: 'Retry' }));
      expect(await screen.findByText(/No API keys yet/)).toBeInTheDocument();
      expect(screen.queryByText('Could not load your API keys.')).not.toBeInTheDocument();
    });
  });

  describe('announcement banner', () => {
    beforeEach(() => {
      useUserStore.setState({ isAdmin: true });
      server.use(
        http.put('/api/announcement', () => HttpResponse.json({ ok: true })),
        http.delete('/api/announcement', () => HttpResponse.json({ ok: true })),
      );
    });

    it('shows a new announcement to the admin at once, even after dismissing the old one', async () => {
      const user = userEvent.setup();
      useUiStore.setState({ announcementDismissed: true });
      renderSettings();
      await user.type(screen.getByPlaceholderText(/Cluster maintenance/), 'Down at noon');
      await user.click(screen.getByRole('button', { name: 'warning' }));
      await user.click(screen.getByRole('button', { name: 'Set announcement' }));
      await screen.findByText('Announcement set');
      const state = useUiStore.getState();
      expect(state.announcement).toMatchObject({ message: 'Down at noon', level: 'warning' });
      expect(state.announcementDismissed).toBe(false);
    });

    it('removes the banner when the announcement is cleared', async () => {
      const user = userEvent.setup();
      useUiStore.setState({ announcement: { message: 'Old', level: 'info', updated_at: '2026-01-01T00:00:00Z' } });
      renderSettings();
      await user.click(screen.getByRole('button', { name: 'Clear announcement' }));
      await screen.findByText('Announcement cleared');
      expect(useUiStore.getState().announcement).toBeNull();
    });

    it('leaves the banner alone when saving fails', async () => {
      const user = userEvent.setup();
      server.use(http.put('/api/announcement', () => new HttpResponse(null, { status: 500 })));
      renderSettings();
      await user.type(screen.getByPlaceholderText(/Cluster maintenance/), 'Down at noon');
      await user.click(screen.getByRole('button', { name: 'Set announcement' }));
      await screen.findByText('Failed');
      expect(useUiStore.getState().announcement).toBeNull();
    });
  });

  describe('newly created key', () => {
    async function createKey(user: ReturnType<typeof userEvent.setup>) {
      renderSettings();
      await user.click(screen.getByRole('button', { name: 'Create key' }));
      await screen.findByText('sk-munin-secret-value');
    }

    it('says Copied! only after the browser accepted the copy', async () => {
      const user = userEvent.setup();
      const write = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue();
      await createKey(user);
      await user.click(screen.getByRole('button', { name: 'Copy' }));
      expect(await screen.findByRole('button', { name: 'Copied!' })).toBeInTheDocument();
      expect(write).toHaveBeenCalledWith('sk-munin-secret-value');
    });

    it('says the copy failed when the browser refuses it', async () => {
      const user = userEvent.setup();
      vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValue(new Error('denied'));
      await createKey(user);
      await user.click(screen.getByRole('button', { name: 'Copy' }));
      expect(await screen.findByText(/Copy failed/)).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Copied!' })).not.toBeInTheDocument();
    });

    it('Done removes the key banner', async () => {
      const user = userEvent.setup();
      await createKey(user);
      await user.click(screen.getByRole('button', { name: 'Done' }));
      expect(screen.queryByText('sk-munin-secret-value')).not.toBeInTheDocument();
    });
  });

  describe('accessible names', () => {
    it('ties each label to its control and names the icon-only and repeated buttons', async () => {
      useUserStore.setState({ personas: [{ id: 'chat', name: 'Chat' }] } as never);
      renderSettings();
      expect(screen.getByLabelText('Full name')).toHaveValue('Test User');
      expect(screen.getByLabelText('What should Munin call you?')).toHaveValue('tester');
      expect(screen.getByLabelText('About you').tagName).toBe('TEXTAREA');
      expect(screen.getByLabelText('How should Munin respond?').tagName).toBe('TEXTAREA');
      expect(screen.getByLabelText('Default persona').tagName).toBe('SELECT');
      expect(screen.getByLabelText('Timezone').tagName).toBe('SELECT');
      expect(screen.getByLabelText('API key name')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Change profile picture' })).toBeInTheDocument();
      expect(await screen.findByRole('button', { name: 'Revoke laptop' })).toBeInTheDocument();
    });

    it('labels the announcement text and marks the chosen level', async () => {
      const user = userEvent.setup();
      useUserStore.setState({ isAdmin: true });
      renderSettings();
      expect(screen.getByLabelText('Announcement message').tagName).toBe('TEXTAREA');
      const group = screen.getByRole('group', { name: 'Level:' });
      expect(within(group).getByRole('button', { name: 'info' })).toHaveAttribute('aria-pressed', 'true');
      await user.click(within(group).getByRole('button', { name: 'error' }));
      expect(within(group).getByRole('button', { name: 'error' })).toHaveAttribute('aria-pressed', 'true');
      expect(within(group).getByRole('button', { name: 'info' })).toHaveAttribute('aria-pressed', 'false');
    });
  });

  describe('timezone', () => {
    const select = () => screen.getByLabelText('Timezone') as HTMLSelectElement;
    const withStored = (timezone: string) => server.use(http.get('/api/profile', () => HttpResponse.json({ user_email: 'test@test.com', timezone })));

    it('offers zones the short list missed, with no duplicate options', () => {
      renderSettings();
      const values = Array.from(select().options).map(o => o.value);
      expect(values).toEqual(expect.arrayContaining(['UTC', 'Africa/Nairobi', 'America/Sao_Paulo', 'Asia/Dubai']));
      expect(new Set(values).size).toBe(values.length);
    });

    it("offers this device's zone first", () => {
      renderSettings();
      const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
      const first = select().options[1];
      expect(first.value).toBe(zone);
      expect(first.textContent).toBe(`${zone} (this device)`);
    });

    it('shows a stored zone that is not in the list', async () => {
      withStored('US/Eastern');
      renderSettings();
      await waitFor(() => expect(select()).toHaveValue('US/Eastern'));
    });

    it('shows a stored zone that is in the list', async () => {
      withStored('Africa/Nairobi');
      renderSettings();
      await waitFor(() => expect(select()).toHaveValue('Africa/Nairobi'));
    });
  });
});
