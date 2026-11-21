import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../test/msw-server';
import { CreditMunin } from './CreditMunin';

const SUMMARY = {
  from: '2026-09-01', to: '2026-11-21',
  models: [
    { model: 'qwen3.8-27b', display: 'Qwen3.8-27B', checkpoint: 'cyankiwi/Qwen3.8-27B-AWQ-INT4',
      revision: '63768c10df38c0395e12', vllm_version: '0.20.2rc1', hosted_by: null,
      first_use: '2026-09-01T10:00:00+00:00', last_use: '2026-10-01T10:00:00+00:00',
      requests: 12, inferred_requests: 12, conversations: 3, deep_research_jobs: 0, tokens_total: 34000 },
    { model: 'claude-haiku-5-5', display: 'Claude Haiku 5.5', hosted_by: 'Anthropic API',
      first_use: '2026-11-01T10:00:00+00:00', last_use: '2026-11-01T10:00:00+00:00',
      requests: 1, inferred_requests: 1, conversations: 1, deep_research_jobs: 0, tokens_total: 900 },
  ],
  totals: { requests: 13, conversations: 4, deep_research_jobs: 0, tokens_total: 34900 },
  statement: { methods: 'We used Munin ... to [PURPOSE].', acknowledgment: 'We thank the Elgeti Lab.',
               references_apa: ['Fischer et al. (2026).'], bibtex: '@misc{munin2026}' },
  notes: [],
};

describe('CreditMunin', () => {
  it('loads usage for the chosen dates and shows each model with its statement', async () => {
    let asked = '';
    server.use(http.get('/api/usage/me/summary', ({ request }) => {
      asked = new URL(request.url).search;
      return HttpResponse.json(SUMMARY);
    }));
    const user = userEvent.setup();
    render(<CreditMunin />);
    await user.type(screen.getByLabelText('From'), '2026-09-01');
    await user.click(screen.getByRole('button', { name: 'Show my usage' }));

    expect(await screen.findByText('Qwen3.8-27B')).toBeInTheDocument();
    expect(asked).toBe('?from=2026-09-01');
    expect(screen.getByText(/cyankiwi\/Qwen3.8-27B-AWQ-INT4 · rev 63768c10df38 · vLLM 0.20.2rc1/)).toBeInTheDocument();
    expect(screen.getByText('hosted, via the Anthropic API')).toBeInTheDocument();
    expect(screen.getByText(/taken from the deployment history/)).toBeInTheDocument();
    expect(screen.getByText('We used Munin ... to [PURPOSE].')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Copy BibTeX' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Download CSV' }).getAttribute('href'))
      .toBe('/api/usage/me/summary?format=csv&from=2026-09-01');
  });

  it('says so when the range has no model use', async () => {
    server.use(http.get('/api/usage/me/summary', () => HttpResponse.json({ ...SUMMARY, models: [], statement: {} })));
    const user = userEvent.setup();
    render(<CreditMunin />);
    await user.click(screen.getByRole('button', { name: 'Show my usage' }));
    expect(await screen.findByText('No model use in this range.')).toBeInTheDocument();
  });

  it('reports a failed load with a retry', async () => {
    server.use(http.get('/api/usage/me/summary', () => new HttpResponse(null, { status: 500 })));
    const user = userEvent.setup();
    render(<CreditMunin />);
    await user.click(screen.getByRole('button', { name: 'Show my usage' }));
    expect(await screen.findByText('Could not load your usage.')).toBeInTheDocument();
  });
});
