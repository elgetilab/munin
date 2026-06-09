import { useState, useEffect, useCallback, useMemo } from 'react';
import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, Legend, CartesianGrid,
} from 'recharts';
import { metricsQueryRange } from '../../lib/api';
import type { PromSeries } from '../../lib/api';


/**
 * MetricsTab -- in-house dashboard over the retrieval service's
 * Prometheus instance (P1 #12 + Bug 2026-06-02). Each panel issues a
 * range query through the admin-gated proxy on retrieval and renders
 * via recharts. PromQL strings live here in the same file as the
 * panel that consumes them so schema changes (renaming a label,
 * adding a purpose) move atomically with the dashboard.
 */


const TIME_RANGES: Array<{ label: string; minutes: number; stepSeconds: number }> = [
  { label: '1h', minutes: 60, stepSeconds: 15 },
  { label: '6h', minutes: 360, stepSeconds: 60 },
  { label: '24h', minutes: 1440, stepSeconds: 300 },
  { label: '7d', minutes: 10080, stepSeconds: 1800 },
  { label: '30d', minutes: 43200, stepSeconds: 7200 },
];

const AUTO_REFRESH_MS = 30_000;

// Distinct colour palette for series. Picked so adjacent series stay
// distinguishable in colour-blind viewing (Wong palette + an accent).
const SERIES_COLOURS = [
  '#58a6ff', '#3fb950', '#d29922', '#f85149',
  '#a371f7', '#e3b341', '#56d4dd', '#ff7b72',
  '#79c0ff', '#85e89d', '#ffa657', '#f0883e',
];

// ── Panel definitions ──────────────────────────────────────────────────────

interface PanelDef {
  id: string;
  title: string;
  description?: string;
  // PromQL. `$WINDOW` is replaced by a sensible rate window for the
  // selected time range (5m, 15m, 1h, ...).
  query: string;
  // Which label on the returned series becomes the legend / series
  // name. Empty string means "use all labels concat'd" (rare).
  seriesLabel: string;
  chartType: 'line' | 'bar';
  yUnit?: 'count' | 'seconds' | 'percent';
}

const PANELS: PanelDef[] = [
  {
    id: 'vllm-rate',
    title: 'vLLM calls by purpose',
    description: 'Rate of successful vLLM calls per second, broken down by call site.',
    query: 'sum by (purpose) (rate(munin_vllm_request_total[$WINDOW]))',
    seriesLabel: 'purpose',
    chartType: 'line',
    yUnit: 'count',
  },
  {
    id: 'vllm-outcomes',
    title: 'vLLM outcomes',
    description: 'Success vs transient retry vs transport error vs permanent failure.',
    query: 'sum by (outcome) (rate(munin_vllm_request_total[$WINDOW]))',
    seriesLabel: 'outcome',
    chartType: 'line',
    yUnit: 'count',
  },
  {
    id: 'vllm-p95',
    title: 'vLLM latency p95 by purpose',
    description: 'p95 wall time of a successful vLLM call (retries not included).',
    query: 'histogram_quantile(0.95, sum by (purpose, le) (rate(munin_vllm_request_duration_seconds_bucket[$WINDOW])))',
    seriesLabel: 'purpose',
    chartType: 'line',
    yUnit: 'seconds',
  },
  {
    id: 'vllm-tokens',
    title: 'Token throughput',
    description: 'Tokens billed per second across all call sites, broken down by direction.',
    query: 'sum by (direction) (rate(munin_vllm_tokens_total[$WINDOW]))',
    seriesLabel: 'direction',
    chartType: 'line',
    yUnit: 'count',
  },
  {
    id: 'chat-turns',
    title: 'Chat turns by terminal_reason',
    description: 'How turns end: done, cancelled, max_turns, stream_error.',
    query: 'sum by (terminal_reason) (rate(munin_chat_turns_total[$WINDOW]))',
    seriesLabel: 'terminal_reason',
    chartType: 'line',
    yUnit: 'count',
  },
  {
    id: 'mcp-tool-rate',
    title: 'MCP tool calls (top 10)',
    description: 'Rate of MCP tool dispatches; top 10 tools by volume.',
    query: 'topk(10, sum by (name) (rate(munin_mcp_tool_total[$WINDOW])))',
    seriesLabel: 'name',
    chartType: 'line',
    yUnit: 'count',
  },
  {
    id: 'mcp-tool-errors',
    title: 'MCP tool error rate',
    description: 'Fraction of MCP dispatches that returned an error, by tool.',
    query: 'sum by (name) (rate(munin_mcp_tool_total{outcome="error"}[$WINDOW])) / clamp_min(sum by (name) (rate(munin_mcp_tool_total[$WINDOW])), 0.001)',
    seriesLabel: 'name',
    chartType: 'line',
    yUnit: 'percent',
  },
  {
    id: 'phantom-urls',
    title: 'Phantom URLs (per hour)',
    description: 'Hallucinated artifact / paper URLs caught by the post-turn audit.',
    query: 'sum by (kind) (increase(munin_phantom_url_total[1h]))',
    seriesLabel: 'kind',
    chartType: 'bar',
    yUnit: 'count',
  },
];


