import { readFileSync } from 'node:fs';
import { test, expect, type Frame, type Page } from '@playwright/test';
import { installMocks } from './fixtures/mock';
import { ev, tokens } from './fixtures/sse';
import { cssVarColor } from './fixtures/theme';

// The real viewer in a real browser: 3Dmol.js, WebGL and the sandboxed frame.
// Only the network is mocked.
const WIDGET_TYPE = 'application/vnd.munin.widget+json';
const ID = 'art_protein';
const SPEC = { widget: 'protein', source: 'pdb', id: '1CRN', style: 'cartoon', color: 'spectrum', highlight: [] };

// With or without ?version=, and not the /structure route beneath it.
const ARTIFACT_URL = new RegExp(`/api/chats/conv-e2e/artifacts/${ID}(\\?|$)`);

// A short alpha helix, as C-alpha atoms.
const PDB = Array.from({ length: 20 }, (_, i) => {
  const angle = (i * 100 * Math.PI) / 180;
  const n = (v: number) => v.toFixed(3).padStart(8);
  return `ATOM  ${String(i + 1).padStart(5)}  CA  ALA A${String(i + 1).padStart(4)}    ${n(2.3 * Math.cos(angle))}${n(2.3 * Math.sin(angle))}${n(1.5 * i)}  1.00 10.00           C`;
}).join('\n');

async function openViewer(page: Page) {
  const result = { id: ID, title: '1CRN: Crambin', content_type: WIDGET_TYPE, version: 1, source: 'model_written' };
  await installMocks(page, {
    chatScript: [
      ev.conversation('conv-e2e'), ev.routing('chat'),
      ev.toolCall('show_protein', { pdb_id: '1CRN' }, 'tc-protein'),
      ev.toolResult('tc-protein', result),
      ev.artifactCreated(result),
      ...tokens('Here is crambin.'),
      ev.done(),
    ],
  });
  await page.route(ARTIFACT_URL, route => route.fulfill({
    json: { ...result, latest_version: 1, content: JSON.stringify(SPEC) },
  }));
  await page.route(`**/api/chats/conv-e2e/artifacts/${ID}/structure*`, route => route.fulfill({
    json: { format: 'pdb', data: PDB },
  }));
  await page.goto('/');
  await page.getByTestId('composer-textarea').fill('show me crambin');
  await page.getByTestId('composer-send').click();
  await expect(page.getByTestId('protein-viewer')).toHaveAttribute('data-status', 'rendered', { timeout: 20000 });
}

async function widgetFrame(page: Page): Promise<Frame> {
  const handle = await page.getByTestId('widget-frame').first().elementHandle();
  const frame = await handle!.contentFrame();
  return frame!;
}

test.describe('protein viewer', () => {
  test('the controls show keyboard focus despite outline-none', async ({ page }) => {
    await openViewer(page);
    const viewer = page.getByTestId('protein-viewer');
    await viewer.getByRole('button', { name: 'Spin' }).focus();
    await page.keyboard.press('Shift+Tab');
    const color = viewer.getByLabel('Color');
    await expect(color).toBeFocused();
    await expect(color).toHaveClass(/outline-none/);
    // The accent where the controls sit, so this holds in both themes, and if the
    // viewer keeps its own dark colours it is read from there.
    const accent = await cssVarColor(color, '--color-accent');
    await expect(color).toHaveCSS('outline', `${accent} solid 2px`);
    await page.keyboard.press('Tab');
    const spin = viewer.getByRole('button', { name: 'Spin' });
    await expect(spin).toHaveCSS('outline', `${await cssVarColor(spin, '--color-accent')} solid 2px`);
  });

  test('renders in the message and leaves the panel closed', async ({ page }) => {
    await openViewer(page);
    await expect(page.getByTestId('artifact-panel')).toBeHidden();
    await expect((await widgetFrame(page)).locator('canvas').first()).toBeVisible();
  });

  test('the controls keep it rendering, and Expand opens the panel', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', e => errors.push(e.message));
    await openViewer(page);
    const viewer = page.getByTestId('protein-viewer');
    for (const style of ['stick', 'sphere', 'line', 'cartoon']) await viewer.getByLabel('Style').selectOption(style);
    for (const color of ['chain', 'secondary', 'bfactor', 'spectrum']) await viewer.getByLabel('Color').selectOption(color);
    await viewer.getByRole('button', { name: 'Spin' }).click();
    await viewer.getByRole('button', { name: 'Stop' }).click();
    await viewer.getByRole('button', { name: 'Reset view' }).click();
    await expect(viewer).toHaveAttribute('data-status', 'rendered');

    await viewer.getByRole('button', { name: 'Expand' }).click();
    await expect(page.getByTestId('artifact-panel').getByTestId('protein-viewer')).toHaveAttribute('data-status', 'rendered', { timeout: 20000 });
    expect(errors).toEqual([]);
  });

  test('a structure that cannot load shows why, with no broken viewer', async ({ page }) => {
    await installMocks(page, {
      chatScript: [
        ev.conversation('conv-e2e'),
        ev.toolCall('show_protein', { pdb_id: '1CRN' }, 'tc-protein'),
        ev.toolResult('tc-protein', { id: ID, title: 'Crambin', content_type: WIDGET_TYPE, version: 1 }),
        ev.done(),
      ],
    });
    await page.route(ARTIFACT_URL, route => route.fulfill({
      json: { id: ID, content: JSON.stringify(SPEC) },
    }));
    await page.route(`**/api/chats/conv-e2e/artifacts/${ID}/structure*`, route => route.fulfill({
      status: 502,
      json: { detail: { error: { message: 'could not reach the structure database' } } },
    }));
    await page.goto('/');
    await page.getByTestId('composer-textarea').fill('show me crambin');
    await page.getByTestId('composer-send').click();
    await expect(page.getByRole('alert')).toContainText('could not reach the structure database');
    await expect(page.getByTestId('widget-frame')).toHaveCount(0);
  });
});

