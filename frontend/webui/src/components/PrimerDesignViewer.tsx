// The widget for a `design_primers` result: the artifact body is a small JSON
// spec (kind "primer_design") that the tool writes, and this draws it.
//
// Unlike ProteinViewer this runs in the page rather than in a WidgetFrame. It
// needs no third-party library and no 3D context, its data is already in the
// host (InlineArtifacts fetches the artifact), and two things it has to do are
// not available inside the frame: follow the app's own light and dark tokens,
// and hand the user a file. A frame with sandbox="allow-scripts" cannot start a
// download at all. A click on <a download> inside it is dropped silently, with
// no error and no console warning (verified in e2e/widgets.spec.ts), so an
// in-frame download would look like it worked and produce nothing. Drawing here
// keeps the saves on the host side, where ArtifactPanel already saves files,
// and leaves the frame's sandbox untouched.
import { saveBlob } from '../lib/artifactDownload';
import {
  ampliconSpan,
  buildDownload,
  checkCounts,
  checkGroups,
  checkLabel,
  focusSpan,
  decimals,
  modulo,
  oligoSpan,
  primerRows,
  windowPieces,
  type CheckStatus,
  type DownloadKind,
  type MapWindow,
  type PrimerCheck,
  type PrimerDesignSpec,
  type PrimerOligo,
  type PrimerPair,
  type PrimerVerified,
  type Span,
} from '../lib/primerDesign';

// --- the template map ------------------------------------------------------

const MAP_W = 600;
const MAP_PAD = 14;
const MAP_INNER = MAP_W - 2 * MAP_PAD;
const PLUS_Y = 30;
const MINUS_Y = 50;

function round(value: number): number {
  return Math.round(value * 100) / 100;
}

function toX(value: number, win: MapWindow): number {
  return round(MAP_PAD + (value / win.span) * MAP_INNER);
}

function arrowPath(x0: number, x1: number, centreY: number, height: number, forward: boolean): string {
  const half = height / 2;
  const head = Math.min(7, Math.max(2, (x1 - x0) * 0.45));
  if (forward) {
    const neck = round(x1 - head);
    return `M ${x0} ${centreY - half} L ${neck} ${centreY - half} L ${x1} ${centreY} L ${neck} ${centreY + half} L ${x0} ${centreY + half} Z`;
  }
  const neck = round(x0 + head);
  return `M ${x1} ${centreY - half} L ${neck} ${centreY - half} L ${x0} ${centreY} L ${neck} ${centreY + half} L ${x1} ${centreY + half} Z`;
}

function PrimerArrow({ span, win, length, circular, forward, label }: {
  span: Span | null;
  win: MapWindow;
  length: number | null;
  circular: boolean;
  forward: boolean;
  label: string;
}) {
  const pieces = windowPieces(span, win, length, circular);
  if (pieces.length === 0) return null;
  const centreY = forward ? PLUS_Y : MINUS_Y;
  return (
    <g data-testid={forward ? 'primer-arrow-forward' : 'primer-arrow-reverse'}>
      <title>{label}</title>
      {pieces.map(([from, to], i) => {
        const x0 = toX(from, win);
        const x1 = Math.max(toX(to, win), x0 + 9);
        return <path key={i} d={arrowPath(x0, x1, centreY, 11, forward)} fill="var(--color-accent)" />;
      })}
    </g>
  );
}

