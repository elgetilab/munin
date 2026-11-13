import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PrimerDesignViewer } from './PrimerDesignViewer';
import {
  ampliconsFasta,
  parsePrimerDesignSpec,
  primersCsv,
  primersFasta,
  type PrimerDesignSpec,
} from '../lib/primerDesign';

// The fixtures are the plan's verified goldens: M13 forward(-21) x M13/pUC
// reverse on pUC19 gives 123 bp, and iGEM VF2 x VR gives 845 bp and wraps the
// origin, which is the case that breaks a flat-string implementation.
const AMPLICON = 'ACGT'.repeat(30) + 'ACG';

const CHECKS = [
  {
    rule: 'tm_range', label: 'Tm within 57-63 C', severity: 'soft', passed: true,
    value: 60.2, threshold: '57-63 C', source: 'Dieffenbach 1993', detail: '',
  },
  {
    rule: 'gc_clamp', label: 'GC clamp at the 3 prime end', severity: 'hard', passed: false,
    value: 'no G or C', threshold: 'at least one', source: 'Innis and Gelfand 1990',
    detail: 'the last five bases are A or T',
  },
  {
    rule: 'specificity', label: 'Genome specificity', severity: 'hard', passed: null,
    value: null, threshold: '1 hit', source: 'Ye et al. 2012',
    detail: 'not computed: no BLAST database is available',
  },
];

const RAW = {
  kind: 'primer_design',
  version: 1,
  mode: 'pcr',
  template: { name: 'pUC19', length: 2686, circular: true },
  target: [400, 450],
  pairs: [
    {
      name: 'pair_1',
      forward: {
        sequence: 'TGTAAAACGACGGCCAGT', display: 'tgtaaAACGACGGCCAGT',
        anneal: 'AACGACGGCCAGT', tail: 'tgtaa', strand: '+',
        start: 377, end: 395, length: 18, tm: 60.2, gc_percent: 50.0,
      },
      reverse: {
        sequence: 'CAGGAAACAGCTATGAC', display: 'CAGGAAACAGCTATGAC',
        anneal: 'CAGGAAACAGCTATGAC', tail: '', strand: '-',
        start: 483, end: 500, length: 17, tm: 58.1, gc_percent: 47.1,
      },
      product_size: 123,
      amplicon: AMPLICON,
      tm_difference: 2.1,
      penalty: 2.58,
      checks: CHECKS,
      verified: { outcome: 'single_product', product_count: 1, product_sizes: [123], single_clean_product: true },
    },
    {
      name: 'pair_2',
      forward: {
        sequence: 'TGCCACCTGACGTCTAAGAA', display: 'TGCCACCTGACGTCTAAGAA',
        anneal: 'TGCCACCTGACGTCTAAGAA', tail: '', strand: '+',
        start: 2600, end: 2620, length: 20, tm: 59.4, gc_percent: 50.0,
      },
      reverse: {
        sequence: 'ATTACCGCCTTTGAGTGAGC', display: 'ATTACCGCCTTTGAGTGAGC',
        anneal: 'ATTACCGCCTTTGAGTGAGC', tail: '', strand: '-',
        start: 739, end: 759, length: 20, tm: 61.0, gc_percent: 50.0,
      },
      product_size: 845,
      amplicon: '',
      tm_difference: 1.6,
      penalty: 4.1,
      checks: [CHECKS[0]],
      verified: { outcome: 'multiple_products', product_count: 2, product_sizes: [845, 183], single_clean_product: false },
    },
  ],
  summary: { total: 4, passed: 2, hard_failures: 1, soft_failures: 0, not_computed: 1, usable: false },
  notes: ['No reference database, so specificity was not checked.'],
};

function parsed(raw: unknown = RAW): PrimerDesignSpec {
  const spec = parsePrimerDesignSpec(JSON.stringify(raw));
  if (!spec) throw new Error('the fixture did not parse');
  return spec;
}

function renderViewer(raw: unknown = RAW) {
  return render(<PrimerDesignViewer spec={parsed(raw)} title="Primers for pUC19 MCS" />);
}

// jsdom has no object URLs and does not follow a click on a link, so this
// stands in for the browser's side of saving a file (as ArtifactPanel.test does).
function captureDownloads() {
  const saved: { name: string; blob: Blob }[] = [];
  let last: Blob;
  URL.createObjectURL = vi.fn((blob: Blob) => { last = blob; return 'blob:test'; });
  URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    saved.push({ name: this.download, blob: last });
  });
  return saved;
}

