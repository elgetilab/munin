import { render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from '../test/msw-server';
import { Settings } from './Settings';
import type { UserProfile } from '../lib/api';
import { _resetUiStoreForTests } from '../stores/uiStore';
import { _resetUserStoreForTests } from '../stores/userStore';

// Characterization tests written before the fetch-on-mount restructure.
// Settings fans out to four independent reads on mount (keys, usage, the
// Munin profile, and the announcement for admins), each swallowing its own
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
    // The failure is swallowed by design; the rest of the page must survive.
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
});
