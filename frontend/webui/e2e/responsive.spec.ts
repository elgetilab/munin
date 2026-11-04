import { test, expect, type Page } from '@playwright/test';
import { installMocks } from './fixtures/mock';
import { ev, tokens } from './fixtures/sse';

// Layout rules that only exist in a real browser: Tailwind's reset, the xl
// breakpoint for the panels, and what a phone can and cannot reach.
const REPLY = '1. First\n2. Second\n\n- A bullet\n- Another\n\n- [x] A task\n';

const script = () => [
  ev.conversation('conv-e2e'), ev.routing('chat'),
  ev.artifactCreated({ id: 'a-md', title: 'Report One', content_type: 'text/markdown', source: 'model_written' }),
  ...tokens(REPLY),
  ev.done(),
];

async function send(page: Page) {
  await page.getByTestId('composer-textarea').fill('Tell me about lists');
  await page.getByTestId('composer-send').click();
  await expect(page.getByTestId('composer-send')).toBeVisible();
}

const horizontalOverflow = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);

test.describe('replies', () => {
  test('lists keep their markers, except a task item, whose marker is its checkbox', async ({ page }) => {
    await installMocks(page, { chatScript: script() });
    await page.goto('/');
    await send(page);
    await expect(page.locator('.prose-munin ol').first()).toHaveCSS('list-style-type', 'decimal');
    await expect(page.locator('.prose-munin ul').first()).toHaveCSS('list-style-type', 'disc');
    await expect(page.locator('.prose-munin li.task-list-item').first()).toHaveCSS('list-style-type', 'none');
  });
});

test.describe('panels', () => {
  for (const [width, height] of [[390, 844], [820, 1180], [1024, 768]]) {
    test(`are a full-screen overlay at ${width}px, with working back and close`, async ({ page }) => {
      await page.setViewportSize({ width, height });
      await installMocks(page, { chatScript: script() });
      await page.goto('/');
      await send(page);

      const artifacts = page.getByTestId('artifact-panel');
      await expect(artifacts).toHaveCSS('position', 'fixed');
      expect((await artifacts.boundingBox())?.width).toBe(width);
      await artifacts.getByRole('button', { name: '←' }).click();
      await expect(page.getByTestId('artifact-item')).toBeVisible();
      await artifacts.getByRole('button', { name: '✕' }).click();
      await expect(artifacts).toBeHidden();

      await page.getByRole('button', { name: 'Knowledge', exact: true }).click();
      const knowledge = page.getByTestId('knowledge-panel');
      await expect(knowledge).toHaveCSS('position', 'fixed');
      expect((await knowledge.boundingBox())?.width).toBe(width);
      await knowledge.getByRole('button', { name: '✕' }).click();
      await expect(knowledge).toBeHidden();
    });
  }

  test('are a side column from xl up', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await installMocks(page, { chatScript: script() });
    await page.goto('/');
    await send(page);
    const panel = page.getByTestId('artifact-panel');
    await expect(panel).toHaveCSS('position', 'static');
    expect((await panel.boundingBox())?.width).toBe(576);
    await expect(page.getByTestId('composer-textarea')).toBeInViewport();
  });
});

test.describe('header', () => {
  test('on a phone the pills stay on one line and the page does not scroll sideways', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await installMocks(page, { chatScript: script() });
    await page.goto('/');
    await send(page);
    await page.getByTestId('artifact-panel').getByRole('button', { name: '✕' }).click();

    await expect(page.getByTestId('artifacts-button')).toHaveCSS('height', '26px');
    expect(await horizontalOverflow(page)).toBe(0);
    const report = page.getByRole('button', { name: 'Report this chat' });
    await expect(report).toBeVisible();
    await expect(report.getByText('Report')).toBeHidden();
    const hamburger = await page.getByRole('button', { name: 'Show sidebar' }).boundingBox();
    expect(hamburger?.height).toBeGreaterThanOrEqual(44);
  });

  test('beside an open sidebar on a tablet the page does not scroll sideways', async ({ page }) => {
    await page.setViewportSize({ width: 820, height: 1180 });
    await installMocks(page, { chatScript: script() });
    await page.goto('/');
    await send(page);
    await page.getByTestId('artifact-panel').getByRole('button', { name: '✕' }).click();
    expect(await horizontalOverflow(page)).toBe(0);
  });
});

test.describe('profile menu', () => {
  test('gives a phone Help, Knowledge, Docs and Upload', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await installMocks(page);
    await page.goto('/');
    await page.getByRole('button', { name: 'Show sidebar' }).click();
    await page.getByText('e2e@test.local').click();
    await expect(page.getByRole('button', { name: 'Help' })).toBeVisible();
    for (const name of ['Knowledge', 'Docs', 'Upload']) {
      await expect(page.getByRole('link', { name, exact: true })).toBeVisible();
    }
    await page.getByRole('link', { name: 'Knowledge', exact: true }).click();
    await expect(page).toHaveURL(/\/knowledge$/);
  });

  test('leaves those links to the header nav from md up', async ({ page }) => {
    await installMocks(page);
    await page.goto('/');
    await page.getByText('e2e@test.local').click();
    await expect(page.getByRole('button', { name: 'Help' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Upload', exact: true })).toHaveCount(1);
  });
});

test.describe('keyboard', () => {
  test('the skip link is the first stop and lands in the composer, without changing the page', async ({ page }) => {
    await installMocks(page);
    await page.goto('/');
    const url = page.url();
    await page.keyboard.press('Tab');
    const link = page.getByRole('link', { name: 'Skip to message box' });
    await expect(link).toBeFocused();
    await expect(link).toBeInViewport();
    await expect(link).toHaveCSS('outline-style', 'solid');
    await page.keyboard.press('Enter');
    await expect(page.getByTestId('composer-textarea')).toBeFocused();
    expect(page.url()).toBe(url);
  });

  test('Escape closes the + menu and returns to its button', async ({ page }) => {
    await installMocks(page);
    await page.goto('/');
    await page.getByTestId('composer-attach').click();
    await expect(page.getByTestId('attach-deep-research')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByTestId('attach-deep-research')).toBeHidden();
    await expect(page.getByTestId('composer-attach')).toBeFocused();
  });

  test('the composer draws its ring on the bubble, not across it', async ({ page }) => {
    await installMocks(page);
    await page.goto('/');
    const box = page.getByTestId('composer-textarea');
    await box.focus();
    await expect(box).toHaveCSS('outline-style', 'none');
    await expect(box.locator('..')).toHaveCSS('outline-style', 'solid');
    await expect(box.locator('..')).toHaveCSS('outline-color', 'rgb(88, 166, 255)');
  });
});
