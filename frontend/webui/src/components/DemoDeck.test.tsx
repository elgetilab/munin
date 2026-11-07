import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../test/msw-server';
import { DemoDeck } from './DemoDeck';

function renderDeck(slide: number, onSlide = vi.fn(), extra: Record<string, unknown> = {}) {
  server.use(http.get('/demo/demo.json', () => HttpResponse.json({ projectId: null, savedResearchChat: null, cards: [], ...extra })));
  render(<DemoDeck slide={slide} onSlide={onSlide} offline={false} onRun={vi.fn()} onOpenChat={vi.fn()} />);
  return onSlide;
}

describe('DemoDeck layout', () => {
  it('keeps the top of a tall slide reachable on a short screen', () => {
    renderDeck(0);
    const main = screen.getByRole('main');
    expect(main).toHaveClass('overflow-y-auto');
    expect(main).not.toHaveClass('justify-center');
    expect(main.firstElementChild).toHaveClass('my-auto');
  });
});

describe('DemoDeck event notes', () => {
  it('makes no claim about the setup when demo.json gives none', async () => {
    renderDeck(0);
    const caption = screen.getByText(/The machine Munin normally runs on/);
    await waitFor(() => expect(caption).toHaveTextContent(/^The machine Munin normally runs on\.$/));
    expect(screen.queryByText(/tonight/i)).not.toBeInTheDocument();
  });

  it('adds the setup note from demo.json', async () => {
    renderDeck(0, vi.fn(), { setupNote: 'It is offline today, so this demo runs on a fallback server.' });
    expect(await screen.findByText(/It is offline today/)).toHaveTextContent(
      'The machine Munin normally runs on. It is offline today, so this demo runs on a fallback server.',
    );
  });

  it('adds the benchmark note from demo.json, and only then', async () => {
    renderDeck(1, vi.fn(), { benchmarkNote: 'This demo uses a different model.' });
    expect(await screen.findByText(/This demo uses a different model\./)).toHaveTextContent(
      /in preparation\. This demo uses a different model\. Citations are always worth checking\.$/,
    );
  });

  it('keeps the benchmark source plain without a note', async () => {
    renderDeck(1);
    const source = screen.getByText(/LitQA2, 199 questions/);
    await waitFor(() => expect(source).toHaveTextContent(/in preparation\. Citations are always worth checking\.$/));
    expect(source).not.toHaveTextContent(/tonight/i);
  });
});

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
