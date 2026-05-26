import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from '../test/msw-server';
import { MemoryProposalPill } from './MemoryProposalPill';
import type { MemoryProposal } from '../lib/types';

const PROPOSAL: MemoryProposal = {
  id: 'p-1',
  key: 'user_role',
  value: 'postdoc in Smith Lab',
  reason: 'stable identity fact',
};

describe('MemoryProposalPill', () => {
  it('renders key, value, and reason', () => {
    render(<MemoryProposalPill proposal={PROPOSAL} onDismiss={() => {}} />);
    expect(screen.getByText(/postdoc in Smith Lab/)).toBeInTheDocument();
    expect(screen.getByText(/user_role/)).toBeInTheDocument();
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

  it('removes the pill optimistically even when the API errors', async () => {
    server.use(
      http.post('/api/memories/proposed/:id/accept', () =>
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
    fireEvent.click(screen.getByRole('button', { name: /accept memory/i }));
    // Still dismisses locally — best-effort feature; proposal re-surfaces
    // on next reload if the server side actually failed.
    await waitFor(() => expect(dismissed).toEqual(['p-1']));
  });
});