// The primer widget draws in the page rather than in a frame, and the test
// below for the sandbox says why: a frame with only allow-scripts cannot save
// a file, and the user has to be able to take the design away for ordering.
const PRIMER_ID = 'art_primers';
const PRIMER_URL = new RegExp(`/api/chats/conv-e2e/artifacts/${PRIMER_ID}(\\?|$)`);

const PRIMER_SPEC = {
  kind: 'primer_design',
  version: 1,
  mode: 'pcr',
  template: { name: 'pUC19', length: 2686, circular: true },
  target: [400, 450],
  pairs: [
    {
      name: 'pair_1',
      forward: {
        sequence: 'TGTAAAACGACGGCCAGT', display: 'tgtaaAACGACGGCCAGT', anneal: 'AACGACGGCCAGT',
        tail: 'tgtaa', strand: '+', start: 377, end: 395, length: 18, tm: 60.2, gc_percent: 50.0,
      },
      reverse: {
        sequence: 'CAGGAAACAGCTATGAC', display: 'CAGGAAACAGCTATGAC', anneal: 'CAGGAAACAGCTATGAC',
        tail: '', strand: '-', start: 483, end: 500, length: 17, tm: 58.1, gc_percent: 47.1,
      },
      product_size: 123,
      amplicon: 'ACGT'.repeat(30) + 'ACG',
      tm_difference: 2.1,
      penalty: 2.58,
      checks: [
        { rule: 'forward.tm_range', label: 'forward primer: Tm within 57-63 C', severity: 'soft', passed: true, value: 60.2, threshold: '57-63 C', source: 'Dieffenbach 1993', detail: '' },
        // The tool gives a check it could not run passed: true and says so in
        // `detail`, so the widget must read the detail, not the flag.
        { rule: 'pair.specificity', label: 'pair: Genome specificity', severity: 'hard', passed: true, value: null, threshold: '1 hit', source: 'Ye et al. 2012', detail: 'not computed: no BLAST database is available' },
      ],
      verified: { outcome: 'single_product', product_count: 1, product_sizes: [123], single_clean_product: true },
    },
    {
      // iGEM VF2 x VR on pUC19: 845 bp across the origin.
      name: 'pair_2',
      forward: {
        sequence: 'TGCCACCTGACGTCTAAGAA', display: 'TGCCACCTGACGTCTAAGAA', anneal: 'TGCCACCTGACGTCTAAGAA',
        tail: '', strand: '+', start: 2600, end: 2620, length: 20, tm: 59.4, gc_percent: 50.0,
      },
      reverse: {
        sequence: 'ATTACCGCCTTTGAGTGAGC', display: 'ATTACCGCCTTTGAGTGAGC', anneal: 'ATTACCGCCTTTGAGTGAGC',
        tail: '', strand: '-', start: 739, end: 759, length: 20, tm: 61.0, gc_percent: 50.0,
      },
      product_size: 845,
      amplicon: '',
      tm_difference: 1.6,
      penalty: 4.1,
      checks: [{ rule: 'reverse.gc_clamp', label: 'reverse primer: GC clamp at the 3 prime end', severity: 'hard', passed: false, value: 'no G or C', threshold: 'at least one', source: 'Innis and Gelfand 1990', detail: 'the last five bases are A or T' }],
      verified: { outcome: 'multiple_products', product_count: 2, product_sizes: [845, 183], single_clean_product: false },
    },
  ],
  summary: { total: 3, passed: 1, hard_failures: 1, soft_failures: 0, not_computed: 1, usable: false },
  notes: ['No reference database, so specificity was not checked.'],
};

