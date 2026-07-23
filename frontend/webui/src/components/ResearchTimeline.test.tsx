import { render, screen } from '@testing-library/react';
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
