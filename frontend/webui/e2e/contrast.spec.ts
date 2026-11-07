import { test, expect, type Page } from '@playwright/test';
import { installMocks } from './fixtures/mock';
import { ev, tokens } from './fixtures/sse';
import * as S from './fixtures/scenarios';
import { formatFailures, measureContrast, type ContrastFailure } from './fixtures/contrast';
import { expectProjectTheme, projectTheme, type Theme } from './fixtures/theme';

// Every visible piece of text on the main screens, against what is painted
// behind it: 4.5:1, or 3:1 for large text (e2e/fixtures/contrast.ts). It runs
// once per project, so once in dark and once in light (playwright.config.ts).
// A failure lists the element, both colours and the ratio.

const REPLY = [
  '## Findings',
  '',
  'Crambin is a small plant protein. See [the PDB entry](https://www.rcsb.org/structure/1CRN) and `1CRN`.',
  '',
  '1. It has 46 residues',
  '2. It crystallises well',
  '',
  '- [x] Fetched the structure',
  '- [ ] Compared homologues',
  '',
  '> A quote from a source.',
  '',
  '| Residue | Count |',
  '| --- | --- |',
  '| Cys | 6 |',
  '',
  '```python',
  'def residues(seq: str) -> int:',
  '    # count them',
  '    return len(seq)  # "done"',
  'print(residues("TTCCPSIVARSNFNVCRLPGT"), 42, True)',
  '```',
  '',
].join('\n');

const chatScript = () => [
  ev.conversation('conv-e2e'), ev.routing('research'),
  ev.toolCall('web_search', { query: 'crambin' }, 'tc-web'),
  ev.toolResult('tc-web', { results: [{ title: 'Crambin', url: 'https://example.org/crambin', snippet: 'a protein' }] }),
  ev.artifactCreated({ id: 'a-md', title: 'Crambin notes', content_type: 'text/markdown', source: 'model_written' }),
  ...tokens(REPLY, 6),
  ev.done(),
];

const conversations = [
  S.conversationSummary('conv-e2e', 'Crambin structure'),
  S.conversationSummary('conv-2', 'CRISPR off-target risks'),
  S.conversationSummary('conv-3', 'Lipid nanoparticles'),
];

const topics = [
  { slug: 'protein-folding', label: 'Protein folding', paper_count: 12 },
  { slug: 'crispr', label: 'CRISPR', paper_count: 4 },
];
const groups = [{ slug: 'structural', display_name: 'Structural biology', paper_count: 9 }];
const contributors = [{ username: 'ada', display_name: 'Ada L', group_slug: 'structural', paper_count: 3 }];

// The catalogue, and the paper map the Knowledge page draws (the map itself is a
// canvas, so only its labels and legend are checked).
async function tagsWithContent(page: Page) {
  await page.route('**/api/tags', (route) => route.fulfill({
    json: { topics, groups, contributors, contributor_count: 1 },
  }));
  await page.route('**/api/embedding_map', (route) => route.fulfill({
    json: {
      generated_at: new Date(0).toISOString(), paper_count: 3, cluster_count: 1,
      clusters: [{ id: 0, label: 'Protein folding', slug: 'protein-folding', size: 3, centroid: [1, 1] }],
      points: [0, 1, 2].map((i) => ({ id: `p${i}`, doi: `10.1/${i}`, title: `Paper ${i}`, year: 2020 + i, x: i, y: i, cluster: 0 })),
    },
  }));
}

async function openAccountMenu(page: Page) {
  await page.getByText('e2e@test.local').click();
}

// Failures that were already there in dark before light mode, and that light
// mode must not change (dark stays exactly as it was). Each is matched exactly:
// the theme, the end of the element's selector and both colours, so any other
// failure still fails. A test that shows one names it in `seen`, and fails if
// it is no longer found, so this list has to shrink when one is fixed.
interface KnownFailure { theme: Theme; selectorEnd: string; fg: string; bg: string; why: string }

const AVATAR_INITIAL: KnownFailure = {
  theme: 'dark', selectorEnd: 'div.w-7.h-7.rounded-full.bg-accent/20', fg: '#58a6ff', bg: '#2e435c',
  why: 'sidebar account avatar initial, text-accent on bg-accent/20 over the hovered row: 4.02:1 in dark',
};
const CLUSTER_COUNT: KnownFailure = {
  theme: 'dark', selectorEnd: 'span.text-[10px].tabular-nums.opacity-60', fg: '#5e656e', bg: '#1a1f26',
  why: 'Knowledge page topic chip count at opacity-60: 2.81:1 in dark',
};
const KNOWN_DARK: KnownFailure[] = [AVATAR_INITIAL, CLUSTER_COUNT];

const isKnown = (f: ContrastFailure, k: KnownFailure) =>
  f.selector.endsWith(k.selectorEnd) && f.fg === k.fg && f.bg === k.bg;

async function expectReadable(page: Page, opts: { root?: string; ignore?: string[]; seen?: KnownFailure[] } = {}) {
  const report = await measureContrast(page, opts);
  await test.info().attach('contrast', { body: JSON.stringify(report, null, 2), contentType: 'application/json' });
  // A walk that found nothing would pass whatever the colours were.
  expect(report.checked, `text checked, skipped: ${JSON.stringify(report.skipped)}`).toBeGreaterThan(5);
  const theme = projectTheme(test.info());
  const known = KNOWN_DARK.filter((k) => k.theme === theme);
  const failures = report.failures.filter((f) => !known.some((k) => isKnown(f, k)));
  expect(failures, `text under the contrast minimum:\n${formatFailures(failures)}\n`).toEqual([]);
  for (const k of (opts.seen ?? []).filter((s) => s.theme === theme)) {
    expect(report.failures.some((f) => isKnown(f, k)), `known failure no longer found, remove it from KNOWN_DARK: ${k.why}`).toBe(true);
  }
}

