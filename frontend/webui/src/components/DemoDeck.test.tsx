import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../test/msw-server';
import { DemoDeck } from './DemoDeck';

function renderDeck(slide: number, onSlide = vi.fn()) {
  server.use(http.get('/demo/demo.json', () => HttpResponse.json({ projectId: null, savedResearchChat: null, cards: [] })));
  render(<DemoDeck slide={slide} onSlide={onSlide} offline={false} onRun={vi.fn()} onOpenChat={vi.fn()} />);
  return onSlide;
}

describe('DemoDeck outlook', () => {
  it('reaches the outlook from the live demo', async () => {
    const onSlide = renderDeck(2);
    await userEvent.click(screen.getByTestId('demo-next'));
    expect(onSlide).toHaveBeenCalledWith(3);
  });

  it('is the last slide, and shows the plans', () => {
    renderDeck(3);
    expect(screen.getByRole('heading', { name: 'What comes next' })).toBeInTheDocument();
    for (const plan of ['HypMol Knowledge Graph', 'XWiki in Chemnitz', 'A more capable sandbox', 'Cluster jobs from chat', 'Shared team spaces', 'More interactivity']) {
      expect(screen.getByText(plan)).toBeInTheDocument();
    }
    expect(screen.getByText('4 / 4')).toBeInTheDocument();
    expect(screen.getByTestId('demo-next')).toBeDisabled();
  });
});
