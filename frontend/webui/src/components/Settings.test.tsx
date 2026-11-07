import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../test/msw-server';
import { Settings } from './Settings';
import type { UserProfile } from '../lib/api';
import { useUiStore, _resetUiStoreForTests } from '../stores/uiStore';
import { useUserStore, _resetUserStoreForTests } from '../stores/userStore';

// Characterization tests written before the fetch-on-mount restructure.
// Settings fans out to three independent reads on mount (keys, usage and
// the Munin profile), each handling its own
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
    expect(screen.getByText('How Munin works with you')).toBeInTheDocument();
    expect(screen.queryByText('laptop')).not.toBeInTheDocument();
  });

  it('still renders when the Munin profile has never been created', async () => {
    server.use(http.get('/api/profile', () => new HttpResponse(null, { status: 404 })));
    renderSettings();
    await waitFor(() => expect(screen.getByText('How Munin works with you')).toBeInTheDocument());
    expect(screen.getByText('API Keys')).toBeInTheDocument();
  });

  it('closes from a labelled Back to chat control', async () => {
    const user = userEvent.setup();
    useUiStore.setState({ showSettings: true });
    renderSettings();
    await user.click(screen.getByRole('button', { name: 'Back to chat' }));
    expect(useUiStore.getState().showSettings).toBe(false);
  });

  it('heads the first block Account and explains the profile in plain words', () => {
    renderSettings();
    expect(screen.getByRole('heading', { name: 'Account' })).toBeInTheDocument();
    expect(screen.getByText(/Munin reads this at the start of every chat/)).toBeInTheDocument();
    expect(screen.queryByText(/injected/)).not.toBeInTheDocument();
  });

  it('explains what a persona is and what the empty choice does', () => {
    useUserStore.setState({ personas: [{ id: 'chat', name: 'Chat' }] } as never);
    renderSettings();
    const select = screen.getByLabelText('Default persona');
    expect(select).toHaveAccessibleDescription(/A persona is a preset way for Munin to work/);
    expect(within(select).getByRole('option', { name: 'None (Munin picks for each chat)' })).toHaveValue('');
  });

  describe('profile picture', () => {
    const fileInput = (container: HTMLElement) => container.querySelector('input[type="file"]') as HTMLInputElement;
    const picture = (container: HTMLElement) => container.querySelector('img');

    it('states the size limit up front', () => {
      renderSettings();
      expect(screen.getByText('Image up to 150 KB')).toBeInTheDocument();
    });

    it('shows a too-large image as inline text, not an alert dialog', async () => {
      const user = userEvent.setup();
      const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});
      const { container } = renderSettings();
      const big = new File([new Uint8Array(200_000)], 'big.png', { type: 'image/png' });
      await user.upload(fileInput(container), big);
      expect(screen.getByRole('alert')).toHaveTextContent('That image is 200 KB. Choose one up to 150 KB.');
      expect(alertSpy).not.toHaveBeenCalled();
      expect(picture(container)).toBeNull();
    });

    it('clears the file input so the same file can be picked again', async () => {
      const user = userEvent.setup();
      const { container } = renderSettings();
      const input = fileInput(container);
      const small = new File([new Uint8Array(10)], 'me.png', { type: 'image/png' });
      await user.upload(input, small);
      await waitFor(() => expect(picture(container)).not.toBeNull());
      expect(input.value).toBe('');
    });

    it('says a changed picture is not saved until Save changes', async () => {
      const user = userEvent.setup();
      server.use(http.patch('*/auth/me', async ({ request }) => HttpResponse.json({ ...PROFILE, ...(await request.json() as object) })));
      const { container } = renderSettings();
      const small = new File([new Uint8Array(10)], 'me.png', { type: 'image/png' });
      await user.upload(fileInput(container), small);
      expect(await screen.findByText(/Not saved yet/)).toBeInTheDocument();
      await user.click(screen.getByRole('button', { name: 'Save changes' }));
      await waitFor(() => expect(screen.queryByText(/Not saved yet/)).not.toBeInTheDocument());
    });
  });

  describe('API usage', () => {
    it('shows the monthly limit from the server, not a fixed figure', async () => {
      renderSettings();
      expect(await screen.findByText('1.0M tokens/month')).toBeInTheDocument();
      expect(screen.queryByText('100M tokens/month')).not.toBeInTheDocument();
      expect(screen.getByText('Monthly API usage (browser chat is not counted)')).toBeInTheDocument();
    });

    it('hides the limit until usage has loaded', () => {
      server.use(http.get('/api/usage/me', () => new Promise(() => { /* never resolves */ })));
      renderSettings();
      expect(screen.queryByText(/tokens\/month/)).not.toBeInTheDocument();
    });

    it('counts API requests only when the server reports them', async () => {
      server.use(http.get('/api/usage/me', () => HttpResponse.json({
        current_month: { tokens_used: 0, tokens_limit: 1000000, tokens_remaining: 1000000, requests: 40, api_requests: 3, tools_used: {} },
        api_keys: [],
      })));
      renderSettings();
      expect(await screen.findByText('3 API requests this month')).toBeInTheDocument();
    });
  });

  describe('quick start', () => {
    it('copies the snippet and says so', async () => {
      const user = userEvent.setup();
      const write = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue();
      renderSettings();
      await user.click(screen.getByRole('button', { name: 'Copy quick start code' }));
      expect(await screen.findByRole('button', { name: 'Copied quick start code' })).toHaveTextContent('Copied!');
      expect(write).toHaveBeenCalledWith(expect.stringContaining('from openai import OpenAI'));
    });

    it('says the copy failed when the browser refuses it', async () => {
      const user = userEvent.setup();
      vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValue(new Error('denied'));
      renderSettings();
      await user.click(screen.getByRole('button', { name: 'Copy quick start code' }));
      expect(await screen.findByText(/Select the code above and copy it by hand/)).toBeInTheDocument();
    });

    it('wraps long lines instead of scrolling sideways', () => {
      renderSettings();
      const pre = screen.getByText(/from openai import OpenAI/);
      expect(pre).toHaveClass('whitespace-pre-wrap');
      expect(pre).not.toHaveClass('overflow-x-auto');
    });
  });

  it('no longer holds the admin announcement, which lives in the Admin Dashboard', () => {
    useUserStore.setState({ isAdmin: true });
    renderSettings();
    expect(screen.queryByLabelText('Announcement message')).not.toBeInTheDocument();
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
