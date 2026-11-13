// The `design_primers` result spec, and everything about it that is not a
// component: parsing a stored artifact, the files the user can take away, and
// the geometry the template map is drawn from.
//
// Nothing here trusts the spec. The model can rewrite an artifact body with
// update_artifact, so a field that is not what it should be is dropped and the
// widget says what it does not have, the way parseProteinSpec does.

export const PRIMER_DESIGN_KIND = 'primer_design';
export const PRIMER_DESIGN_VERSION = 1;

export interface PrimerOligo {
  // The full oligo as it would be ordered, tail included.
  sequence: string;
  // The same bases with the tail in lowercase and the annealing part in
  // uppercase. Falls back to `sequence` when the tool did not send it.
  display: string;
  anneal: string;
  tail: string;
  strand: string;
  start: number | null;
  end: number | null;
  length: number | null;
  tm: number | null;
  gc: number | null;
}

export type CheckStatus = 'pass' | 'fail' | 'not-computed';

export interface PrimerCheck {
  rule: string;
  label: string;
  severity: string;
  status: CheckStatus;
  value: string;
  threshold: string;
  source: string;
  detail: string;
}

export interface PrimerVerified {
  outcome: string;
  productCount: number | null;
  productSizes: number[];
  singleCleanProduct: boolean | null;
}

export interface PrimerPair {
  name: string;
  forward: PrimerOligo | null;
  reverse: PrimerOligo | null;
  productSize: number | null;
  amplicon: string;
  tmDifference: number | null;
  penalty: number | null;
  checks: PrimerCheck[];
  verified: PrimerVerified | null;
}

export interface PrimerDesignSpec {
  kind: typeof PRIMER_DESIGN_KIND;
  mode: string;
  template: { name: string; length: number | null; circular: boolean };
  target: [number, number] | null;
  pairs: PrimerPair[];
  summary: {
    total: number | null;
    passed: number | null;
    hardFailures: number | null;
    softFailures: number | null;
    notComputed: number | null;
    usable: boolean | null;
  } | null;
  notes: string[];
}

// --- parsing ---------------------------------------------------------------
// The model can rewrite an artifact with update_artifact, so nothing here is
// trusted: a field that is not what it should be is dropped, and the widget
// says what it does not have rather than throwing inside a message.

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function str(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function scalar(value: unknown): string {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  return '';
}

function parseOligo(value: unknown): PrimerOligo | null {
  if (!isRecord(value)) return null;
  const sequence = str(value.sequence) || str(value.display).toUpperCase();
  const display = str(value.display) || sequence;
  if (!sequence) return null;
  return {
    sequence,
    display,
    anneal: str(value.anneal),
    tail: str(value.tail),
    strand: str(value.strand),
    start: num(value.start),
    end: num(value.end),
    length: num(value.length) ?? sequence.length,
    tm: num(value.tm),
    gc: num(value.gc_percent),
  };
}

// A check is "not computed" whenever the tool could not evaluate it. That is a
// different fact from passing, and the two must never look alike, so anything
// uncertain lands here rather than in the pass column.
//
// `computed` is the authoritative signal: the backend sets it to false on a
// rule whose input was never supplied, while still sending passed: true so the
// check cannot disqualify a primer. The detail-string test is kept only as a
// fallback for a spec produced before that field existed; it must never be the
// primary signal, because wording drifts and a drifted string would turn a
// silently-unchecked rule into a green pass.
function checkStatus(passed: unknown, detail: string,
                     computed?: unknown): CheckStatus {
  if (computed === false) return 'not-computed';
  if (/^not[ _-]?computed/i.test(detail.trim())) return 'not-computed';
  if (passed === true) return 'pass';
  if (passed === false) return 'fail';
  return 'not-computed';
}

function parseCheck(value: unknown): PrimerCheck | null {
  if (!isRecord(value)) return null;
  const rule = str(value.rule);
  const label = str(value.label) || rule;
  if (!label) return null;
  const detail = str(value.detail);
  const severity = value.severity === 'hard' || value.severity === 'soft' ? value.severity : '';
  return {
    rule,
    label,
    severity,
    status: checkStatus(value.passed, detail, value.computed),
    value: scalar(value.value),
    threshold: scalar(value.threshold),
    source: str(value.source),
    detail,
  };
}

function parseVerified(value: unknown): PrimerVerified | null {
  if (!isRecord(value)) return null;
  const sizes = Array.isArray(value.product_sizes)
    ? value.product_sizes.filter((s: unknown): s is number => typeof s === 'number' && Number.isFinite(s))
    : [];
  return {
    outcome: str(value.outcome),
    productCount: num(value.product_count),
    productSizes: sizes,
    singleCleanProduct: typeof value.single_clean_product === 'boolean' ? value.single_clean_product : null,
  };
}

function parsePair(value: unknown, index: number): PrimerPair[] {
  if (!isRecord(value)) return [];
  const forward = parseOligo(value.forward);
  const reverse = parseOligo(value.reverse);
  if (!forward && !reverse) return [];
  return [{
    name: str(value.name) || `pair_${index + 1}`,
    forward,
    reverse,
    productSize: num(value.product_size),
    amplicon: str(value.amplicon),
    tmDifference: num(value.tm_difference),
    penalty: num(value.penalty),
    checks: (Array.isArray(value.checks) ? value.checks : []).flatMap(c => parseCheck(c) ?? []),
    verified: parseVerified(value.verified),
  }];
}

function parseTarget(value: unknown): [number, number] | null {
  if (!Array.isArray(value) || value.length < 2) return null;
  const from = num(value[0]);
  const to = num(value[1]);
  return from === null || to === null ? null : [from, to];
}

function parseSummary(value: unknown): PrimerDesignSpec['summary'] {
  if (!isRecord(value)) return null;
  return {
    total: num(value.total),
    passed: num(value.passed),
    hardFailures: num(value.hard_failures),
    softFailures: num(value.soft_failures),
    notComputed: num(value.not_computed),
    usable: typeof value.usable === 'boolean' ? value.usable : null,
  };
}

/**
 * The spec a stored artifact holds, or null when this widget cannot show it:
 * unparseable, another kind, or a version this build does not know. A null
 * sends the caller back to the plain artifact card, which is what the protein
 * widget does with a spec it cannot use.
 */
export function parsePrimerDesignSpec(content: string): PrimerDesignSpec | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(content);
  } catch {
    return null;
  }
  if (!isRecord(parsed) || parsed.kind !== PRIMER_DESIGN_KIND) return null;
  if (parsed.version !== undefined && parsed.version !== PRIMER_DESIGN_VERSION) return null;
  const template = isRecord(parsed.template) ? parsed.template : {};
  return {
    kind: PRIMER_DESIGN_KIND,
    mode: str(parsed.mode),
    template: {
      name: str(template.name),
      length: num(template.length),
      circular: template.circular === true,
    },
    target: parseTarget(parsed.target),
    pairs: (Array.isArray(parsed.pairs) ? parsed.pairs : []).flatMap(parsePair),
    summary: parseSummary(parsed.summary),
    notes: (Array.isArray(parsed.notes) ? parsed.notes : []).filter((n: unknown): n is string => typeof n === 'string'),
  };
}