async function openPrimers(page: Page) {
  const result = { id: PRIMER_ID, title: 'Primers for pUC19 MCS', content_type: WIDGET_TYPE, version: 1, source: 'model_written' };
  await installMocks(page, {
    chatScript: [
      ev.conversation('conv-e2e'), ev.routing('chat'),
      ev.toolCall('design_primers', { mode: 'pcr' }, 'tc-primers'),
      ev.toolResult('tc-primers', result),
      ev.artifactCreated(result),
      ...tokens('Here are two pairs.'),
      ev.done(),
    ],
  });
  await page.route(PRIMER_URL, route => route.fulfill({
    json: { ...result, latest_version: 1, content: JSON.stringify(PRIMER_SPEC) },
  }));
  await page.goto('/');
  await page.getByTestId('composer-textarea').fill('design primers over the pUC19 MCS');
  await page.getByTestId('composer-send').click();
  await expect(page.getByTestId('primer-design-viewer')).toBeVisible();
}

test.describe('primer design widget', () => {
  test('draws both pairs, the tail and the origin-wrapping amplicon', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', e => errors.push(e.message));
    await openPrimers(page);

    const pairs = page.getByTestId('primer-pair');
    await expect(pairs).toHaveCount(2);
    // The case distinction a cloning user reads the primer by.
    const display = page.getByTestId('primer-display').first();
    await expect(display.locator('[data-part="tail"]')).toHaveText('tgtaa');
    await expect(display.locator('[data-part="anneal"]')).toHaveText('AACGACGGCCAGT');

    const wrapping = pairs.nth(1).getByTestId('primer-map').first();
    await expect(wrapping).toHaveAttribute('data-wrapped', 'true');
    await expect(wrapping.getByTestId('primer-map-amplicon')).toHaveCount(2);
    await expect(wrapping.getByTestId('primer-map-wrap-note')).toBeVisible();

    await expect(pairs.first().getByTestId('primer-verified')).toContainText('Single clean product');
    await expect(pairs.nth(1).getByTestId('primer-verified')).toContainText('Not a single clean product');
    // A check that could not run never looks like one that passed.
    const notComputed = pairs.first().getByTestId('primer-check').filter({ hasText: 'Not computed' });
    await expect(notComputed).toHaveCount(1);
    await expect(notComputed).toContainText('Ye et al. 2012');
    // Checks are gathered under the scope their rule id is namespaced with.
    await expect(pairs.first().getByTestId('primer-check-group').first()).toHaveAttribute('data-scope', 'forward');
    expect(errors).toEqual([]);
  });

  test('saves the primers as CSV and as FASTA, from the page and not the frame', async ({ page }) => {
    await openPrimers(page);
    const viewer = page.getByTestId('primer-design-viewer');
    // No sandboxed frame is involved, which is what makes the save possible.
    await expect(viewer.getByTestId('widget-frame')).toHaveCount(0);

    const [csv] = await Promise.all([
      page.waitForEvent('download'),
      viewer.getByRole('button', { name: 'Primers (CSV)' }).click(),
    ]);
    expect(csv.suggestedFilename()).toBe('pUC19_primers.csv');
    expect(readFileSync(await csv.path(), 'utf-8')).toBe([
      'name,sequence,length,tm,gc_percent,strand,start,end,pair,product_size',
      'pair_1_F,TGTAAAACGACGGCCAGT,18,60.2,50,+,377,395,pair_1,123',
      'pair_1_R,CAGGAAACAGCTATGAC,17,58.1,47.1,-,483,500,pair_1,123',
      'pair_2_F,TGCCACCTGACGTCTAAGAA,20,59.4,50,+,2600,2620,pair_2,845',
      'pair_2_R,ATTACCGCCTTTGAGTGAGC,20,61,50,-,739,759,pair_2,845',
      '',
    ].join('\n'));

    const [fasta] = await Promise.all([
      page.waitForEvent('download'),
      viewer.getByRole('button', { name: 'Primers (FASTA)' }).click(),
    ]);
    expect(fasta.suggestedFilename()).toBe('pUC19_primers.fasta');
    const text = readFileSync(await fasta.path(), 'utf-8');
    expect(text.split('\n').slice(0, 2)).toEqual([
      '>pair_1_F strand=+ 377-395 len=18 Tm=60.2 GC=50 template=pUC19',
      'TGTAAAACGACGGCCAGT',
    ]);
    expect(text.match(/^>/gm)).toHaveLength(4);
  });

  test('renders a single-primer sequencing design without inventing a product', async ({ page }) => {
    const result = { id: PRIMER_ID, title: 'Sequencing primer for pUC19', content_type: WIDGET_TYPE, version: 1, source: 'model_written' };
    const spec = {
      kind: 'primer_design',
      version: 1,
      mode: 'sequencing',
      template: { name: 'pUC19', length: 2686, circular: true },
      target: null,
      pairs: [{
        name: 'seq_primer',
        forward: null,
        reverse: {
          sequence: 'CAGGAAACAGCTATGAC', display: 'CAGGAAACAGCTATGAC', anneal: 'CAGGAAACAGCTATGAC',
          tail: '', strand: '-', start: 483, end: 500, length: 17, tm: 58.1, gc_percent: 47.1,
        },
        product_size: 0,
        amplicon: '',
        tm_difference: null,
        penalty: null,
        checks: [{ rule: 'primer.primer_length', label: 'primer: length 18-25 nt', severity: 'soft', passed: true, value: 17, threshold: '18-25 nt', source: 'Dieffenbach 1993', detail: '' }],
        verified: { outcome: 'single_product', product_count: 1, product_sizes: [], single_clean_product: true },
      }],
      summary: { total: 1, passed: 1, hard_failures: 0, soft_failures: 0, not_computed: 0, usable: true },
      notes: ['verified here counts PRIMING SITES on this template, not PCR products.'],
    };
    const errors: string[] = [];
    page.on('pageerror', e => errors.push(e.message));
    await installMocks(page, {
      chatScript: [
        ev.conversation('conv-e2e'),
        ev.toolCall('design_primers', { mode: 'sequencing' }, 'tc-seq'),
        ev.toolResult('tc-seq', result),
        ev.artifactCreated(result),
        ev.done(),
      ],
    });
    await page.route(PRIMER_URL, route => route.fulfill({ json: { ...result, content: JSON.stringify(spec) } }));
    await page.goto('/');
    await page.getByTestId('composer-textarea').fill('a sequencing primer please');
    await page.getByTestId('composer-send').click();

    const viewer = page.getByTestId('primer-design-viewer');
    await expect(viewer).toBeVisible();
    await expect(viewer.getByTestId('primer-oligo')).toHaveCount(1);
    await expect(viewer.getByTestId('primer-map-amplicon')).toHaveCount(0);
    await expect(viewer.getByTestId('primer-verified')).toContainText('1 priming site');
    await expect(viewer.getByRole('button', { name: 'Amplicons (FASTA)' })).toHaveCount(0);
    expect(errors).toEqual([]);
  });

  test('falls back to a card when the spec is for a version this build does not know', async ({ page }) => {
    const result = { id: PRIMER_ID, title: 'Primers for pUC19 MCS', content_type: WIDGET_TYPE, version: 1, source: 'model_written' };
    await installMocks(page, {
      chatScript: [
        ev.conversation('conv-e2e'),
        ev.toolCall('design_primers', { mode: 'pcr' }, 'tc-primers'),
        ev.toolResult('tc-primers', result),
        ev.artifactCreated(result),
        ev.done(),
      ],
    });
    await page.route(PRIMER_URL, route => route.fulfill({
      json: { ...result, content: JSON.stringify({ ...PRIMER_SPEC, version: 99 }) },
    }));
    await page.goto('/');
    await page.getByTestId('composer-textarea').fill('design primers');
    await page.getByTestId('composer-send').click();
    await expect(page.getByTestId('artifact-card')).toContainText('preview unavailable');
    await expect(page.getByTestId('primer-design-viewer')).toHaveCount(0);
  });
});

