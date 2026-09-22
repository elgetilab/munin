import { act, render, screen, waitFor } from '@testing-library/react';
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
