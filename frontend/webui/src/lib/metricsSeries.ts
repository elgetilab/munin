/**
 * Prometheus range-query helpers for the admin Metrics tab.
 *
 * Lives here rather than beside the component that uses it because a
 * module that exports both a component and a plain function breaks React
 * Fast Refresh (react-refresh/only-export-components). It is a pure
 * function with its own tests, so a lib module is where it belongs.
 */

import type { PromSeries } from './api';


/** Pick a Prometheus rate-window for the selected time range. Too
 * short and rate() returns noise; too long and step-aligned curves
 * smear over real changes. The values below match Grafana's defaults
 * for similar zoom levels. */
export function rateWindowFor(minutes: number): string {
  if (minutes <= 60) return '5m';
  if (minutes <= 360) return '15m';
  if (minutes <= 1440) return '1h';
  if (minutes <= 10080) return '6h';
  return '1d';
}

/** Flatten Prometheus matrix series into recharts-friendly rows.
 *
 * Prometheus returns one series per label combination, each with its
 * own list of timestamps. recharts wants rows where every series'
 * value at a given timestamp is a column. We bucket on the union of
 * timestamps and fill missing values with `null` (recharts skips
 * them rather than zeroing). */
export function seriesToRows(
  series: PromSeries[],
  seriesLabel: string,
): { rows: Array<Record<string, number | string | null>>; names: string[] } {
  const seriesNames: string[] = [];
  const tsBuckets = new Map<number, Record<string, number | null>>();
  for (const s of series) {
    const name = s.metric[seriesLabel] || JSON.stringify(s.metric);
    if (!seriesNames.includes(name)) seriesNames.push(name);
    for (const [ts, valStr] of s.values) {
      const val = Number.parseFloat(valStr);
      const row = tsBuckets.get(ts) ?? {};
      row[name] = Number.isFinite(val) ? val : null;
      tsBuckets.set(ts, row);
    }
  }
  const sortedTs = Array.from(tsBuckets.keys()).sort((a, b) => a - b);
  const rows: Array<Record<string, number | string | null>> = sortedTs.map(ts => {
    const row: Record<string, number | string | null> = {
      time: new Date(ts * 1000).toLocaleTimeString('en-US', {
        hour: '2-digit', minute: '2-digit', hour12: false,
      }),
      _ts: ts,
    };
    for (const name of seriesNames) {
      row[name] = tsBuckets.get(ts)?.[name] ?? null;
    }
    return row;
  });
  return { rows, names: seriesNames };
}
