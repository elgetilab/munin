import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from '../test/msw-server';
import { PlanCard } from './PlanCard';
import type { Plan } from '../lib/types';

function makePlan(overrides: Partial<Plan> = {}): Plan {
  return {
    conversation_id: 'c1',
    items: [
      { id: 'step-1', title: 'Search the corpus', status: 'pending' },
      { id: 'step-2', title: 'Compare methods', status: 'pending' },
    ],
    requires_approval: true,
    approved_at: null,
    approval_mode: 'each',
    created_at: '',
    updated_at: '',
    ...overrides,
  };
}

const AUTO = { approved_at: '2026-01-01T00:00:00Z', approval_mode: 'auto' as const };

// Records every plan request. Each test can make them fail with a status.
function mockPlanApi(status = 200) {
  const calls: Array<{ path: string; body: unknown }> = [];
  const respond = async (path: string, request: Request) => {
    const text = await request.text();
    calls.push({ path, body: text ? JSON.parse(text) : null });
    return HttpResponse.json({ error: { message: 'kaboom' } }, { status });
  };
  server.use(
    http.post('/api/chats/:id/plan/approve', ({ request }) => respond('approve', request)),
    http.post('/api/chats/:id/plan/reject', ({ request }) => respond('reject', request)),
    http.patch('/api/chats/:id/plan', ({ request }) => respond('edit', request)),
  );
  return calls;
}

describe('PlanCard revoke', () => {
  it('sends no message and only changes the setting', async () => {
    const calls = mockPlanApi();
    const onAfterApprove = vi.fn();
    const onAfterReject = vi.fn();
    const onAfterEdit = vi.fn();
    render(
      <PlanCard
        plan={makePlan(AUTO)}
        conversationId="c1"
        onAfterApprove={onAfterApprove}
        onAfterReject={onAfterReject}
        onAfterEdit={onAfterEdit}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'revoke' }));
    await screen.findByText(/asking before each step/i);

    expect(calls).toEqual([{ path: 'approve', body: { mode: 'each' } }]);
    expect(onAfterEdit).not.toHaveBeenCalled();
    expect(onAfterApprove).not.toHaveBeenCalled();
    expect(onAfterReject).not.toHaveBeenCalled();
  });

  it('drops the auto-approve footer and indicator once it succeeds', async () => {
    mockPlanApi();
    render(<PlanCard plan={makePlan(AUTO)} conversationId="c1" />);
    expect(screen.getByText(/auto-approve on/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'revoke' }));
    await screen.findByText(/asking before each step/i);

    expect(screen.queryByRole('button', { name: 'revoke' })).not.toBeInTheDocument();
    expect(screen.queryByText(/auto-approve on/)).not.toBeInTheDocument();
  });
});

describe('PlanCard after an action', () => {
  it('Approve hides the buttons, shows Approved and calls back once', async () => {
    mockPlanApi();
    const onAfterApprove = vi.fn();
    render(<PlanCard plan={makePlan()} conversationId="c1" onAfterApprove={onAfterApprove} />);

    fireEvent.click(screen.getByRole('button', { name: 'Approve plan' }));
    await screen.findByText('Approved');

    expect(onAfterApprove).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('button', { name: 'Approve plan' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Reject plan' })).not.toBeInTheDocument();
    expect(screen.queryByText(/approval required/)).not.toBeInTheDocument();
  });

  it('Approve all switches to the auto footer, from which it can be revoked', async () => {
    const calls = mockPlanApi();
    render(<PlanCard plan={makePlan()} conversationId="c1" />);

    fireEvent.click(screen.getByRole('button', { name: 'Approve all subsequent calls' }));

    expect(await screen.findByRole('button', { name: 'revoke' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Approve plan' })).not.toBeInTheDocument();
    expect(calls[0]).toEqual({ path: 'approve', body: { mode: 'auto' } });
  });

  it('Reject says what to do next and focuses the composer', async () => {
    mockPlanApi();
    const onAfterReject = vi.fn();
    render(
      <>
        <textarea id="composer-textarea" aria-label="composer" />
        <PlanCard plan={makePlan()} conversationId="c1" onAfterReject={onAfterReject} />
      </>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Reject plan' }));
    await screen.findByText('Rejected. Tell Munin what to change.');

    expect(onAfterReject).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('button', { name: 'Approve plan' })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText('composer')).toHaveFocus());
  });

  it('a failed Approve keeps the buttons, so it can be tried again', async () => {
    mockPlanApi(500);
    const onAfterApprove = vi.fn();
    render(<PlanCard plan={makePlan()} conversationId="c1" onAfterApprove={onAfterApprove} />);

    fireEvent.click(screen.getByRole('button', { name: 'Approve plan' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Approve plan' })).toBeEnabled());

    expect(onAfterApprove).not.toHaveBeenCalled();
    expect(screen.queryByText('Approved')).not.toBeInTheDocument();
  });
});
