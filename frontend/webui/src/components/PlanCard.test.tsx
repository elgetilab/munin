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

    fireEvent.click(screen.getByRole('button', { name: 'Revoke' }));
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

    fireEvent.click(screen.getByRole('button', { name: 'Revoke' }));
    await screen.findByText(/asking before each step/i);

    expect(screen.queryByRole('button', { name: 'Revoke' })).not.toBeInTheDocument();
    expect(screen.queryByText(/auto-approve on/)).not.toBeInTheDocument();
  });
});

describe('PlanCard failures', () => {
  it.each([
    ['Approve plan', 'Could not approve the plan. Try again.'],
    ['Reject plan', 'Could not reject the plan. Try again.'],
  ])('%s says it failed, and the next click clears the message', async (button, message) => {
    mockPlanApi(500);
    render(<PlanCard plan={makePlan()} conversationId="c1" />);

    fireEvent.click(screen.getByRole('button', { name: button }));
    expect(await screen.findByRole('alert')).toHaveTextContent(message);

    fireEvent.click(screen.getByRole('button', { name: button }));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    await screen.findByRole('alert');
  });

  it('a failed revoke says so and keeps the auto footer', async () => {
    mockPlanApi(500);
    render(<PlanCard plan={makePlan(AUTO)} conversationId="c1" />);
    fireEvent.click(screen.getByRole('button', { name: 'Revoke' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/could not change/i);
    expect(screen.getByRole('button', { name: 'Revoke' })).toBeInTheDocument();
  });

  it('a failed save keeps the editor and the edits, and says so', async () => {
    mockPlanApi(500);
    const onAfterEdit = vi.fn();
    render(<PlanCard plan={makePlan()} conversationId="c1" onAfterEdit={onAfterEdit} />);
    fireEvent.click(screen.getByRole('button', { name: 'Edit plan' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Step 1 title' }), { target: { value: 'Changed' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save & approve' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not save the plan. Try again.');
    expect(screen.getByRole('textbox', { name: 'Step 1 title' })).toHaveValue('Changed');
    expect(onAfterEdit).not.toHaveBeenCalled();
  });
});

describe('PlanCard editor', () => {
  const openEditor = () => fireEvent.click(screen.getByRole('button', { name: 'Edit plan' }));
  const save = () => screen.getByRole('button', { name: 'Save & approve' });

  it('shows plain numbers, not raw ids, in both modes', () => {
    render(<PlanCard plan={makePlan()} conversationId="c1" />);
    expect(screen.queryByText(/step-1/)).not.toBeInTheDocument();
    expect(screen.getByText('1.')).toBeInTheDocument();
    openEditor();
    expect(screen.queryByText(/step-1/)).not.toBeInTheDocument();
    expect(screen.getByText('1.')).toBeInTheDocument();
  });

  it('adds a step with an id that cannot clash after a removal', async () => {
    const calls = mockPlanApi();
    render(<PlanCard plan={makePlan()} conversationId="c1" />);
    openEditor();
    fireEvent.click(screen.getByRole('button', { name: 'Remove step 1' }));
    fireEvent.click(screen.getByRole('button', { name: /add step/i }));
    const added = screen.getByRole('textbox', { name: 'Step 2 title' });
    expect(added).toHaveFocus();
    fireEvent.change(added, { target: { value: 'New step' } });
    fireEvent.click(save());
    await screen.findByText('Approved');

    const { items } = calls[0].body as { items: Array<{ id: string; title: string; status: string }> };
    expect(items.map(i => i.title)).toEqual(['Compare methods', 'New step']);
    expect(items[1].status).toBe('pending');
    expect(items[1].id).toBeTruthy();
    expect(new Set(items.map(i => i.id)).size).toBe(2);
  });

  it('shows the saved steps afterwards, not the snapshot from before', async () => {
    mockPlanApi();
    render(<PlanCard plan={makePlan()} conversationId="c1" />);
    openEditor();
    fireEvent.change(screen.getByRole('textbox', { name: 'Step 1 title' }), { target: { value: 'Edited title' } });
    fireEvent.click(save());
    await screen.findByText('Approved');
    expect(screen.getByText('Edited title')).toBeInTheDocument();
    expect(screen.queryByText('Search the corpus')).not.toBeInTheDocument();
  });

  it('disables Save while every title is blank', () => {
    render(<PlanCard plan={makePlan()} conversationId="c1" />);
    openEditor();
    fireEvent.change(screen.getByRole('textbox', { name: 'Step 1 title' }), { target: { value: ' ' } });
    expect(save()).toBeEnabled();
    expect(screen.getByText(/left out when you save/)).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: 'Step 2 title' }), { target: { value: '' } });
    expect(save()).toBeDisabled();
  });

  it('Edit starts from the current plan, even if the prop changed since mount', () => {
    const { rerender } = render(<PlanCard plan={makePlan()} conversationId="c1" />);
    rerender(
      <PlanCard
        plan={makePlan({ items: [{ id: 'step-1', title: 'Newer title', status: 'done' }] })}
        conversationId="c1"
      />,
    );
    openEditor();
    expect(screen.getByRole('textbox', { name: 'Step 1 title' })).toHaveValue('Newer title');
    expect(screen.queryByRole('textbox', { name: 'Step 2 title' })).not.toBeInTheDocument();
  });

  it('Cancel discards the edits', () => {
    render(<PlanCard plan={makePlan()} conversationId="c1" />);
    openEditor();
    fireEvent.change(screen.getByRole('textbox', { name: 'Step 1 title' }), { target: { value: 'Changed' } });
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.getByText('Search the corpus')).toBeInTheDocument();
    openEditor();
    expect(screen.getByRole('textbox', { name: 'Step 1 title' })).toHaveValue('Search the corpus');
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

    expect(await screen.findByRole('button', { name: 'Revoke' })).toBeInTheDocument();
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

describe('PlanCard look', () => {
  it('marks each step with a line icon of one width, and keeps the status in its name', () => {
    const plan = makePlan({
      items: [
        { id: 'a', title: 'One', status: 'pending' },
        { id: 'b', title: 'Two', status: 'in_progress' },
        { id: 'c', title: 'Three', status: 'done' },
        { id: 'd', title: 'Four', status: 'cancelled' },
      ],
    });
    render(<PlanCard plan={plan} />);
    for (const name of ['pending: One', 'in progress: Two', 'done: Three', 'cancelled: Four']) {
      const item = screen.getByRole('listitem', { name });
      const icon = item.querySelector('svg');
      expect(icon).not.toBeNull();
      expect(icon!.getAttribute('width')).toBe('14');
      expect(item.textContent).not.toMatch(/\p{Extended_Pictographic}/u);
    }
  });

  it('gives Approve the primary look, the others the secondary one, and all a touch height', () => {
    render(<PlanCard plan={makePlan()} conversationId="c1" />);
    const approve = screen.getByRole('button', { name: 'Approve plan' });
    expect(approve.className).toContain('bg-accent');
    for (const name of ['Approve all subsequent calls', 'Edit plan', 'Reject plan']) {
      const button = screen.getByRole('button', { name });
      expect(button.className).not.toContain('bg-accent');
      expect(button.className).toContain('border-border');
    }
    for (const button of screen.getAllByRole('button')) {
      expect(button.className).toContain('pointer-coarse:min-h-9');
    }
  });

  it('shows Revoke as a quiet button in sentence case', () => {
    render(<PlanCard plan={makePlan(AUTO)} conversationId="c1" />);
    const revoke = screen.getByRole('button', { name: 'Revoke' });
    expect(revoke.className).not.toContain('underline');
    expect(revoke.className).toContain('pointer-coarse:min-h-9');
  });
});