test.describe('contrast', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
  });

  test('chat with a reply, the sidebar and an open artifact', async ({ page }, info) => {
    await installMocks(page, { chatScript: chatScript(), conversations });
    await page.goto('/');
    await expectProjectTheme(page, info);
    await page.getByTestId('composer-textarea').fill('Tell me about crambin');
    await page.getByTestId('composer-send').click();
    await expect(page.getByTestId('composer-send')).toBeVisible();
    await expect(page.locator('code.language-python').first()).toBeVisible();
    const panel = page.getByTestId('artifact-panel');
    await expect(panel).toBeVisible();
    await expect(panel.getByText('Mocked artifact body.')).toBeVisible();
    await expectReadable(page);
  });

  test('the composer with text typed, and the + menu', async ({ page }, info) => {
    await installMocks(page, { conversations });
    await page.goto('/');
    await expectProjectTheme(page, info);
    await expectReadable(page);
    await page.getByTestId('composer-textarea').fill('A draft message');
    await page.getByTestId('composer-attach').click();
    await expect(page.getByTestId('attach-deep-research')).toBeVisible();
    await expectReadable(page);
  });

  test('the artifact list', async ({ page }, info) => {
    await installMocks(page, { chatScript: chatScript(), conversations });
    await page.goto('/');
    await expectProjectTheme(page, info);
    await page.getByTestId('composer-textarea').fill('Tell me about crambin');
    await page.getByTestId('composer-send').click();
    await expect(page.getByTestId('composer-send')).toBeVisible();
    await page.getByTestId('artifact-panel').getByRole('button', { name: 'Back to artifact list' }).click();
    await expect(page.getByTestId('artifact-item')).toBeVisible();
    await expectReadable(page);
  });

  test('the account menu', async ({ page }, info) => {
    await installMocks(page, { conversations });
    await page.goto('/');
    await expectProjectTheme(page, info);
    await openAccountMenu(page);
    await expect(page.getByRole('menuitem', { name: 'Settings' })).toBeVisible();
    await expectReadable(page, { seen: [AVATAR_INITIAL] });
  });

  test('settings', async ({ page }, info) => {
    await installMocks(page);
    await page.goto('/');
    await expectProjectTheme(page, info);
    await openAccountMenu(page);
    await page.getByRole('menuitem', { name: 'Settings' }).click();
    await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible();
    await expectReadable(page);
  });

  test('the admin dashboard', async ({ page }, info) => {
    await installMocks(page);
    const seen = new Date(Date.now() - 5 * 60_000).toISOString();
    const users = [
      { email: 'ada@test.local', last_seen_at: seen, source: 'browser' },
      { email: 'bob@test.local', last_seen_at: seen, source: 'api_key' },
    ];
    await page.route('**/api/usage/me', (route) => route.fulfill({ json: { ...S.USAGE, is_admin: true } }));
    await page.route('**/api/usage/admin/activity', (route) => route.fulfill({
      json: { online: users.slice(0, 1), recent: users.slice(1), all_users: users, total: users.length },
    }));
    await page.route('**/api/usage/admin', (route) => route.fulfill({
      json: {
        period: '2026-11', total_tokens: 123_456, total_requests: 321, active_users: 2,
        top_users: [
          { email: 'ada@test.local', tokens: 100_000, requests: 300, by_source: { browser: { tokens: 100_000, requests: 300 } } },
          { email: 'bob@test.local', tokens: 23_456, requests: 21, by_source: { api_key: { tokens: 23_456, requests: 21 } } },
        ],
        tools_usage: { web_search: 12, run_python: 3 },
      },
    }));
    await page.goto('/');
    await expectProjectTheme(page, info);
    await openAccountMenu(page);
    await page.getByRole('menuitem', { name: 'Admin' }).click();
    await expect(page.getByRole('heading', { name: 'Admin Dashboard' })).toBeVisible();
    await expect(page.getByText('Users this month')).toBeVisible();
    await expectReadable(page);
  });

  test('the Knowledge page', async ({ page }, info) => {
    await installMocks(page);
    await tagsWithContent(page);
    await page.goto('/knowledge');
    await expectProjectTheme(page, info);
    await expect(page.getByRole('heading', { name: 'Knowledge Base' })).toBeVisible();
    await expect(page.getByText('12 papers')).toBeVisible();
    await expectReadable(page, { seen: [CLUSTER_COUNT] });
  });

  test('the Sources panel', async ({ page }, info) => {
    await installMocks(page);
    await tagsWithContent(page);
    await page.goto('/');
    await expectProjectTheme(page, info);
    await page.getByRole('button', { name: 'Sources', exact: true }).click();
    const knowledge = page.getByTestId('knowledge-panel');
    await expect(knowledge).toBeVisible();
    await knowledge.getByRole('button', { name: 'Topics (2)' }).click();
    await expectReadable(page);
  });

  test('a phone with the sidebar open', async ({ page }, info) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await installMocks(page, { conversations });
    await page.goto('/');
    await expectProjectTheme(page, info);
    await page.getByRole('button', { name: 'Show sidebar' }).click();
    await expect(page.getByText('CRISPR off-target risks')).toBeVisible();
    await expectReadable(page);
  });
});
