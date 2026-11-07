import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../../test/msw-server';
import { MetricsTab } from './MetricsTab';
import { formatMetricValue, rateWindowFor, seriesToRows } from '../../lib/metricsSeries';
import { cloneElement } from 'react';
import type { ReactElement } from 'react';

// jsdom measures every element as 0 by 0, so the real ResponsiveContainer draws
// nothing. A fixed size lets one test look at the chart itself.
vi.mock('recharts', async importOriginal => {
  const actual = await importOriginal<typeof import('recharts')>();
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactElement<{ width?: number; height?: number }> }) =>
      cloneElement(children, { width: 400, height: 200 }),
  };
});

/**
 * MetricsTab tests cover the pure transforms (seriesToRows,
 * rateWindowFor) and an end-to-end render that mocks one
 * /api/admin/metrics/query_range response per panel.
 *
 * recharts uses a ResponsiveContainer that measures the parent's
 * dimensions; in jsdom that's always 0. We don't try to assert on
 * the rendered chart pixels -- the chart just renders an SVG with
 * no data points and that's fine. What we DO assert is:
 *   - The panel titles all appear.
 *   - The fetch goes to the right URL with the right body shape.
 *   - "No data in this window" shows when the response has no series.
 */


describe('rateWindowFor', () => {
  it('returns 5m for short ranges, 1h for medium, 1d for very long', () => {
    expect(rateWindowFor(60)).toBe('5m');
    expect(rateWindowFor(360)).toBe('15m');
    expect(rateWindowFor(1440)).toBe('1h');
    expect(rateWindowFor(10080)).toBe('6h');
    expect(rateWindowFor(43200)).toBe('1d');
  });
});

describe('formatMetricValue', () => {
  // Under 18 per hour is under 0.005/s, which toFixed(2) showed as 0.00.
  it('keeps low traffic visible', () => {
    expect(formatMetricValue(0.0042)).toBe('0.0042');
    expect(formatMetricValue(0.00049)).toBe('0.00049');
  });

  it('shows ordinary values without padding, and large ones compactly', () => {
    expect(formatMetricValue(0)).toBe('0');
    expect(formatMetricValue(8)).toBe('8');
    expect(formatMetricValue(1.5)).toBe('1.5');
    expect(formatMetricValue(99.6)).toBe('100');
    expect(formatMetricValue(250)).toBe('250');
    expect(formatMetricValue(1500)).toBe('1.5k');
  });
});

describe('seriesToRows', () => {
  it('groups multi-series by timestamp into recharts rows', () => {
    const series = [
      {
        metric: { purpose: 'chat_turn' },
        values: [
          [1700000000, '1'],
          [1700000015, '2'],
        ] as Array<[number, string]>,
      },
      {
        metric: { purpose: 'title' },
        values: [
          [1700000000, '0.1'],
          [1700000015, '0.2'],
        ] as Array<[number, string]>,
      },
    ];
    const { rows, names } = seriesToRows(series, 'purpose');
    expect(names).toEqual(['chat_turn', 'title']);
    expect(rows).toHaveLength(2);
    expect(rows[0].chat_turn).toBe(1);
    expect(rows[0].title).toBe(0.1);
    expect(rows[1].chat_turn).toBe(2);
    expect(rows[1].title).toBe(0.2);
  });

  it('fills missing values with null, not 0', () => {
    const series = [
      {
        metric: { purpose: 'a' },
        values: [
          [1, '1'],
          [3, '3'],
        ] as Array<[number, string]>,
      },
      {
        metric: { purpose: 'b' },
        values: [
          [2, '2'],
        ] as Array<[number, string]>,
      },
    ];
    const { rows } = seriesToRows(series, 'purpose');
    // 3 timestamps, each row has a/b that may be null
    expect(rows).toHaveLength(3);
    // At ts=1 b should be null; at ts=2 a should be null
    expect(rows[0].a).toBe(1);
    expect(rows[0].b).toBe(null);
    expect(rows[1].a).toBe(null);
    expect(rows[1].b).toBe(2);
    expect(rows[2].a).toBe(3);
    expect(rows[2].b).toBe(null);
  });

  it('parses non-finite values as null', () => {
    const series = [
      {
        metric: { purpose: 'a' },
        values: [
          [1, 'NaN'],
          [2, '+Inf'],
        ] as Array<[number, string]>,
      },
    ];
    const { rows } = seriesToRows(series, 'purpose');
    expect(rows[0].a).toBe(null);
    // Infinity is finite-checked via Number.isFinite which excludes it
    expect(rows[1].a).toBe(null);
  });
});


