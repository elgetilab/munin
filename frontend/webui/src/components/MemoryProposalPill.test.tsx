import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from '../test/msw-server';
import { MemoryProposalPill } from './MemoryProposalPill';
import { useUiStore } from '../stores/uiStore';
import type { MemoryProposal } from '../lib/types';

const PROPOSAL: MemoryProposal = {
  id: 'p-1',
  key: 'user_role',
  value: 'postdoc in Smith Lab',
  reason: 'stable identity fact',
};

describe('MemoryProposalPill', () => {
  beforeEach(() => useUiStore.getState().setToast(null));

  it('renders key, value, and reason', () => {
    render(<MemoryProposalPill proposal={PROPOSAL} onDismiss={() => {}} />);
    expect(screen.getByText(/postdoc in Smith Lab/)).toBeInTheDocument();
    expect(screen.getByText(/User role/)).toBeInTheDocument();
    expect(screen.queryByText(/user_role/)).not.toBeInTheDocument();
    expect(screen.getByText(/stable identity fact/)).toBeInTheDocument();
  });

  it('POSTs accept and calls onDismiss with the proposal id', async () => {
    let hit: string | null = null;
    server.use(
      http.post('/api/memories/proposed/:id/accept', ({ params }) => {
        hit = params.id as string;
        return HttpResponse.json({ accepted: { key: 'user_role' } });
      }),
    );
    const dismissed: string[] = [];
    render(
      <MemoryProposalPill
        proposal={PROPOSAL}
        onDismiss={(id) => dismissed.push(id)}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /accept memory/i }));
    await waitFor(() => expect(dismissed).toEqual(['p-1']));
    expect(hit).toBe('p-1');
  });

  it('POSTs reject and calls onDismiss', async () => {
    let hit: string | null = null;
    server.use(
      http.post('/api/memories/proposed/:id/reject', ({ params }) => {
        hit = params.id as string;
        return HttpResponse.json({ rejected: true });
      }),
    );
    const dismissed: string[] = [];
    render(
      <MemoryProposalPill
        proposal={PROPOSAL}
        onDismiss={(id) => dismissed.push(id)}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /reject memory/i }));
    await waitFor(() => expect(dismissed).toEqual(['p-1']));
    expect(hit).toBe('p-1');
  });

  it('keeps the pill and says so when Save fails, and Save can be retried', async () => {
    let attempts = 0;
    server.use(
      http.post('/api/memories/proposed/:id/accept', () => {
        attempts++;
        return attempts === 1
          ? HttpResponse.json({ error: { message: 'kaboom' } }, { status: 500 })
          : HttpResponse.json({ accepted: { key: 'user_role' } });
      }),
    );
    const dismissed: string[] = [];
    render(
      <MemoryProposalPill
        proposal={PROPOSAL}
        onDismiss={(id) => dismissed.push(id)}
      />,
    );
    const save = screen.getByRole('button', { name: /accept memory/i });
    fireEvent.click(save);
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not save. Try again.');
    expect(dismissed).toEqual([]);
    expect(useUiStore.getState().toast).toBeNull();

    await waitFor(() => expect(save).toBeEnabled());
    fireEvent.click(save);
    await waitFor(() => expect(dismissed).toEqual(['p-1']));
    expect(useUiStore.getState().toast).toBe('Saved to memory');
  });

  it('still removes the pill when Dismiss fails', async () => {
    server.use(
      http.post('/api/memories/proposed/:id/reject', () =>
        HttpResponse.json({ error: { message: 'kaboom' } }, { status: 500 }),
      ),
    );
    const dismissed: string[] = [];
    render(
      <MemoryProposalPill
        proposal={PROPOSAL}
        onDismiss={(id) => dismissed.push(id)}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /reject memory/i }));
    // Best-effort; the proposal re-surfaces on next reload.
    await waitFor(() => expect(dismissed).toEqual(['p-1']));
  });
});

describe('MemoryProposalPill look', () => {
  it('matches the other cards: Save primary, Dismiss secondary, both with a touch height', () => {
    render(<MemoryProposalPill proposal={PROPOSAL} onDismiss={() => {}} />);
    const save = screen.getByRole('button', { name: 'Accept memory' });
    const dismiss = screen.getByRole('button', { name: 'Reject memory' });
    expect(save.className).toContain('bg-accent');
    expect(dismiss.className).not.toContain('bg-accent');
    expect(save.className).toContain('pointer-coarse:min-h-9');
    expect(dismiss.className).toContain('pointer-coarse:min-h-9');
  });
});