test.describe('widget sandbox', () => {
  test('code in the frame cannot reach the app, its cookies or the network', async ({ page, context }) => {
    await openViewer(page);
    await context.addCookies([{ name: 'session', value: 'secret', url: new URL(page.url()).origin }]);
    const frame = await widgetFrame(page);
    // The browser reports a request it then refuses, so what counts is the reason
    // each one failed. Anything allowed through would reach these routes instead.
    const refusals: string[] = [];
    const delivered: string[] = [];
    page.on('requestfailed', r => { if (r.frame() === frame) refusals.push(r.failure()?.errorText ?? ''); });
    await page.route('https://example.org/**', route => { delivered.push(route.request().url()); return route.abort(); });
    await page.route('**/api/chats', route => {
      if (route.request().frame() === frame) delivered.push(route.request().url());
      return route.fallback();
    });

    const probes = await frame.evaluate(async (origin: string) => {
      const blocked = async (attempt: () => unknown) => {
        try {
          await attempt();
          return false;
        } catch {
          return true;
        }
      };
      return {
        origin: window.origin,
        cookie: await blocked(() => document.cookie),
        storage: await blocked(() => window.localStorage.getItem('x')),
        parentDom: await blocked(() => window.parent.document.body),
        parentLocation: await blocked(() => window.parent.location.href),
        fetchApp: await blocked(() => fetch(`${origin}/api/chats`, { credentials: 'include' })),
        fetchOutside: await blocked(() => fetch('https://example.org/')),
        xhr: await blocked(() => new Promise((resolve, reject) => {
          const request = new XMLHttpRequest();
          request.open('GET', 'https://example.org/');
          request.onload = resolve;
          request.onerror = reject;
          request.send();
        })),
        image: await blocked(() => new Promise((resolve, reject) => {
          const image = new Image();
          image.onload = resolve;
          image.onerror = reject;
          image.src = 'https://example.org/x.png';
        })),
        script: await blocked(() => new Promise((resolve, reject) => {
          const script = document.createElement('script');
          script.onload = resolve;
          script.onerror = reject;
          script.src = 'https://example.org/x.js';
          document.head.appendChild(script);
        })),
      };
    }, new URL(page.url()).origin);

    expect(probes).toEqual({
      origin: 'null',
      cookie: true,
      storage: true,
      parentDom: true,
      parentLocation: true,
      fetchApp: true,
      fetchOutside: true,
      xhr: true,
      image: true,
      script: true,
    });
    expect(delivered).toEqual([]);
    expect(refusals.filter(reason => reason !== 'csp')).toEqual([]);
  });

  // Why the primer widget draws in the page: a widget frame carries only
  // allow-scripts, so the browser drops a download started inside it, with no
  // error, no exception and no console warning. A widget that offered a file
  // from in there would look like it worked and hand the user nothing. Adding
  // allow-downloads is the alternative, and it is not worth loosening a sandbox
  // that otherwise keeps model-written code away from everything.
  test('the frame cannot save a file, and is not told it failed', async ({ page }) => {
    await openViewer(page);
    const frame = await widgetFrame(page);
    const downloads: string[] = [];
    page.on('download', d => downloads.push(d.suggestedFilename()));
    const errors: string[] = [];
    page.on('console', m => { if (m.type() === 'error' || m.type() === 'warning') errors.push(m.text()); });

    const attempt = await frame.evaluate(() => {
      const url = URL.createObjectURL(new Blob(['name,sequence\np1,ACGT\n'], { type: 'text/csv' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = 'primers.csv';
      document.body.appendChild(link);
      try {
        link.click();
        return { clicked: true, scheme: url.split(':')[0] };
      } catch (e) {
        return { clicked: false, error: String(e) };
      }
    });

    // The click goes through and makes a blob URL, so the widget has no way to
    // tell that nothing was saved.
    expect(attempt).toEqual({ clicked: true, scheme: 'blob' });
    await page.waitForTimeout(1000);
    expect(downloads).toEqual([]);
    expect(errors).toEqual([]);
  });

  test('a message from the frame cannot steer the app', async ({ page }) => {
    await openViewer(page);
    const frame = await widgetFrame(page);
    await frame.evaluate(() => {
      window.parent.postMessage({ type: 'error', message: '<img src=x onerror=alert(1)>' }, '*');
    });
    // Shown as plain text, never as markup.
    await expect(page.getByRole('alert').locator('p')).toHaveText('<img src=x onerror=alert(1)>');
    await expect(page.getByRole('alert').locator('img')).toHaveCount(0);
  });
});