function TemplateMap({ pair, spec, win, caption }: {
  pair: PrimerPair;
  spec: PrimerDesignSpec;
  win: MapWindow;
  caption: string;
}) {
  const { length, circular } = spec.template;
  const amplicon = ampliconSpan(pair, length);
  const forward = oligoSpan(pair.forward, length);
  const reverse = oligoSpan(pair.reverse, length);
  const target = spec.target
    ? { start: Math.min(spec.target[0], spec.target[1]), span: Math.abs(spec.target[1] - spec.target[0]) || 1 }
    : null;
  const ampliconPieces = windowPieces(amplicon, win, length, circular);
  const targetPieces = windowPieces(target, win, length, circular);
  const wrapped = !!length && !!amplicon && circular && amplicon.start + amplicon.span > length;
  const left = Math.round(win.from);
  const right = Math.round(length && circular ? modulo(win.from + win.span, length) || length : win.from + win.span);
  const describe = [
    `Template map of ${spec.template.name || 'the template'}`,
    length ? `${length} bp` : '',
    circular ? 'circular' : 'linear',
    forward ? `forward primer at ${forward.start}` : '',
    reverse ? `reverse primer at ${reverse.start}` : '',
    amplicon ? `amplicon ${amplicon.span} bp` : '',
    wrapped ? 'wrapping the origin' : '',
  ].filter(Boolean).join(', ');

  return (
    <div data-testid="primer-map" data-wrapped={wrapped ? 'true' : 'false'} className="px-3 pt-2">
      <svg
        viewBox={`0 0 ${MAP_W} 78`}
        role="img"
        aria-label={describe}
        className="w-full h-auto"
        preserveAspectRatio="xMidYMid meet"
      >
        {targetPieces.map(([from, to], i) => (
          <rect
            key={`t${i}`}
            data-testid="primer-map-target"
            x={toX(from, win)}
            y={14}
            width={Math.max(toX(to, win) - toX(from, win), 2)}
            height={52}
            fill="var(--color-warning)"
            fillOpacity="0.18"
            stroke="var(--color-warning)"
            strokeDasharray="3 3"
            strokeOpacity="0.6"
          />
        ))}
        <line x1={MAP_PAD} y1={PLUS_Y} x2={MAP_W - MAP_PAD} y2={PLUS_Y} stroke="var(--color-border)" strokeWidth="2" />
        <line x1={MAP_PAD} y1={MINUS_Y} x2={MAP_W - MAP_PAD} y2={MINUS_Y} stroke="var(--color-border)" strokeWidth="2" />
        <text x={2} y={PLUS_Y + 3} fontSize="10" fill="var(--color-text-secondary)">+</text>
        <text x={2} y={MINUS_Y + 3} fontSize="10" fill="var(--color-text-secondary)">-</text>
        {ampliconPieces.map(([from, to], i) => (
          <rect
            key={`a${i}`}
            data-testid="primer-map-amplicon"
            x={toX(from, win)}
            y={PLUS_Y + 4}
            width={Math.max(toX(to, win) - toX(from, win), 2)}
            height={MINUS_Y - PLUS_Y - 8}
            fill="var(--color-accent)"
            fillOpacity="0.28"
          />
        ))}
        <PrimerArrow
          span={forward}
          win={win}
          length={length}
          circular={circular}
          forward
          label={`Forward primer, plus strand${forward ? `, ${forward.start} to ${forward.start + forward.span}` : ''}`}
        />
        <PrimerArrow
          span={reverse}
          win={win}
          length={length}
          circular={circular}
          forward={false}
          label={`Reverse primer, minus strand${reverse ? `, ${reverse.start} to ${reverse.start + reverse.span}` : ''}`}
        />
        <text x={MAP_PAD} y={74} fontSize="10" fill="var(--color-text-secondary)">{left}</text>
        <text x={MAP_W - MAP_PAD} y={74} fontSize="10" textAnchor="end" fill="var(--color-text-secondary)">{right}</text>
      </svg>
      <p className="mt-1 text-[11px] text-text-secondary">
        {caption}
        {wrapped && <span data-testid="primer-map-wrap-note"> · the amplicon wraps the origin</span>}
      </p>
    </div>
  );
}

// Below this the amplicon is a sliver of the whole template, so a second map
// shows its neighbourhood. The window may itself cross the origin, which the
// window arithmetic already handles.
const DETAIL_RATIO = 5;

function detailWindow(pair: PrimerPair, spec: PrimerDesignSpec, extent: number): MapWindow | null {
  const { length, circular } = spec.template;
  // A sequencing primer has no product, so the zoom follows the primer itself.
  const focus = focusSpan(pair, length);
  if (!length || !focus || focus.span * DETAIL_RATIO > extent) return null;
  const pad = Math.max(Math.round(focus.span * 0.5), 25);
  const span = Math.min(focus.span + 2 * pad, extent);
  const from = circular ? modulo(focus.start - pad, length) : Math.max(0, Math.min(focus.start - pad, extent - span));
  return { from, span };
}