describe('PrimerDesignViewer', () => {
  afterEach(() => vi.restoreAllMocks());

  it('renders both pairs of a two-pair spec, with their sizes', () => {
    renderViewer();
    const pairs = screen.getAllByTestId('primer-pair');
    expect(pairs).toHaveLength(2);
    expect(pairs[0]).toHaveAttribute('data-name', 'pair_1');
    expect(pairs[0]).toHaveTextContent('123 bp product');
    expect(pairs[1]).toHaveTextContent('845 bp product');
    expect(screen.getByTestId('primer-design-viewer')).toHaveAttribute('data-pairs', '2');
  });

  it('shows the template, the mode and the target region', () => {
    renderViewer();
    const viewer = screen.getByTestId('primer-design-viewer');
    expect(viewer).toHaveAttribute('data-mode', 'pcr');
    expect(viewer).toHaveTextContent('PCR');
    expect(viewer).toHaveTextContent('pUC19 · 2686 bp · circular · target 400-450');
  });

  it('shows Tm, GC, length and the Tm difference of a pair', () => {
    renderViewer();
    const pair = screen.getAllByTestId('primer-pair')[0];
    const forward = within(pair).getAllByTestId('primer-oligo')[0];
    expect(forward).toHaveTextContent('60.2 C');
    expect(forward).toHaveTextContent('50.0%');
    expect(forward).toHaveTextContent('18 nt');
    expect(pair).toHaveTextContent('2.10 C');
    expect(pair).toHaveTextContent('penalty 2.58');
  });

  it('draws the lowercase tail and the uppercase annealing region as different parts', () => {
    renderViewer();
    const display = screen.getAllByTestId('primer-display')[0];
    const tail = display.querySelector('[data-part="tail"]')!;
    const anneal = display.querySelector('[data-part="anneal"]')!;
    expect(tail).toHaveTextContent('tgtaa');
    expect(anneal).toHaveTextContent('AACGACGGCCAGT');
    expect(tail.className).not.toBe(anneal.className);
    expect(display).toHaveTextContent('tgtaaAACGACGGCCAGT');
    // A primer with no tail is one uppercase run, not an empty element.
    const plain = screen.getAllByTestId('primer-display')[1];
    expect(plain.querySelectorAll('[data-part="tail"]')).toHaveLength(0);
    expect(plain).toHaveTextContent('CAGGAAACAGCTATGAC');
  });

  it('separates a failed check from one that was not computed', () => {
    renderViewer();
    const checks = within(screen.getAllByTestId('primer-pair')[0]).getAllByTestId('primer-check');
    const byRule = Object.fromEntries(checks.map(c => [c.getAttribute('data-rule'), c]));

    expect(byRule.tm_range).toHaveAttribute('data-status', 'pass');
    expect(byRule.gc_clamp).toHaveAttribute('data-status', 'fail');
    expect(byRule.specificity).toHaveAttribute('data-status', 'not-computed');

    const pill = (row: HTMLElement) => row.firstElementChild!;
    expect(pill(byRule.gc_clamp)).toHaveTextContent('Fail');
    expect(pill(byRule.gc_clamp).className).toContain('text-error');
    expect(pill(byRule.specificity)).toHaveTextContent('Not computed');
    expect(pill(byRule.specificity).className).toContain('border-dashed');
    expect(pill(byRule.specificity).className).not.toContain('text-success');
    expect(pill(byRule.tm_range).className).toContain('text-success');
  });

  it('shows the severity and the source of every check', () => {
    renderViewer();
    const row = within(screen.getAllByTestId('primer-pair')[0])
      .getAllByTestId('primer-check')
      .find(c => c.getAttribute('data-rule') === 'gc_clamp')!;
    expect(row).toHaveAttribute('data-severity', 'hard');
    expect(row).toHaveTextContent('(hard)');
    expect(row).toHaveTextContent('Innis and Gelfand 1990');
    expect(row).toHaveTextContent('the last five bases are A or T');
  });

  it('opens the checks when something failed and keeps them closed when all passed', () => {
    const allPass = {
      ...RAW,
      pairs: [{ ...RAW.pairs[0], checks: [CHECKS[0]] }],
    };
    const { unmount } = renderViewer();
    expect(screen.getAllByTestId('primer-checks')[0]).toHaveAttribute('open');
    unmount();
    renderViewer(allPass);
    expect(screen.getByTestId('primer-checks')).not.toHaveAttribute('open');
    expect(screen.getByTestId('primer-checks')).toHaveTextContent('1 of 1 checks passed');
  });

  it('leads with the verified block, and says when a pair is not a single product', () => {
    renderViewer();
    const [first, second] = screen.getAllByTestId('primer-verified');
    expect(first).toHaveAttribute('data-clean', 'true');
    expect(first).toHaveTextContent('Single clean product');
    expect(first).toHaveTextContent('1 in-silico product');
    expect(first).toHaveTextContent('123 bp');
    expect(second).toHaveAttribute('data-clean', 'false');
    expect(second).toHaveTextContent('Not a single clean product');
    expect(second).toHaveTextContent('845, 183 bp');
    expect(second).toHaveTextContent('outcome: multiple_products');
  });

  it('marks an unverified pair as unverified rather than clean', () => {
    renderViewer({ ...RAW, pairs: [{ ...RAW.pairs[0], verified: undefined }] });
    const verified = screen.getByTestId('primer-verified');
    expect(verified).toHaveAttribute('data-clean', 'unknown');
    expect(verified).toHaveTextContent('Not verified');
    expect(verified.firstElementChild!.className).toContain('border-dashed');
  });

  it('never counts a check that was not computed as a check that passed', () => {
    // The tool gives a not-computed check passed: true and says so in `detail`,
    // and its own summary.passed counts it (backend rules.summarise). The
    // header shows the number that leaves them out instead.
    renderViewer({
      ...RAW,
      pairs: [{ ...RAW.pairs[0], checks: [CHECKS[0], CHECKS[2]] }],
      summary: { total: 2, passed: 2, hard_failures: 0, soft_failures: 0, not_computed: 1, usable: true },
    });
    const summary = screen.getByTestId('primer-summary');
    expect(summary).toHaveTextContent('1 of 2 checks passed');
    expect(summary).toHaveTextContent('1 not computed');
    expect(summary).toHaveAttribute('data-usable', 'true');
    // The same convention inside the pair.
    expect(screen.getByTestId('primer-checks')).toHaveTextContent('1 of 2 checks passed');
  });

  it('reads "not computed" from the detail even when the check says it passed', () => {
    renderViewer({
      ...RAW,
      pairs: [{
        ...RAW.pairs[0],
        checks: [{ ...CHECKS[0], rule: 'hairpin_tm', passed: true, value: null, detail: 'not computed' }],
      }],
    });
    expect(screen.getByTestId('primer-check')).toHaveAttribute('data-status', 'not-computed');
  });

  it('shows the overall summary and the notes', () => {
    renderViewer();
    const summary = screen.getByTestId('primer-summary');
    expect(summary).toHaveAttribute('data-usable', 'false');
    expect(summary).toHaveTextContent('Not usable');
    expect(summary).toHaveTextContent('2 of 4 checks passed');
    expect(summary).toHaveTextContent('1 hard');
    expect(summary).toHaveTextContent('1 not computed');
    expect(screen.getByTestId('primer-notes')).toHaveTextContent('No reference database');
  });

  describe('the template map', () => {
    it('draws the primers, the amplicon and the target on the template', () => {
      renderViewer();
      const map = within(screen.getAllByTestId('primer-pair')[0]).getAllByTestId('primer-map')[0];
      expect(within(map).getByTestId('primer-arrow-forward')).toBeInTheDocument();
      expect(within(map).getByTestId('primer-arrow-reverse')).toBeInTheDocument();
      expect(within(map).getAllByTestId('primer-map-amplicon')).toHaveLength(1);
      expect(within(map).getAllByTestId('primer-map-target').length).toBeGreaterThan(0);
      expect(map).toHaveAttribute('data-wrapped', 'false');
    });

    it('draws an origin-wrapping amplicon as two pieces, with no broken geometry', () => {
      renderViewer();
      const map = within(screen.getAllByTestId('primer-pair')[1]).getAllByTestId('primer-map')[0];
      expect(map).toHaveAttribute('data-wrapped', 'true');
      expect(within(map).getAllByTestId('primer-map-amplicon')).toHaveLength(2);
      expect(within(map).getByTestId('primer-map-wrap-note')).toHaveTextContent('wraps the origin');
      for (const rect of within(map).getAllByTestId('primer-map-amplicon')) {
        expect(Number(rect.getAttribute('x'))).toBeGreaterThanOrEqual(0);
        expect(Number(rect.getAttribute('width'))).toBeGreaterThan(0);
      }
      expect(map.querySelector('svg')!.outerHTML).not.toMatch(/NaN|Infinity|undefined/);
    });

    it('draws the template stretch rather than the product when a primer has a tail', () => {
      // The 5' tail is in the product but nowhere in the template, so the map
      // draws forward.start to reverse.end and the caption says the rest is tail.
      renderViewer({
        ...RAW,
        pairs: [{ ...RAW.pairs[0], product_size: 163 }],
      });
      const map = screen.getAllByTestId('primer-map')[0];
      expect(map).toHaveTextContent('amplicon 377-500');
      expect(map).toHaveTextContent("123 bp of it on the template, the rest is 5' tail");
    });

    it('adds a detail map when the amplicon is a sliver of the template', () => {
      renderViewer();
      // 123 bp in 2686 bp is too small to read, so the overview is followed by a
      // zoom on the amplicon's neighbourhood.
      const maps = within(screen.getAllByTestId('primer-pair')[0]).getAllByTestId('primer-map');
      expect(maps).toHaveLength(2);
      expect(maps[1]).toHaveTextContent('Detail');
    });

    it('says so instead of drawing when the template length is missing', () => {
      renderViewer({ ...RAW, template: { name: 'unknown', circular: false } });
      expect(screen.queryAllByTestId('primer-map')).toHaveLength(0);
      expect(screen.getAllByTestId('primer-pair')[0]).toHaveTextContent('does not give the template length');
    });

    it('keeps the geometry sound on a linear template', () => {
      const linear = {
        ...RAW,
        template: { name: 'gBlock', length: 900, circular: false },
        pairs: [RAW.pairs[0]],
      };
      renderViewer(linear);
      const map = screen.getAllByTestId('primer-map')[0];
      expect(map).toHaveAttribute('data-wrapped', 'false');
      expect(within(map).getAllByTestId('primer-map-amplicon')).toHaveLength(1);
      expect(map.querySelector('svg')!.outerHTML).not.toMatch(/NaN|-\d+\.?\d*"/);
    });
  });

  describe('downloads', () => {
    it('writes a primer table a vendor form can take', async () => {
      const saved = captureDownloads();
      renderViewer();
      await userEvent.click(screen.getByRole('button', { name: 'Primers (CSV)' }));
      expect(saved).toHaveLength(1);
      expect(saved[0].name).toBe('pUC19_primers.csv');
      expect(await saved[0].blob.text()).toBe([
        'name,sequence,length,tm,gc_percent,strand,start,end,pair,product_size',
        'pair_1_F,TGTAAAACGACGGCCAGT,18,60.2,50,+,377,395,pair_1,123',
        'pair_1_R,CAGGAAACAGCTATGAC,17,58.1,47.1,-,483,500,pair_1,123',
        'pair_2_F,TGCCACCTGACGTCTAAGAA,20,59.4,50,+,2600,2620,pair_2,845',
        'pair_2_R,ATTACCGCCTTTGAGTGAGC,20,61,50,-,739,759,pair_2,845',
        '',
      ].join('\n'));
    });

    it('writes the primers as FASTA, with the ordered sequence including the tail', async () => {
      const saved = captureDownloads();
      renderViewer();
      await userEvent.click(screen.getByRole('button', { name: 'Primers (FASTA)' }));
      expect(saved[0].name).toBe('pUC19_primers.fasta');
      const text = await saved[0].blob.text();
      expect(text.split('\n').slice(0, 4)).toEqual([
        '>pair_1_F strand=+ 377-395 len=18 Tm=60.2 GC=50 template=pUC19',
        'TGTAAAACGACGGCCAGT',
        '>pair_1_R strand=- 483-500 len=17 Tm=58.1 GC=47.1 template=pUC19',
        'CAGGAAACAGCTATGAC',
      ]);
      expect(text.endsWith('\n')).toBe(true);
      expect(text.match(/^>/gm)).toHaveLength(4);
    });

    it('writes the amplicon as FASTA, wrapped at 60 columns', async () => {
      const saved = captureDownloads();
      renderViewer();
      await userEvent.click(screen.getByRole('button', { name: 'Amplicons (FASTA)' }));
      expect(saved[0].name).toBe('pUC19_amplicons.fasta');
      const lines = (await saved[0].blob.text()).trimEnd().split('\n');
      expect(lines[0]).toBe('>pair_1_amplicon 123 bp template=pUC19');
      expect(lines.slice(1).map(l => l.length)).toEqual([60, 60, 3]);
      expect(lines.slice(1).join('')).toBe(AMPLICON);
    });

    it('says in the file when the tool truncated the amplicon', () => {
      const spec = parsed({ ...RAW, pairs: [{ ...RAW.pairs[0], amplicon: 'ACGTACGTAC', product_size: 4123 }] });
      expect(ampliconsFasta(spec).split('\n')[0])
        .toBe('>pair_1_amplicon 4123 bp TRUNCATED to 10 bp in this view template=pUC19');
    });

    it('offers no amplicon download when no pair carries one', () => {
      renderViewer({ ...RAW, pairs: [{ ...RAW.pairs[0], amplicon: '' }] });
      expect(screen.queryByRole('button', { name: 'Amplicons (FASTA)' })).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Primers (CSV)' })).toBeEnabled();
    });

    it('quotes a field that would otherwise break the table', () => {
      const spec = parsed({ ...RAW, pairs: [{ ...RAW.pairs[0], name: 'pair,"one"' }] });
      expect(primersCsv(spec).split('\n')[1]).toMatch(/^"pair,""one""_F"/);
    });

    it('builds the files from the spec alone, so they can be checked without the DOM', () => {
      const spec = parsed();
      expect(primersCsv(spec).split('\n')).toHaveLength(6);
      expect(primersFasta(spec).match(/^>/gm)).toHaveLength(4);
      expect(ampliconsFasta(spec).match(/^>/gm)).toHaveLength(1);
    });
  });

  describe('a spec it cannot use', () => {
    it('refuses what is not a primer design, and what is not JSON', () => {
      expect(parsePrimerDesignSpec('not json')).toBeNull();
      expect(parsePrimerDesignSpec(JSON.stringify({ widget: 'protein', source: 'pdb', id: '1CRN' }))).toBeNull();
      expect(parsePrimerDesignSpec(JSON.stringify([1, 2]))).toBeNull();
    });

    it('refuses a version it does not know, rather than guessing at it', () => {
      expect(parsePrimerDesignSpec(JSON.stringify({ ...RAW, version: 2 }))).toBeNull();
      expect(parsePrimerDesignSpec(JSON.stringify({ ...RAW, version: '1' }))).toBeNull();
      expect(parsePrimerDesignSpec(JSON.stringify({ ...RAW, version: 1 }))).not.toBeNull();
    });

    it('drops the parts of a pair it cannot read and still renders', () => {
      renderViewer({
        kind: 'primer_design',
        version: 1,
        mode: 'pcr',
        template: { name: 'pUC19', length: 2686, circular: true },
        target: 'the middle',
        pairs: [
          'nonsense',
          { name: 'pair_1', forward: { sequence: 'ACGTACGTACGTACGTAC' }, checks: ['x', { label: 'Tm' }] },
          { name: 'empty', forward: null, reverse: null },
        ],
        summary: 'fine',
        notes: 'careful',
      });
      const pairs = screen.getAllByTestId('primer-pair');
      expect(pairs).toHaveLength(1);
      expect(pairs[0]).toHaveTextContent('ACGTACGTACGTACGTAC');
      expect(within(pairs[0]).getAllByTestId('primer-check')).toHaveLength(1);
      expect(within(pairs[0]).getByTestId('primer-check')).toHaveAttribute('data-status', 'not-computed');
      expect(screen.queryByTestId('primer-summary')).not.toBeInTheDocument();
      expect(screen.queryByTestId('primer-notes')).not.toBeInTheDocument();
      // No target and no coordinates, so there is nothing to draw on the map.
      expect(screen.queryAllByTestId('primer-map-target')).toHaveLength(0);
    });

    it('says a design with no pairs found nothing, and offers no empty download', () => {
      renderViewer({ ...RAW, pairs: [] });
      expect(screen.getByTestId('primer-design-viewer')).toHaveTextContent('returned no primer pairs');
      expect(screen.getByRole('button', { name: 'Primers (CSV)' })).toBeDisabled();
    });
  });

  it('takes its colours from the theme, so it follows light and dark', () => {
    const { container } = renderViewer();
    // Tokens, never a literal colour: the SVG reads the same variables the rest
    // of the UI does, which the light theme redefines.
    expect(container.innerHTML).not.toMatch(/#[0-9a-fA-F]{3,8}["' ]/);
    expect(container.innerHTML).toContain('var(--color-accent)');
    expect(container.innerHTML).toContain('var(--color-text-secondary)');
  });
});
