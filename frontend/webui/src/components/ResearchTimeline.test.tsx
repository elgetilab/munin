import { fireEvent, render, screen, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from '../test/msw-server';
import { ResearchTimeline } from './ResearchTimeline';
import type { DeepResearchJob, ResearchEvent } from '../stores/deepResearchStore';

// The timeline renders as the last item of the message transcript (see
// MessageList) so it scrolls WITH the conversation. It must therefore flow at
// its natural height and NOT introduce its own internal scrollbar or height
// cap - a nested scroll inside the chat area is exactly the "sits awkwardly on
// top of the chat" behaviour we moved away from. It must also render the full
// findings list (a finished job carries 50+) rather than truncating.

function bigJob(noteCount: number, rowCount: number): DeepResearchJob {
  const events: ResearchEvent[] = [
    { t: 0, type: 'plan', items: [{ id: 'q1', text: 'Sub-question one', status: 'resolved' }] },
  ];
  for (let i = 0; i < rowCount; i++) {
    events.push({ t: i + 1, type: 'tool_call', id: `c${i}`, name: 'search', arguments: { query: `q${i}` } });
    events.push({ t: i + 1, type: 'tool_result', id: `c${i}`, summary: 'ok', outcome: 'done' });
  }
  for (let i = 0; i < noteCount; i++) {
    events.push({ t: 1000 + i, type: 'note', claim: `Finding number ${i}`, tier: 'web' });
  }
  events.push({ t: 9998, type: 'artifact', artifact_id: 'art_1' });
  events.push({ t: 9999, type: 'done' });
  return {
    id: 'dr_test', conversationId: 'conv_1', question: 'Q', status: 'done',
    events, artifactId: 'art_1', startedAt: 0,
  };
}

function endedJob(status: DeepResearchJob['status'], extra: ResearchEvent[] = []): DeepResearchJob {
  return {
    id: 'dr_test', conversationId: 'conv_1', question: 'Q', status, startedAt: 0,
    events: [
      { t: 0, type: 'plan', items: [{ id: 'q1', text: 'Half done', status: 'in_progress' }] },
      { t: 1, type: 'tool_call', id: 'c1', name: 'search', arguments: { query: 'lipids' } },
      { t: 2, type: 'note', claim: 'A finding' },
      ...extra,
    ],
  };
}

describe('ResearchTimeline ended states', () => {
  it('says a cancelled job was cancelled, not finished', () => {
    render(<ResearchTimeline job={endedJob('cancelled')} />);
    expect(screen.getByTestId('research-ended')).toHaveTextContent(
      'Research cancelled. The findings above were gathered before it stopped.',
    );
    expect(screen.queryByText('Research finished.')).not.toBeInTheDocument();
  });

  it('says findings are partial and how to go on when a job fails', () => {
    render(<ResearchTimeline job={endedJob('error')} />);
    const footer = screen.getByTestId('research-ended');
    expect(footer).toHaveTextContent('Research failed.');
    expect(footer).toHaveTextContent('The findings above are partial.');
    expect(footer).toHaveTextContent('from the + menu');
  });

  it('shows the reason when the event log has one, in plain words', () => {
    render(<ResearchTimeline job={endedJob('error', [{ t: 3, type: 'error', message: 'vLLM unavailable' }])} />);
    expect(screen.getByTestId('research-ended')).toHaveTextContent('The model is unavailable right now.');
    expect(screen.getByTestId('research-ended')).not.toHaveTextContent('vLLM');
  });

  it('does not claim partial findings when there are none', () => {
    render(<ResearchTimeline job={{ ...endedJob('cancelled'), events: [] }} />);
    expect(screen.getByTestId('research-ended').textContent).toBe('Research cancelled.');
  });

  it('shows no running marks once the job has ended', () => {
    render(<ResearchTimeline job={endedJob('cancelled')} />);
    expect(screen.getByTestId('research-tool-row')).not.toHaveTextContent('…');
    const item = screen.getByTestId('research-plan-item');
    expect(item).not.toHaveTextContent('◐');
    expect(within(item).getByText(/Not finished/)).toBeInTheDocument();
  });

  it('keeps the running marks and says the status while active', () => {
    render(<ResearchTimeline job={endedJob('running')} />);
    expect(screen.getByTestId('research-tool-row')).toHaveTextContent('…');
    const item = screen.getByTestId('research-plan-item');
    expect(item).toHaveTextContent('◐');
    expect(within(item).getByText(/In progress/)).toBeInTheDocument();
  });
});

describe('ResearchTimeline cancel', () => {
  it('shows it is cancelling and blocks a second click', async () => {
    let calls = 0;
    server.use(http.post('/api/research/cancel/:id', () => { calls++; return HttpResponse.json({}); }));
    render(<ResearchTimeline job={endedJob('running')} />);
    const cancel = screen.getByTestId('research-cancel');

    fireEvent.click(cancel);
    expect(cancel).toHaveTextContent('Cancelling…');
    expect(cancel).toBeDisabled();
    fireEvent.click(cancel);
    await vi.waitFor(() => expect(calls).toBe(1));
    expect(cancel).toHaveTextContent('Cancelling…');
  });

  it('the next job in the same chat can be cancelled too', async () => {
    server.use(http.post('/api/research/cancel/:id', () => HttpResponse.json({ ok: true })));
    const { rerender } = render(<ResearchTimeline job={endedJob('running')} />);
    fireEvent.click(screen.getByTestId('research-cancel'));
    expect(screen.getByTestId('research-cancel')).toBeDisabled();

    rerender(<ResearchTimeline job={{ ...endedJob('running'), id: 'dr_next' }} />);

    expect(screen.getByTestId('research-cancel')).toBeEnabled();
    expect(screen.getByTestId('research-cancel')).not.toHaveTextContent('Cancelling');
  });

  it('says so when the cancel fails, and it can be tried again', async () => {
    server.use(http.post('/api/research/cancel/:id', () => HttpResponse.json({}, { status: 500 })));
    render(<ResearchTimeline job={endedJob('running')} />);
    const cancel = screen.getByTestId('research-cancel');

    fireEvent.click(cancel);
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not cancel. Try again.');
    expect(cancel).toBeEnabled();
    expect(cancel).toHaveTextContent('Cancel');
  });
});

describe('ResearchTimeline', () => {
  it('renders the full findings list without truncating', () => {
    render(<ResearchTimeline job={bigJob(52, 48)} />);
    expect(screen.getAllByTestId('research-note')).toHaveLength(52);
    expect(screen.getByTestId('research-report-link')).toBeTruthy();
  });

  it('flows at natural height with no internal scroll or height cap', () => {
    const { container } = render(<ResearchTimeline job={bigJob(52, 48)} />);
    const card = screen.getByTestId('research-timeline');
    // No self-imposed max-height, and no nested scroll region: the surrounding
    // transcript owns the scrolling.
    expect(card.className).not.toMatch(/max-h-\[/);
    expect(container.querySelector('.overflow-y-auto')).toBeNull();
  });
});
