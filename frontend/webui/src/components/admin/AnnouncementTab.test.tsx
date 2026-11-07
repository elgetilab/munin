import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../../test/msw-server';
import { AnnouncementTab } from './AnnouncementTab';
import { useUiStore, _resetUiStoreForTests } from '../../stores/uiStore';

describe('AnnouncementTab', () => {
  beforeEach(() => {
    _resetUiStoreForTests();
    server.use(
      http.put('/api/announcement', () => HttpResponse.json({ ok: true })),
      http.delete('/api/announcement', () => HttpResponse.json({ ok: true })),
    );
  });

  const button = (name: string | RegExp) => screen.getByRole('button', { name });

  it('shows a new announcement to the admin at once, even after dismissing the old one', async () => {
    const user = userEvent.setup();
    useUiStore.setState({ announcementDismissed: true });
    render(<AnnouncementTab />);
    await user.type(screen.getByPlaceholderText(/Cluster maintenance/), 'Down at noon');
    await user.click(button('Warning'));
    await user.click(button('Set announcement'));
    await screen.findByText('Announcement set');
    const state = useUiStore.getState();
    expect(state.announcement).toMatchObject({ message: 'Down at noon', level: 'warning' });
    expect(state.announcementDismissed).toBe(false);
  });

  it('removes the banner when the announcement is cleared', async () => {
    const user = userEvent.setup();
    useUiStore.setState({ announcement: { message: 'Old', level: 'info', updated_at: '2026-01-01T00:00:00Z' } });
    render(<AnnouncementTab />);
    await user.click(button('Clear announcement'));
    await screen.findByText('Announcement cleared');
    expect(useUiStore.getState().announcement).toBeNull();
  });

  it('turns Clear off when the box is empty and nothing is stored', async () => {
    let fetched = false;
    server.use(http.get('/api/announcement', () => { fetched = true; return HttpResponse.json({ announcement: null }); }));
    render(<AnnouncementTab />);
    await waitFor(() => expect(fetched).toBe(true));
    expect(button('Clear announcement')).toBeDisabled();
  });

  it('allows Clear when an announcement is stored', async () => {
    const user = userEvent.setup();
    server.use(http.get('/api/announcement', () => HttpResponse.json({
      announcement: { message: 'Stored', level: 'info', updated_at: '2026-01-01T00:00:00Z' },
    })));
    render(<AnnouncementTab />);
    const box = await screen.findByDisplayValue('Stored');
    await user.clear(box);
    expect(button('Clear announcement')).toBeEnabled();
  });

  it('says plainly, as an error, when saving fails', async () => {
    const user = userEvent.setup();
    server.use(http.put('/api/announcement', () => new HttpResponse(null, { status: 500 })));
    render(<AnnouncementTab />);
    await user.type(screen.getByPlaceholderText(/Cluster maintenance/), 'Down at noon');
    await user.click(button('Set announcement'));
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not save the announcement');
    expect(useUiStore.getState().announcement).toBeNull();
  });

  it('labels the text, capitalises the levels and marks the chosen one', async () => {
    const user = userEvent.setup();
    render(<AnnouncementTab />);
    expect(screen.getByLabelText('Announcement message').tagName).toBe('TEXTAREA');
    const group = screen.getByRole('group', { name: 'Level:' });
    expect(within(group).getAllByRole('button').map(b => b.textContent)).toEqual(['Info', 'Warning', 'Error']);
    expect(within(group).getByRole('button', { name: 'Info' })).toHaveAttribute('aria-pressed', 'true');
    await user.click(within(group).getByRole('button', { name: 'Error' }));
    expect(within(group).getByRole('button', { name: 'Error' })).toHaveAttribute('aria-pressed', 'true');
    expect(within(group).getByRole('button', { name: 'Info' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('previews the banner in the chosen level', async () => {
    const user = userEvent.setup();
    render(<AnnouncementTab />);
    expect(screen.queryByTestId('announcement-preview')).not.toBeInTheDocument();
    await user.type(screen.getByPlaceholderText(/Cluster maintenance/), 'Down at noon');
    await user.click(button('Warning'));
    const preview = screen.getByTestId('announcement-preview');
    expect(preview).toHaveTextContent('Down at noon');
    expect(preview).toHaveClass('text-warning');
  });
});
