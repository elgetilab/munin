import { test, expect, type Page } from '@playwright/test';
import { installMocks } from './fixtures/mock';
import { ev, tokens } from './fixtures/sse';
import * as S from './fixtures/scenarios';
import { formatFailures, measureContrast } from './fixtures/contrast';
import { expectProjectTheme } from './fixtures/theme';

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

// Strict in both themes: no exceptions.
async function expectReadable(page: Page, opts: { root?: string; ignore?: string[] } = {}) {
  const report = await measureContrast(page, opts);
  await test.info().attach('contrast', { body: JSON.stringify(report, null, 2), contentType: 'application/json' });
  // A walk that found nothing would pass whatever the colours were.
  expect(report.checked, `text checked, skipped: ${JSON.stringify(report.skipped)}`).toBeGreaterThan(5);
  expect(report.failures, `text under the contrast minimum:\n${formatFailures(report.failures)}\n`).toEqual([]);
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

  test('announcement banners at each level', async ({ page }, info) => {
    await installMocks(page, { conversations });
    for (const level of ['info', 'warning', 'error']) {
      await page.route('**/api/announcement', (route) => route.fulfill({
        json: { announcement: { message: `A ${level} announcement for everyone.`, level } },
      }));
      await page.goto('/');
      await expectProjectTheme(page, info);
      await expect(page.getByText(`A ${level} announcement for everyone.`)).toBeVisible();
      await expectReadable(page);
    }
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
    await expectReadable(page);
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
    await expectReadable(page);
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