/** Pick a Prometheus rate-window for the selected time range. Too
 * short and rate() returns noise; too long and step-aligned curves
 * smear over real changes. The values below match Grafana's defaults
 * for similar zoom levels. */
function rateWindowFor(minutes: number): string {
  if (minutes <= 60) return '5m';
  if (minutes <= 360) return '15m';
  if (minutes <= 1440) return '1h';
  if (minutes <= 10080) return '6h';
  return '1d';
}


// ── Data shape transforms ──────────────────────────────────────────────────

/** Flatten Prometheus matrix series into recharts-friendly rows.
 *
 * Prometheus returns one series per label combination, each with its
 * own list of timestamps. recharts wants rows where every series'
 * value at a given timestamp is a column. We bucket on the union of
 * timestamps and fill missing values with `null` (recharts skips
 * them rather than zeroing). */
function seriesToRows(
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


// ── Components ─────────────────────────────────────────────────────────────

export function MetricsTab() {
  const [rangeIdx, setRangeIdx] = useState(1); // default 6h
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [refreshTick, setRefreshTick] = useState(0);

  useEffect(() => {
    if (!autoRefresh) return;
    const id = setInterval(() => setRefreshTick(t => t + 1), AUTO_REFRESH_MS);
    return () => clearInterval(id);
  }, [autoRefresh]);

  const range = TIME_RANGES[rangeIdx];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-1">
          {TIME_RANGES.map((r, i) => (
            <button
              key={r.label}
              onClick={() => setRangeIdx(i)}
              className={`px-2 py-1 text-xs rounded-md cursor-pointer transition-colors ${
                i === rangeIdx
                  ? 'bg-bg-tertiary text-text-primary'
                  : 'text-text-secondary hover:bg-bg-tertiary hover:text-text-primary'
              }`}
            >
              {r.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-3 text-xs text-text-secondary">
          <label className="flex items-center gap-1.5 cursor-pointer">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={e => setAutoRefresh(e.target.checked)}
              className="cursor-pointer"
            />
            <span>Auto-refresh ({AUTO_REFRESH_MS / 1000}s)</span>
          </label>
          <button
            onClick={() => setRefreshTick(t => t + 1)}
            className="px-2 py-1 rounded-md text-text-secondary hover:bg-bg-tertiary hover:text-text-primary cursor-pointer"
            title="Refresh now"
          >
            Refresh
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {PANELS.map(panel => (
          <Panel
            key={panel.id}
            def={panel}
            range={range}
            refreshTick={refreshTick}
          />
        ))}
      </div>
    </div>
  );
}


function Panel({
  def, range, refreshTick,
}: {
  def: PanelDef;
  range: typeof TIME_RANGES[number];
  refreshTick: number;
}) {
  const [series, setSeries] = useState<PromSeries[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const end = new Date();
      const start = new Date(end.getTime() - range.minutes * 60_000);
      const query = def.query.replaceAll('$WINDOW', rateWindowFor(range.minutes));
      const resp = await metricsQueryRange(query, start, end, range.stepSeconds);
      if (resp.status !== 'success') {
        throw new Error(resp.error || 'query failed');
      }
      setSeries(resp.data.result);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'fetch failed');
      setSeries([]);
    }
    setLoading(false);
  }, [def.query, range.minutes, range.stepSeconds]);

  useEffect(() => { load(); }, [load, refreshTick]);

  const { rows, names } = useMemo(
    () => seriesToRows(series, def.seriesLabel),
    [series, def.seriesLabel],
  );

  return (
    <div className="bg-bg-secondary border border-border rounded-lg p-3">
      <div className="mb-2">
        <h3 className="text-sm font-medium text-text-primary">{def.title}</h3>
        {def.description && (
          <p className="text-xs text-text-secondary mt-0.5">{def.description}</p>
        )}
      </div>
      <div className="h-56">
        {loading && rows.length === 0 ? (
          <div className="h-full flex items-center justify-center text-text-secondary text-xs">
            Loading...
          </div>
        ) : err ? (
          <div className="h-full flex items-center justify-center text-error text-xs px-2 text-center">
            {err}
          </div>
        ) : rows.length === 0 ? (
          <div className="h-full flex items-center justify-center text-text-secondary text-xs">
            No data in this window.
          </div>
        ) : (
          <ChartFor def={def} rows={rows} names={names} />
        )}
      </div>
    </div>
  );
}


function ChartFor({
  def, rows, names,
}: {
  def: PanelDef;
  rows: Array<Record<string, number | string | null>>;
  names: string[];
}) {
  const yFormatter = useMemo(() => {
    if (def.yUnit === 'percent') {
      return (v: number) => `${(v * 100).toFixed(0)}%`;
    }
    if (def.yUnit === 'seconds') {
      return (v: number) => v >= 1 ? `${v.toFixed(1)}s` : `${(v * 1000).toFixed(0)}ms`;
    }
    return (v: number) => v >= 1000 ? `${(v / 1000).toFixed(1)}k` : v.toFixed(2);
  }, [def.yUnit]);

  if (def.chartType === 'bar') {
    return (
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} margin={{ top: 5, right: 5, left: -10, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#30363d" vertical={false} />
          <XAxis dataKey="time" stroke="#8b949e" fontSize={10} tickLine={false} axisLine={false} />
          <YAxis stroke="#8b949e" fontSize={10} tickFormatter={yFormatter} tickLine={false} axisLine={false} width={45} />
          <Tooltip
            contentStyle={{ background: '#1a1f26', border: '1px solid #30363d', borderRadius: 6, fontSize: 12 }}
            labelStyle={{ color: '#e6edf3' }}
            formatter={(v) => yFormatter(typeof v === 'number' ? v : Number.parseFloat(String(v)))}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {names.map((name, i) => (
            <Bar key={name} dataKey={name} fill={SERIES_COLOURS[i % SERIES_COLOURS.length]} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    );
  }

  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={rows} margin={{ top: 5, right: 5, left: -10, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#30363d" vertical={false} />
        <XAxis dataKey="time" stroke="#8b949e" fontSize={10} tickLine={false} axisLine={false} />
        <YAxis stroke="#8b949e" fontSize={10} tickFormatter={yFormatter} tickLine={false} axisLine={false} width={45} />
        <Tooltip
          contentStyle={{ background: '#1a1f26', border: '1px solid #30363d', borderRadius: 6, fontSize: 12 }}
          labelStyle={{ color: '#e6edf3' }}
          formatter={(v) => yFormatter(typeof v === 'number' ? v : Number.parseFloat(String(v)))}
        />
        <Legend wrapperStyle={{ fontSize: 11 }} />
        {names.map((name, i) => (
          <Line
            key={name}
            type="monotone"
            dataKey={name}
            stroke={SERIES_COLOURS[i % SERIES_COLOURS.length]}
            dot={false}
            strokeWidth={2}
            connectNulls={false}
            isAnimationActive={false}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

export const _testables = { seriesToRows, rateWindowFor };