// --- the rest of a pair ----------------------------------------------------

function SequenceLine({ oligo, direction }: { oligo: PrimerOligo; direction: 'Forward' | 'Reverse' }) {
  // Runs of one case at a time: lowercase is the 5' tail, which does not bind,
  // and uppercase is the annealing region. For a cloning primer that difference
  // is the whole point of the design, so it is drawn, not just stated.
  const runs = oligo.display.match(/[a-z]+|[^a-z]+/g) ?? [];
  const anneals = oligo.tail
    ? 'Of the annealing region only, not the 5\' tail. Hairpin and dimer temperatures in the checks are for the whole oligo.'
    : 'Of the annealing region, which here is the whole oligo.';
  return (
    <div data-testid="primer-oligo" data-direction={direction.toLowerCase()} className="min-w-0">
      <div className="flex flex-wrap items-baseline gap-x-2 text-xs">
        <span className="text-text-primary font-medium">{direction}</span>
        <span className="text-text-secondary">{oligo.strand ? `${oligo.strand} strand` : ''}</span>
        <span className="text-text-secondary">
          {oligo.start !== null && oligo.end !== null ? `anneals ${oligo.start}-${oligo.end}` : ''}
        </span>
      </div>
      <p className="mt-0.5 font-mono text-[12px] leading-relaxed wrap-anywhere">
        <span className="text-text-tertiary select-none">5&apos;-</span>
        <span data-testid="primer-display">
          {runs.map((run, i) => (
            <span
              key={i}
              data-part={/^[a-z]+$/.test(run) ? 'tail' : 'anneal'}
              className={/^[a-z]+$/.test(run)
                ? 'text-text-secondary italic underline decoration-dotted underline-offset-2'
                : 'text-text-primary font-semibold'}
            >
              {run}
            </span>
          ))}
        </span>
        <span className="text-text-tertiary select-none">-3&apos;</span>
      </p>
      {/* Tm and GC are the ANNEALING region's, which is what the checks
          judged, while Length is the whole oligo. With a tail those are
          different stretches of DNA, so each says which one it means. */}
      <dl className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-text-secondary">
        <Stat label="Tm" value={oligo.tm === null ? 'n/a' : `${decimals(oligo.tm, 1)} C`} title={anneals} />
        <Stat label="GC" value={oligo.gc === null ? 'n/a' : `${decimals(oligo.gc, 1)}%`} title={anneals} />
        <Stat
          label="Length"
          value={oligo.length === null ? 'n/a' : `${oligo.length} nt`}
          title={oligo.tail
            ? `The whole oligo: ${oligo.tail.length} nt of tail plus ${oligo.anneal.length || (oligo.length ?? 0) - oligo.tail.length} nt that anneal`
            : 'The whole oligo'}
        />
        {oligo.tail && (
          <Stat
            label="Tail"
            value={`${oligo.tail.length} nt`}
            title="A 5' tail is not in the template, so it has no coordinates and is not in the quoted Tm or GC"
          />
        )}
      </dl>
    </div>
  );
}

function Stat({ label, value, title }: { label: string; value: string; title?: string }) {
  return (
    <span className="inline-flex items-baseline gap-1" title={title}>
      <dt className="text-text-tertiary">{label}</dt>
      <dd className="text-text-secondary">{value}</dd>
    </span>
  );
}

const STATUS_LABEL: Record<CheckStatus, string> = {
  pass: 'Pass',
  fail: 'Fail',
  'not-computed': 'Not computed',
};

// A not-computed check is neither green nor red: it is outlined and dashed, so
// it can never be read as a check that passed.
function statusClass(check: PrimerCheck): string {
  if (check.status === 'pass') return 'bg-success/15 text-success';
  if (check.status === 'fail') {
    return check.severity === 'soft' ? 'bg-warning/15 text-warning' : 'bg-error/15 text-error';
  }
  return 'border border-dashed border-border-input text-text-secondary';
}