describe('MetricsTab rendering', () => {
  it('renders all panel titles and dispatches one query_range per panel', async () => {
    const seenQueries: string[] = [];
    server.use(
      http.post('/api/admin/metrics/query_range', async ({ request }) => {
        const body = (await request.json()) as { query: string };
        seenQueries.push(body.query);
        return HttpResponse.json({
          status: 'success',
          data: { resultType: 'matrix', result: [] },
        });
      }),
    );

    render(<MetricsTab />);

    // Each panel renders its title; assert a representative subset
    // of the 8 panels.
    expect(await screen.findByText('vLLM calls by purpose')).toBeInTheDocument();
    expect(screen.getByText('vLLM outcomes')).toBeInTheDocument();
    expect(screen.getByText('vLLM latency p95 by purpose')).toBeInTheDocument();
    expect(screen.getByText('MCP tool calls (top 10)')).toBeInTheDocument();
    expect(screen.getByText('Phantom URLs (per hour)')).toBeInTheDocument();

    // Wait for the round-trips: one POST per panel.
    await waitFor(() => {
      expect(seenQueries.length).toBe(8);
    });

    // The $WINDOW placeholder must have been substituted before send.
    expect(seenQueries.every(q => !q.includes('$WINDOW'))).toBe(true);
  });

  it('shows "No data" when prometheus returns empty', async () => {
    server.use(
      http.post('/api/admin/metrics/query_range', () =>
        HttpResponse.json({
          status: 'success',
          data: { resultType: 'matrix', result: [] },
        })
      ),
    );

    render(<MetricsTab />);

    await waitFor(() => {
      const noData = screen.getAllByText('No data in this window.');
      // 8 panels all empty
      expect(noData.length).toBe(8);
    });
  });

  it('labels what the rate charts measure', async () => {
    render(<MetricsTab />);
    expect(await screen.findAllByText('calls/s')).toHaveLength(3);
    expect(screen.getByText('tokens/s')).toBeInTheDocument();
    expect(screen.getByText('turns/s')).toBeInTheDocument();
    expect(screen.getByText('URLs/hour')).toBeInTheDocument();
  });

  it('shows one banner, not eight, when every panel fails the same way', async () => {
    server.use(
      http.post('/api/admin/metrics/query_range', () =>
        HttpResponse.json(
          { error: { message: 'parse error: bad PromQL' } },
          { status: 400 }
        )
      ),
    );

    render(<MetricsTab />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('All charts failed to load: parse error: bad PromQL');
    });
    expect(screen.queryByText('vLLM outcomes')).not.toBeInTheDocument();
    expect(screen.queryByText('parse error: bad PromQL')).not.toBeInTheDocument();
  });

  it('brings the panels back when Retry succeeds', async () => {
    const user = userEvent.setup();
    let fail = true;
    server.use(
      http.post('/api/admin/metrics/query_range', () => (
        fail
          ? HttpResponse.json({ error: { message: 'Prometheus is not reachable.' } }, { status: 502 })
          : HttpResponse.json({ status: 'success', data: { resultType: 'matrix', result: [] } })
      )),
    );

    render(<MetricsTab />);
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());

    fail = false;
    await user.click(screen.getByRole('button', { name: 'Retry' }));

    await waitFor(() => expect(screen.getByText('vLLM outcomes')).toBeInTheDocument());
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('keeps the error in the panel when only some panels fail', async () => {
    server.use(
      http.post('/api/admin/metrics/query_range', async ({ request }) => {
        const body = (await request.json()) as { query: string };
        return body.query.includes('munin_phantom_url_total')
          ? HttpResponse.json({ error: { message: 'parse error: bad PromQL' } }, { status: 400 })
          : HttpResponse.json({ status: 'success', data: { resultType: 'matrix', result: [] } });
      }),
    );

    render(<MetricsTab />);

    await waitFor(() => expect(screen.getByText('parse error: bad PromQL')).toBeInTheDocument());
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByText('vLLM outcomes')).toBeInTheDocument();
  });

  it('switching time range re-issues all queries', async () => {
    const user = userEvent.setup();
    let calls = 0;
    server.use(
      http.post('/api/admin/metrics/query_range', () => {
        calls += 1;
        return HttpResponse.json({
          status: 'success',
          data: { resultType: 'matrix', result: [] },
        });
      }),
    );
    render(<MetricsTab />);

    // Initial render -> 8 calls (one per panel)
    await waitFor(() => expect(calls).toBe(8));

    // Click "24h"
    await user.click(screen.getByRole('button', { name: '24h' }));

    // Another 8 calls fire after the range changes.
    await waitFor(() => expect(calls).toBe(16));
  });

  // The colours are CSS variables, so a theme switch recolours a chart that is
  // already drawn. Their values per theme are checked in chartPalette.test.ts.
  it('draws series, legend and frame in theme colours', async () => {
    server.use(
      http.post('/api/admin/metrics/query_range', () =>
        HttpResponse.json({
          status: 'success',
          data: {
            resultType: 'matrix',
            result: [
              { metric: { purpose: 'chat_turn', outcome: 'ok', direction: 'in', terminal_reason: 'done', name: 'search', kind: 'paper' }, values: [[1700000000, '1'], [1700000015, '2']] },
              { metric: { purpose: 'title', outcome: 'retry', direction: 'out', terminal_reason: 'cancelled', name: 'fetch', kind: 'artifact' }, values: [[1700000000, '3'], [1700000015, '4']] },
            ],
          },
        })
      ),
    );

    const { container } = render(<MetricsTab />);

    await waitFor(() => expect(container.querySelectorAll('.recharts-line-curve').length).toBeGreaterThan(0));
    const curves = [...container.querySelectorAll('.recharts-line-curve')];
    expect(curves[0]).toHaveAttribute('stroke', 'var(--chart-1)');
    expect(curves[1]).toHaveAttribute('stroke', 'var(--chart-2)');

    const legend = screen.getAllByText('chat_turn')[0];
    expect(legend.style.color).toBe('var(--chart-1)');

    const bars = container.querySelectorAll('.recharts-bar-rectangle path');
    expect(bars[0]).toHaveAttribute('fill', 'var(--chart-1)');

    // No fixed dark hex is left in the drawn SVG.
    expect(container.innerHTML).not.toMatch(/#(30363d|8b949e|1a1f26|e6edf3|58a6ff)/i);
    expect(container.querySelector('.recharts-cartesian-grid line')).toHaveAttribute('stroke', 'var(--color-border)');
  });
});
