import { render, screen } from '@testing-library/react';
import { ResearchTimeline } from './ResearchTimeline';
import type { DeepResearchJob, ResearchEvent } from '../stores/deepResearchStore';

// Regression guard for the "Deep Research bubble blocks the entire chat" bug:
// a finished job carries 50+ findings and as many activity rows, and the card
// renders as a flex sibling of the (min-h-0) message list. The card MUST bound
// its own height and scroll its body internally, or its intrinsic height wins
// the flex fight and hides the whole conversation. jsdom does no layout, so we
// assert the structural contract that produces the bound rather than measuring
// pixels: a capped root + an internally-scrolling body that holds the lists.

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

describe('ResearchTimeline layout containment', () => {
  it('bounds the card height so it cannot swallow the conversation', () => {
    render(<ResearchTimeline job={bigJob(52, 48)} />);
    const card = screen.getByTestId('research-timeline');
    // The root must carry an explicit max-height cap and be a flex column.
    expect(card.className).toMatch(/max-h-\[/);
    expect(card.className).toContain('flex');
    expect(card.className).toContain('flex-col');
  });

  it('renders every finding inside an internally-scrolling body', () => {
    render(<ResearchTimeline job={bigJob(52, 48)} />);
    const notes = screen.getAllByTestId('research-note');
    expect(notes).toHaveLength(52);
    // Each note must live inside an overflow-y-auto ancestor (the scroll body),
    // itself inside the capped card - that is what keeps the card bounded.
    const scrollBody = notes[0].closest('.overflow-y-auto');
    expect(scrollBody).not.toBeNull();
    expect(scrollBody!.className).toContain('min-h-0');
    expect(scrollBody!.closest('[data-testid="research-timeline"]')).not.toBeNull();
  });

  it('keeps the report link outside the scroll body so it stays pinned', () => {
    render(<ResearchTimeline job={bigJob(52, 48)} />);
    const link = screen.getByTestId('research-report-link');
    // The footer must NOT be inside the scrollable body.
    expect(link.closest('.overflow-y-auto')).toBeNull();
    expect(link.closest('[data-testid="research-timeline"]')).not.toBeNull();
  });
});
