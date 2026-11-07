import { useState, useEffect, useCallback, useMemo } from 'react';
import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, Legend, CartesianGrid,
} from 'recharts';
import { metricsQueryRange } from '../../lib/api';
import type { PromSeries } from '../../lib/api';
import { formatMetricValue, rateWindowFor, seriesToRows } from '../../lib/metricsSeries';
import { ErrorNotice } from '../ui/ErrorNotice';
import { seriesColor } from '../../lib/chartPalette';


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

// Series colours come from --chart-1 to --chart-12 (src/styles/charts.css), and
// the chart's frame from the theme tokens, so a theme switch recolours the charts
// with no re-render. Recharts writes these strings into SVG attributes and inline
// styles, where var() resolves.
const GRID = 'var(--color-border)';
const AXIS = 'var(--color-text-secondary)';
const TOOLTIP_STYLE = {
  background: 'var(--color-bg-secondary)',
  border: '1px solid var(--color-border)',
  borderRadius: 6,
  fontSize: 12,
};
const TOOLTIP_LABEL_STYLE = { color: 'var(--color-text-primary)' };

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
  // What a 'count' value measures. The other kinds label their own values.
  unit?: string;
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
    unit: 'calls/s',
  },
  {
    id: 'vllm-outcomes',
    title: 'vLLM outcomes',
    description: 'Success vs transient retry vs transport error vs permanent failure.',
    query: 'sum by (outcome) (rate(munin_vllm_request_total[$WINDOW]))',
    seriesLabel: 'outcome',
    chartType: 'line',
    yUnit: 'count',
    unit: 'calls/s',
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
    unit: 'tokens/s',
  },
  {
    id: 'chat-turns',
    title: 'Chat turns by terminal_reason',
    description: 'How turns end: done, cancelled, max_turns, stream_error.',
    query: 'sum by (terminal_reason) (rate(munin_chat_turns_total[$WINDOW]))',
    seriesLabel: 'terminal_reason',
    chartType: 'line',
    yUnit: 'count',
    unit: 'turns/s',
  },
  {
    id: 'mcp-tool-rate',
    title: 'MCP tool calls (top 10)',
    description: 'Rate of MCP tool dispatches; top 10 tools by volume.',
    query: 'topk(10, sum by (name) (rate(munin_mcp_tool_total[$WINDOW])))',
    seriesLabel: 'name',
    chartType: 'line',
    yUnit: 'count',
    unit: 'calls/s',
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
    unit: 'URLs/hour',
  },
];




// ── Data shape transforms ──────────────────────────────────────────────────



// ── Components ─────────────────────────────────────────────────────────────

export function MetricsTab() {
  const [rangeIdx, setRangeIdx] = useState(1); // default 6h
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [refreshTick, setRefreshTick] = useState(0);

  // Each panel's latest error, or null. When every panel fails the same way
  // (Prometheus is down), one banner replaces eight copies of the message. The
  // panels stay mounted but draw nothing, so they keep querying and come back.
  const [panelErrors, setPanelErrors] = useState<Record<string, string | null>>({});
  const reportPanel = useCallback((id: string, err: string | null) => {
    setPanelErrors(prev => (prev[id] === err ? prev : { ...prev, [id]: err }));
  }, []);
  const firstError = panelErrors[PANELS[0].id];
  const sharedError = firstError && PANELS.every(p => panelErrors[p.id] === firstError) ? firstError : null;

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

      {sharedError && (
        <ErrorNotice
          message={`All charts failed to load: ${sharedError}`}
          onRetry={() => setRefreshTick(t => t + 1)}
        />
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {PANELS.map(panel => (
          <Panel
            key={panel.id}
            def={panel}
            range={range}
            refreshTick={refreshTick}
            collapsed={!!sharedError}
            onResult={reportPanel}
          />
        ))}
      </div>
    </div>
  );
}


function Panel({
  def, range, refreshTick, collapsed, onResult,
}: {
  def: PanelDef;
  range: typeof TIME_RANGES[number];
  refreshTick: number;
  collapsed: boolean;
  onResult: (id: string, err: string | null) => void;
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
      onResult(def.id, null);
    } catch (e) {
      const message = e instanceof Error ? e.message : 'fetch failed';
      setErr(message);
      setSeries([]);
      onResult(def.id, message);
    }
    setLoading(false);
  }, [def.id, def.query, range.minutes, range.stepSeconds, onResult]);

  useEffect(() => { load(); }, [load, refreshTick]);

  const { rows, names } = useMemo(
    () => seriesToRows(series, def.seriesLabel),
    [series, def.seriesLabel],
  );

  if (collapsed) return null;

  return (
    <div className="bg-bg-secondary border border-border rounded-lg p-3">
      <div className="mb-2">
        <div className="flex items-baseline justify-between gap-2">
          <h3 className="text-sm font-medium text-text-primary">{def.title}</h3>
          {def.unit && <span className="text-[11px] text-text-tertiary whitespace-nowrap">{def.unit}</span>}
        </div>
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
    return formatMetricValue;
  }, [def.yUnit]);

  const tooltipFormatter = (v: unknown) => {
    const value = yFormatter(typeof v === 'number' ? v : Number.parseFloat(String(v)));
    return def.unit ? `${value} ${def.unit}` : value;
  };

  if (def.chartType === 'bar') {
    return (
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} margin={{ top: 5, right: 5, left: -10, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={GRID} vertical={false} />
          <XAxis dataKey="time" stroke={AXIS} fontSize={10} tickLine={false} axisLine={false} />
          <YAxis stroke={AXIS} fontSize={10} tickFormatter={yFormatter} tickLine={false} axisLine={false} width={45} />
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            labelStyle={TOOLTIP_LABEL_STYLE}
            formatter={tooltipFormatter}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {names.map((name, i) => (
            <Bar key={name} dataKey={name} fill={seriesColor(i)} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    );
  }

  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={rows} margin={{ top: 5, right: 5, left: -10, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={GRID} vertical={false} />
        <XAxis dataKey="time" stroke={AXIS} fontSize={10} tickLine={false} axisLine={false} />
        <YAxis stroke={AXIS} fontSize={10} tickFormatter={yFormatter} tickLine={false} axisLine={false} width={45} />
        <Tooltip
          contentStyle={TOOLTIP_STYLE}
          labelStyle={TOOLTIP_LABEL_STYLE}
          formatter={tooltipFormatter}
        />
        <Legend wrapperStyle={{ fontSize: 11 }} />
        {names.map((name, i) => (
          <Line
            key={name}
            type="monotone"
            dataKey={name}
            stroke={seriesColor(i)}
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

