import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../../test/msw-server';
import { MetricsTab } from './MetricsTab';
import { rateWindowFor, seriesToRows } from '../../lib/metricsSeries';

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

  it('shows the upstream error message when prometheus rejects the query', async () => {
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
      const errs = screen.getAllByText('parse error: bad PromQL');
      expect(errs.length).toBe(8);
    });
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
});