// --- downloads -------------------------------------------------------------

export interface PrimerRow {
  name: string;
  sequence: string;
  length: number | null;
  tm: number | null;
  gc: number | null;
  strand: string;
  start: number | null;
  end: number | null;
  pair: string;
  productSize: number | null;
}

export function decimals(value: number | null, digits: number): string {
  return value === null ? '' : value.toFixed(digits);
}

// For a file rather than the screen: the number the tool reported, with no
// precision added to it and none taken away.
function plain(value: number | null): string {
  return value === null ? '' : String(value);
}

/** One row per oligo. The contract names pairs, not oligos, so the oligo names
 *  are the pair's with _F and _R, which is also how they get ordered. */
export function primerRows(spec: PrimerDesignSpec): PrimerRow[] {
  return spec.pairs.flatMap(pair => ([
    [pair.forward, 'F'] as const,
    [pair.reverse, 'R'] as const,
  ].flatMap(([oligo, suffix]) => (oligo ? [{
    name: `${pair.name}_${suffix}`,
    sequence: oligo.sequence,
    length: oligo.length,
    tm: oligo.tm,
    gc: oligo.gc,
    strand: oligo.strand,
    start: oligo.start,
    end: oligo.end,
    pair: pair.name,
    productSize: pair.productSize,
  }] : []))));
}

const CSV_COLUMNS = ['name', 'sequence', 'length', 'tm', 'gc_percent', 'strand', 'start', 'end', 'pair', 'product_size'];

