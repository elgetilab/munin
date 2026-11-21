import { useId, useState } from 'react';
import { fetchUsageSummary, usageSummaryCsvUrl } from '../lib/api';
import type { UsageSummary } from '../lib/api';
import { copyText } from '../lib/copyText';
import { SITE_HOME } from '../lib/urls';
import { ErrorNotice } from './ui/ErrorNotice';

// "Credit Munin in a paper": which models answered this user in a date range,
// and a disclosure statement built from exactly those (gateway
// /api/usage/me/summary). Metadata only; the gateway never reads chat content.

function day(iso: string | null): string {
  return iso ? iso.slice(0, 10) : '';
}

function formatCount(n: number): string {
  return n.toLocaleString('en-US');
}

function CopyBlock({ label, text }: { label: string; text: string }) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle');
  const copy = async () => {
    const ok = await copyText(text);
    setState(ok ? 'copied' : 'failed');
    if (ok) setTimeout(() => setState(s => (s === 'copied' ? 'idle' : s)), 2000);
  };
  return (
    <div className="mb-5">
      <div className="flex items-center justify-between gap-3 mb-2">
        <h5 className="text-sm font-medium text-text-primary">{label}</h5>
        <button
          onClick={copy}
          aria-label={state === 'copied' ? `Copied ${label}` : `Copy ${label}`}
          className="px-3 py-1 bg-bg-primary border border-border rounded-lg text-xs text-text-secondary hover:text-text-primary transition-colors cursor-pointer flex-shrink-0"
        >
          {state === 'copied' ? 'Copied!' : 'Copy'}
        </button>
      </div>
      <pre className="bg-bg-secondary border border-border rounded-lg p-4 text-xs text-text-secondary whitespace-pre-wrap wrap-anywhere">{text}</pre>
      <p role="status" className="text-xs text-error mt-1">
        {state === 'failed' && 'Copy failed. Select the text above and copy it by hand.'}
      </p>
    </div>
  );
}

export function CreditMunin() {
  const id = useId();
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      setSummary(await fetchUsageSummary(from, to));
    } catch {
      setError('Could not load your usage.');
    } finally {
      setLoading(false);
    }
  };

  const st = summary?.statement ?? {};
  const references = (st.references_apa ?? []).join('\n\n');

  return (
    <section aria-labelledby={`${id}-h`}>
      <h3 id={`${id}-h`} className="text-lg font-semibold text-text-primary mb-2">Credit Munin in a paper</h3>
      <p className="text-sm text-text-secondary mb-4">
        Munin is a tool, not an author. If it helped with your research or writing, disclose which models you
        used and when. Pick your project's dates to see exactly which models answered you, with a statement you can
        complete. Only metadata is used, never chat content. Templates and publisher rules are on the{' '}
        <a href={`${SITE_HOME}/cite`} className="text-accent hover:underline">Cite Munin</a> page.
      </p>

      <div className="flex flex-wrap items-end gap-3 mb-4">
        <label className="text-xs text-text-secondary">
          From
          <input
            type="date" value={from} onChange={e => setFrom(e.target.value)}
            className="block mt-1 px-3 py-2 bg-bg-secondary border border-border rounded-lg text-sm text-text-primary"
          />
        </label>
        <label className="text-xs text-text-secondary">
          To
          <input
            type="date" value={to} onChange={e => setTo(e.target.value)}
            className="block mt-1 px-3 py-2 bg-bg-secondary border border-border rounded-lg text-sm text-text-primary"
          />
        </label>
        <button
          onClick={load} disabled={loading}
          className="px-4 py-2 bg-accent text-bg-primary rounded-lg text-sm font-medium hover:opacity-90 transition-opacity cursor-pointer disabled:opacity-50"
        >
          {loading ? 'Loading…' : summary ? 'Update' : 'Show my usage'}
        </button>
      </div>
      <p className="text-xs text-text-secondary mb-4">Leave a date empty for "from the start" or "until today".</p>

      {error && <ErrorNotice message={error} onRetry={load} className="mb-4" />}

      {summary && summary.models.length === 0 && (
        <p className="text-sm text-text-secondary mb-6">No model use in this range.</p>
      )}

      {summary && summary.models.length > 0 && (
        <>
          <div className="mb-2 border border-border rounded-lg overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-text-secondary">
                  <th className="px-3 py-2 font-medium">Model</th>
                  <th className="px-3 py-2 font-medium">Used</th>
                  <th className="px-3 py-2 font-medium text-right">Requests</th>
                  <th className="px-3 py-2 font-medium text-right">Tokens</th>
                </tr>
              </thead>
              <tbody>
                {summary.models.map(m => (
                  <tr key={m.model} className="border-t border-border align-top">
                    <td className="px-3 py-2 text-text-primary">
                      {m.display}
                      <div className="text-text-secondary">
                        {m.hosted_by
                          ? `hosted, via the ${m.hosted_by}`
                          : [m.checkpoint, m.revision && `rev ${m.revision.slice(0, 12)}`, m.vllm_version && `vLLM ${m.vllm_version}`]
                              .filter(Boolean).join(' · ')}
                      </div>
                    </td>
                    <td className="px-3 py-2 text-text-secondary whitespace-nowrap">
                      {day(m.first_use)}{day(m.last_use) !== day(m.first_use) && <> to {day(m.last_use)}</>}
                    </td>
                    <td className="px-3 py-2 text-right text-text-secondary">
                      {formatCount(m.requests)}
                      {m.deep_research_jobs > 0 && <div>+{m.deep_research_jobs} Deep Research</div>}
                    </td>
                    <td className="px-3 py-2 text-right text-text-secondary">{formatCount(m.tokens_total)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2 mb-6 text-xs text-text-secondary">
            <span>
              {summary.models.some(m => m.inferred_requests > 0)
                && 'Older requests did not record their model; it is taken from the deployment history.'}
            </span>
            <a href={usageSummaryCsvUrl(from, to)} download className="text-accent hover:underline">Download CSV</a>
          </div>

          {st.methods && <CopyBlock label="Methods statement" text={st.methods} />}
          {st.writing_only && <CopyBlock label="Writing help only" text={st.writing_only} />}
          {st.acknowledgment && <CopyBlock label="Acknowledgment" text={st.acknowledgment} />}
          {references && <CopyBlock label="References" text={references} />}
          {st.bibtex && <CopyBlock label="BibTeX" text={st.bibtex} />}
          <p className="text-xs text-text-secondary mb-6">
            Replace the bracketed parts: only you know the purpose and how you checked the output.
          </p>
        </>
      )}
    </section>
  );
}