function ChecksBlock({ checks }: { checks: PrimerCheck[] }) {
  if (checks.length === 0) return null;
  const passed = checks.filter(c => c.status === 'pass').length;
  const failed = checks.filter(c => c.status === 'fail');
  const notComputed = checks.filter(c => c.status === 'not-computed').length;
  const hard = failed.filter(c => c.severity !== 'soft').length;
  const soft = failed.length - hard;
  const attention = failed.length > 0 || notComputed > 0;
  return (
    <details data-testid="primer-checks" open={attention} className="px-3 py-2 border-t border-border">
      <summary className="flex flex-wrap items-center gap-2 text-xs text-text-secondary cursor-pointer">
        <span className="text-text-primary">{passed} of {checks.length} checks passed</span>
        {hard > 0 && <span className="px-1.5 py-0.5 rounded bg-error/15 text-error">{hard} hard</span>}
        {soft > 0 && <span className="px-1.5 py-0.5 rounded bg-warning/15 text-warning">{soft} soft</span>}
        {notComputed > 0 && (
          <span className="px-1.5 py-0.5 rounded border border-dashed border-border-input text-text-secondary">
            {notComputed} not computed
          </span>
        )}
      </summary>
      {/* One group per rule namespace: a pair carries the same rule once per
          primer, so the scope is what tells the two apart. */}
      {checkGroups(checks).map((group, g) => (
        <div key={`${group.scope}-${g}`} data-testid="primer-check-group" data-scope={group.scope}>
          {group.heading && <p className="mt-2 text-[11px] text-text-tertiary uppercase tracking-wide">{group.heading}</p>}
          <ul className="mt-1 space-y-1.5">
            {group.checks.map((check, i) => (
              <li
                key={`${check.rule}-${i}`}
                data-testid="primer-check"
                data-rule={check.rule}
                data-status={check.status}
                data-severity={check.severity}
                className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-[11px]"
              >
                <span className={`px-1.5 py-0.5 rounded shrink-0 ${statusClass(check)}`}>{STATUS_LABEL[check.status]}</span>
                <span className="text-text-primary">{checkLabel(check, group.heading)}</span>
                {check.severity && <span className="text-text-tertiary">({check.severity})</span>}
                {check.value && <span className="text-text-secondary font-mono">{check.value}</span>}
                {check.threshold && <span className="text-text-secondary">vs {check.threshold}</span>}
                {check.detail && <span className="text-text-secondary">{check.detail}</span>}
                {check.source && <span className="text-text-tertiary italic">{check.source}</span>}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </details>
  );
}

function VerifiedBlock({ verified, mode }: { verified: PrimerVerified | null; mode: string }) {
  const clean = verified?.singleCleanProduct ?? null;
  // A sequencing design makes no amplicon, so its verified block counts
  // PRIMING SITES: "single" there means the primer binds in one place.
  const sequencing = mode === 'sequencing';
  const tone = clean === true
    ? 'bg-success/15 text-success'
    : clean === false
      ? 'bg-error/15 text-error'
      : 'border border-dashed border-border-input text-text-secondary';
  const headline = clean === true
    ? (sequencing ? 'Binds in one place' : 'Single clean product')
    : clean === false
      ? (sequencing ? 'Binds in more than one place' : 'Not a single clean product')
      : 'Not verified';
  const sizes = verified && verified.productSizes.length > 0
    ? `${verified.productSizes.join(', ')} bp`
    : '';
  const count = verified?.productCount;
  const unit = sequencing ? 'priming site' : 'in-silico product';
  return (
    <div
      data-testid="primer-verified"
      data-clean={clean === null ? 'unknown' : String(clean)}
      data-outcome={verified?.outcome ?? ''}
      className="flex flex-wrap items-center gap-2 px-3 py-2 text-xs"
    >
      <span className={`px-2 py-0.5 rounded font-medium ${tone}`}>{headline}</span>
      <span className="text-text-secondary">
        {verified
          ? [
            count === null || count === undefined ? '' : `${count} ${unit}${count === 1 ? '' : 's'}`,
            sizes,
          ].filter(Boolean).join(' · ')
          : 'the tool returned no in-silico result for this design'}
      </span>
      {/* The outcome word is the tool's own, and in sequencing mode its
          "product" counts priming sites, so it says so on hover. */}
      {verified?.outcome && (
        <span
          className="text-text-tertiary"
          title={sequencing
            ? `In sequencing mode the outcome counts priming sites: ${verified.outcome} means the primer binds in exactly one place.`
            : 'The in-silico PCR verdict from the tool.'}
        >
          outcome: {verified.outcome}
        </span>
      )}
    </div>
  );
}

function PairCard({ pair, spec }: { pair: PrimerPair; spec: PrimerDesignSpec }) {
  const { length, circular } = spec.template;
  const amplicon = ampliconSpan(pair, length);
  // In overlap_assembly the coordinates are on the assembled construct, which
  // can run past the stated template length. A linear axis stretches to hold
  // them rather than clipping a primer out of sight.
  const focus = focusSpan(pair, length);
  const extent = length && length > 0
    ? (circular ? length : Math.max(length, focus ? focus.start + focus.span : 0))
    : 0;
  const detail = detailWindow(pair, spec, extent);
  const overview: MapWindow | null = extent > 0 ? { from: 0, span: extent } : null;
  const coordinates = amplicon
    ? `${amplicon.start}-${length && circular ? modulo(amplicon.start + amplicon.span, length) : amplicon.start + amplicon.span}`
    : '';
  // A tailed primer makes a product longer than the stretch of template it
  // came from, and the map can only draw the template part.
  const tails = amplicon && pair.productSize !== null && pair.productSize > amplicon.span
    ? ` · ${amplicon.span} bp of it on the template, the rest is 5' tail`
    : '';
  return (
    <section data-testid="primer-pair" data-name={pair.name} className="border-t border-border">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-3 pt-2">
        <h4 className="text-sm text-text-primary font-medium">{pair.name}</h4>
        {pair.productSize !== null && pair.productSize > 0
          && <span className="text-xs text-text-secondary">{pair.productSize} bp product</span>}
        {pair.productSize === 0 && <span className="text-xs text-text-secondary">single primer, no product</span>}
        {pair.tmDifference !== null && <span className="text-xs text-text-secondary">ΔTm {decimals(pair.tmDifference, 2)} C</span>}
        {pair.penalty !== null && <span className="text-xs text-text-tertiary">penalty {decimals(pair.penalty, 2)}</span>}
      </div>

      <VerifiedBlock verified={pair.verified} mode={spec.mode} />

      {overview
        ? (
          <>
            <TemplateMap
              pair={pair}
              spec={spec}
              win={overview}
              caption={`Whole template${coordinates ? ` · amplicon ${coordinates}` : ''}${tails}`}
            />
            {detail && (
              <TemplateMap
                pair={pair}
                spec={spec}
                win={detail}
                caption={`Detail · ${detail.from} to ${circular && length ? modulo(detail.from + detail.span, length) || length : detail.from + detail.span}`}
              />
            )}
          </>
        )
        : <p className="px-3 py-1 text-[11px] text-text-secondary">No map: the spec does not give the template length.</p>}

      <div className="grid gap-3 px-3 py-2 sm:grid-cols-2">
        {pair.forward && <SequenceLine oligo={pair.forward} direction="Forward" />}
        {pair.reverse && <SequenceLine oligo={pair.reverse} direction="Reverse" />}
      </div>

      <ChecksBlock checks={pair.checks} />
    </section>
  );
}

const MODE_LABELS: Record<string, string> = {
  pcr: 'PCR',
  overlap_assembly: 'Overlap assembly',
  gibson: 'Overlap assembly',
  restriction: 'Restriction cloning',
  sequencing: 'Sequencing',
};

interface PrimerDesignViewerProps {
  spec: PrimerDesignSpec;
  title: string;
}

export function PrimerDesignViewer({ spec, title }: PrimerDesignViewerProps) {
  const { template, summary } = spec;
  const counts = checkCounts(spec);
  const download = (kind: DownloadKind) => {
    const file = buildDownload(spec, kind);
    saveBlob(new Blob([file.text], { type: file.type }), file.filename);
  };
  const buttonClass = 'px-2 py-1 bg-bg-tertiary border border-border rounded text-xs text-text-primary hover:border-accent transition-colors cursor-pointer';
  const hasPrimers = primerRows(spec).length > 0;
  const hasAmplicon = spec.pairs.some(pair => !!pair.amplicon);

  return (
    <div
      data-testid="primer-design-viewer"
      data-mode={spec.mode}
      data-pairs={spec.pairs.length}
      className="w-full rounded-lg border border-border bg-bg-secondary overflow-hidden"
    >
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 px-3 py-2 border-b border-border">
        <span className="text-sm text-text-primary truncate" title={title}>{title}</span>
        <span className="px-1.5 py-0.5 rounded bg-bg-tertiary text-[11px] text-text-secondary">
          {MODE_LABELS[spec.mode] ?? (spec.mode || 'Primer design')}
        </span>
        <span className="text-[11px] text-text-secondary">
          {[
            template.name,
            template.length === null ? '' : `${template.length} bp`,
            template.circular ? 'circular' : 'linear',
            spec.target ? `target ${spec.target[0]}-${spec.target[1]}` : '',
          ].filter(Boolean).join(' · ')}
        </span>
      </div>

      {summary && (
        <div data-testid="primer-summary" data-usable={summary.usable === null ? 'unknown' : String(summary.usable)} className="flex flex-wrap items-center gap-2 px-3 py-2 text-xs border-b border-border">
          {summary.usable !== null && (
            <span className={`px-2 py-0.5 rounded font-medium ${summary.usable ? 'bg-success/15 text-success' : 'bg-error/15 text-error'}`}>
              {summary.usable ? 'Usable' : 'Not usable'}
            </span>
          )}
          <span className="text-text-secondary">{counts.passed} of {counts.total} checks passed</span>
          {counts.hard > 0 && <span className="px-1.5 py-0.5 rounded bg-error/15 text-error">{counts.hard} hard</span>}
          {counts.soft > 0 && <span className="px-1.5 py-0.5 rounded bg-warning/15 text-warning">{counts.soft} soft</span>}
          {counts.notComputed > 0 && (
            <span className="px-1.5 py-0.5 rounded border border-dashed border-border-input text-text-secondary">
              {counts.notComputed} not computed
            </span>
          )}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 px-3 py-2">
        <span className="text-[11px] text-text-secondary">Download</span>
        <button type="button" className={buttonClass} disabled={!hasPrimers} onClick={() => download('csv')}>Primers (CSV)</button>
        <button type="button" className={buttonClass} disabled={!hasPrimers} onClick={() => download('fasta')}>Primers (FASTA)</button>
        {hasAmplicon && (
          <button type="button" className={buttonClass} onClick={() => download('amplicons')}>Amplicons (FASTA)</button>
        )}
      </div>

      {spec.pairs.length === 0
        ? <p className="px-3 py-3 text-xs text-text-secondary border-t border-border">This design returned no primer pairs.</p>
        : spec.pairs.map((pair, i) => <PairCard key={`${pair.name}-${i}`} pair={pair} spec={spec} />)}

      <p className="px-3 py-2 text-[11px] text-text-secondary border-t border-border">
        Lowercase is the 5&apos; tail, which does not anneal. Uppercase is the annealing region.
        {spec.target && ' The shaded band on the map is the target region.'}
      </p>

      {spec.notes.length > 0 && (
        <ul data-testid="primer-notes" className="px-3 pb-2 space-y-1 text-[11px] text-text-secondary">
          {spec.notes.map((note, i) => <li key={i}>{note}</li>)}
        </ul>
      )}
    </div>
  );
}
