import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../test/msw-server';
import { MOCK_ADMIN_ACTIVITY } from '../test/msw-handlers';
import { AdminPanel } from './AdminPanel';
import { _resetUiStoreForTests } from '../stores/uiStore';

// Characterization tests written before the fetch-on-mount restructure.
// The last one pins a behaviour the restructure FIXES rather than preserves,
// and is expected to fail against the old implementation: see its comment.

describe('AdminPanel', () => {
  beforeEach(() => _resetUiStoreForTests());

  it('shows the summary cards once activity and usage arrive', async () => {
    render(<AdminPanel />);
    await waitFor(() => expect(screen.getByText('Online now')).toBeInTheDocument());
    expect(screen.getByText('Active today')).toBeInTheDocument();
    expect(screen.getByText('Users this month')).toBeInTheDocument();
    expect(screen.getByText('Tokens this month')).toBeInTheDocument();
    expect(screen.getByText('Admin Dashboard')).toBeInTheDocument();
  });

  it('surfaces a failed load', async () => {
    server.use(http.get('/api/usage/admin', () => new HttpResponse(null, { status: 500 })));
    render(<AdminPanel />);
    await waitFor(() => expect(screen.getByText(/failed/i)).toBeInTheDocument());
    expect(screen.queryByText('Online now')).not.toBeInTheDocument();
  });

  it('retries a failed load', async () => {
    const user = userEvent.setup();
    let fail = true;
    server.use(http.get('/api/usage/admin/activity', () => (
      fail ? new HttpResponse(null, { status: 500 }) : HttpResponse.json(MOCK_ADMIN_ACTIVITY)
    )));

    render(<AdminPanel />);
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());

    fail = false;
    await user.click(screen.getByRole('button', { name: 'Retry' }));

    await waitFor(() => expect(screen.getByText('Online now')).toBeInTheDocument());
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('keeps the last data, with a notice, when the 30s refresh fails', async () => {
    let calls = 0;
    server.use(http.get('/api/usage/admin/activity', () => {
      calls += 1;
      return calls > 1 ? new HttpResponse(null, { status: 500 }) : HttpResponse.json(MOCK_ADMIN_ACTIVITY);
    }));

    vi.useFakeTimers();
    try {
      render(<AdminPanel />);
      await vi.waitFor(() => expect(screen.getByText('All tracked users', { exact: false })).toBeInTheDocument());

      await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
      await vi.waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/could not refresh/i));

      expect(screen.getByText('All tracked users', { exact: false })).toBeInTheDocument();
      expect(screen.getByText('Online now')).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it('scrolls a newly chosen tab into view inside the tab row', async () => {
    const user = userEvent.setup();
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
    try {
      render(<AdminPanel />);
      const groups = screen.getByRole('button', { name: 'Groups' });
      expect(groups.closest('.overflow-x-auto')).not.toBeNull();

      await user.click(groups);

      expect(scrollIntoView.mock.contexts).toContain(groups);
    } finally {
      delete (Element.prototype as Partial<Element>).scrollIntoView;
    }
  });

  // The panel refreshes activity and usage on a 30s timer. The old
  // implementation raised its loading flag on every one of those refreshes,
  // and the summary cards are rendered behind `!loading`, so the dashboard
  // blanked and re-appeared every 30 seconds. After the restructure the
  // loading flag only ever turns off, so a refresh updates the numbers in
  // place. Fake timers make this deterministic rather than timing-dependent.
  it('does not blank the summary cards on the 30s auto-refresh', async () => {
    // The refresh request is left hanging, which is what makes this
    // discriminating: the cards' visibility is then decided purely by what
    // the component did with its loading flag when the timer fired, not by
    // how fast the response came back.
    let calls = 0;
    server.use(http.get('/api/usage/admin/activity', async () => {
      calls += 1;
      if (calls > 1) await new Promise(() => { /* never resolves */ });
      return HttpResponse.json(MOCK_ADMIN_ACTIVITY);
    }));

    vi.useFakeTimers();
    try {
      render(<AdminPanel />);
      await vi.waitFor(() => expect(screen.getByText('Online now')).toBeInTheDocument());

      // act() so the state the timer sets is flushed to the DOM before the
      // assertions below; without it this test cannot see a blanked card.
      await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
      expect(calls).toBe(2);

      expect(screen.getByText('Online now')).toBeInTheDocument();
      expect(screen.getByText('Tokens this month')).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});