function csvField(value: string): string {
  return /[",\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

/** The primer table, for a vendor order form. `sequence` is the whole oligo,
 *  tail included, which is what gets ordered, and start/end are where it
 *  anneals. */
export function primersCsv(spec: PrimerDesignSpec): string {
  const lines = [CSV_COLUMNS.join(',')];
  for (const row of primerRows(spec)) {
    lines.push([
      row.name,
      row.sequence,
      plain(row.length),
      plain(row.tm),
      plain(row.gc),
      row.strand,
      plain(row.start),
      plain(row.end),
      row.pair,
      plain(row.productSize),
    ].map(csvField).join(','));
  }
  return `${lines.join('\n')}\n`;
}

function wrap(sequence: string, width = 60): string {
  const lines = [];
  for (let i = 0; i < sequence.length; i += width) lines.push(sequence.slice(i, i + width));
  return lines.join('\n');
}

function fastaRecord(header: string, sequence: string): string {
  return `>${header}\n${wrap(sequence)}\n`;
}

export function primersFasta(spec: PrimerDesignSpec): string {
  const template = spec.template.name;
  return primerRows(spec).map(row => {
    const parts = [
      row.strand ? `strand=${row.strand}` : '',
      row.start !== null && row.end !== null ? `${row.start}-${row.end}` : '',
      row.length !== null ? `len=${row.length}` : '',
      row.tm !== null ? `Tm=${plain(row.tm)}` : '',
      row.gc !== null ? `GC=${plain(row.gc)}` : '',
      template ? `template=${template}` : '',
    ].filter(Boolean);
    return fastaRecord([row.name, ...parts].join(' '), row.sequence);
  }).join('');
}

export function ampliconsFasta(spec: PrimerDesignSpec): string {
  const template = spec.template.name;
  return spec.pairs.flatMap(pair => {
    if (!pair.amplicon) return [];
    // The tool truncates a long amplicon in the spec and says so in `notes`.
    // The file says so too, where the note is no longer alongside it.
    const size = pair.productSize ?? pair.amplicon.length;
    const truncated = size > pair.amplicon.length;
    const parts = [
      `${size} bp`,
      truncated ? `TRUNCATED to ${pair.amplicon.length} bp in this view` : '',
      template ? `template=${template}` : '',
    ].filter(Boolean);
    return [fastaRecord([`${pair.name}_amplicon`, ...parts].join(' '), pair.amplicon)];
  }).join('');
}

function slug(name: string): string {
  const cleaned = name.replace(/[^A-Za-z0-9._-]+/g, '_').replace(/^_+|_+$/g, '');
  return cleaned || 'primers';
}

export type DownloadKind = 'csv' | 'fasta' | 'amplicons';

export function buildDownload(spec: PrimerDesignSpec, kind: DownloadKind): { filename: string; type: string; text: string } {
  const base = slug(spec.template.name || 'primers');
  if (kind === 'csv') return { filename: `${base}_primers.csv`, type: 'text/csv;charset=utf-8', text: primersCsv(spec) };
  if (kind === 'fasta') return { filename: `${base}_primers.fasta`, type: 'text/plain;charset=utf-8', text: primersFasta(spec) };
  return { filename: `${base}_amplicons.fasta`, type: 'text/plain;charset=utf-8', text: ampliconsFasta(spec) };
}

export interface Span {
  start: number;
  span: number;
}

export interface MapWindow {
  from: number;
  span: number;
}

export function modulo(value: number, length: number): number {
  return ((value % length) + length) % length;
}

/**
 * Where an oligo anneals on the template, which is `start` to `end`: a 5' tail
 * is not in the template and has no coordinates, so `length` (the whole oligo)
 * is only the fallback. `end` before `start` is an origin-wrapping footprint
 * on a circular template, not an error.
 */
export function oligoSpan(oligo: PrimerOligo | null, length: number | null): Span | null {
  if (!oligo || oligo.start === null) return null;
  let span = oligo.length;
  if (oligo.end !== null) {
    let measured = oligo.end - oligo.start;
    if (measured < 0 && length) measured += length;
    if (measured > 0) span = measured;
  }
  if (span === null || span <= 0) return null;
  return { start: oligo.start, span };
}

/**
 * The part of the template the product comes from: the forward primer's first
 * annealing base to the reverse primer's last. The coordinates come first and
 * product_size only stands in for them, because a 5' tail is part of the
 * product but is not anywhere on the template, so for a cloning primer the
 * product is longer than the template stretch drawn here. A reverse end that
 * sits before the forward start is an origin-wrapping product on a circular
 * template, so the template length is added rather than the span dropped.
 */
export function ampliconSpan(pair: PrimerPair, length: number | null): Span | null {
  // A sequencing design is one primer and no product at all: product_size is 0
  // and amplicon is "". Drawing a bar there would invent a reaction.
  if (pair.productSize === 0) return null;
  const forward = oligoSpan(pair.forward, length);
  const reverse = oligoSpan(pair.reverse, length);
  if (forward && reverse) {
    let span = reverse.start + reverse.span - forward.start;
    if (span <= 0 && length) span += length;
    if (span > 0) return { start: forward.start, span };
  }
  const size = pair.productSize ?? (pair.amplicon ? pair.amplicon.length : null);
  const single = forward ?? reverse;
  if (single && size && size > 0) return { start: single.start, span: Math.min(size, length ?? size) };
  return null;
}

/** What the detail map zooms on: the product when there is one, and otherwise
 *  the primers themselves, so a lone sequencing primer is still readable. */
export function focusSpan(pair: PrimerPair, length: number | null): Span | null {
  const amplicon = ampliconSpan(pair, length);
  if (amplicon) return amplicon;
  const forward = oligoSpan(pair.forward, length);
  const reverse = oligoSpan(pair.reverse, length);
  if (!forward) return reverse;
  if (!reverse) return forward;
  let span = reverse.start + reverse.span - forward.start;
  if (span <= 0 && length) span += length;
  return span > 0 ? { start: forward.start, span } : forward;
}

// The scopes the tool namespaces a rule id with, as `scope.rule`, because a
// pair carries the same rule twice with different values (one per primer).
// These are the keys of _SCOPE_LABELS in backend/retrieval/mcp/tools/primers.py;
// the label carries the same word, lowercased, as a prefix.
const SCOPE_HEADINGS: Record<string, string> = {
  forward: 'Forward primer',
  reverse: 'Reverse primer',
  primer: 'Primer',
  pair: 'Pair',
  junction_in: 'Upstream junction',
  junction_out: 'Downstream junction',
  cloning: 'Cloning',
};

export interface CheckGroup {
  scope: string;
  heading: string;
  checks: PrimerCheck[];
}

/**
 * The checks in the order the tool sent them, gathered under their scope. A
 * rule with no known scope keeps its own group with no heading, so an id this
 * build has not seen still shows up rather than vanishing.
 */
export function checkGroups(checks: PrimerCheck[]): CheckGroup[] {
  const groups: CheckGroup[] = [];
  for (const check of checks) {
    const prefix = check.rule.includes('.') ? check.rule.slice(0, check.rule.indexOf('.')) : '';
    const scope = prefix in SCOPE_HEADINGS ? prefix : '';
    const heading = SCOPE_HEADINGS[scope] ?? '';
    const last = groups[groups.length - 1];
    if (last && last.scope === scope) last.checks.push(check);
    else groups.push({ scope, heading, checks: [check] });
  }
  return groups;
}

/** The label without the scope word the heading already carries. */
export function checkLabel(check: PrimerCheck, heading: string): string {
  const prefix = `${heading.toLowerCase()}: `;
  return heading && check.label.toLowerCase().startsWith(prefix)
    ? check.label.slice(prefix.length)
    : check.label;
}

/**
 * The check counts to show. The spec's own `summary.passed` counts a check
 * that could not be computed as a pass (the tool gives it passed: true and
 * says "not computed" in `detail`), so the number shown is the one that
 * excludes them: a reader must never be told a check passed when it never ran.
 * `usable` stays the tool's verdict.
 */
export function checkCounts(spec: PrimerDesignSpec): {
  total: number;
  passed: number;
  hard: number;
  soft: number;
  notComputed: number;
} {
  const summary = spec.summary;
  if (summary && summary.total !== null && summary.hardFailures !== null
    && summary.softFailures !== null && summary.notComputed !== null) {
    const { total, hardFailures, softFailures, notComputed } = summary;
    return {
      total,
      passed: Math.max(total - hardFailures - softFailures - notComputed, 0),
      hard: hardFailures,
      soft: softFailures,
      notComputed,
    };
  }
  const checks = spec.pairs.flatMap(pair => pair.checks);
  const failed = checks.filter(c => c.status === 'fail');
  return {
    total: checks.length,
    passed: checks.filter(c => c.status === 'pass').length,
    hard: failed.filter(c => c.severity !== 'soft').length,
    soft: failed.filter(c => c.severity === 'soft').length,
    notComputed: checks.filter(c => c.status === 'not-computed').length,
  };
}

/** A span as it falls inside one window, in window coordinates. A circular
 *  template can put it on screen twice, once at each edge, which is how an
 *  origin-wrapping amplicon draws without the geometry going negative. */
export function windowPieces(span: Span | null, win: MapWindow, length: number | null, circular: boolean): [number, number][] {
  if (!span || win.span <= 0) return [];
  const wraps = circular && !!length && length > 0;
  const offset = wraps ? modulo(span.start - win.from, length) : span.start - win.from;
  const candidates = wraps ? [offset - length, offset, offset + length] : [offset];
  const pieces: [number, number][] = [];
  for (const candidate of candidates) {
    const from = Math.max(0, candidate);
    const to = Math.min(win.span, candidate + span.span);
    if (to > from) pieces.push([from, to]);
  }
  return pieces;
}
