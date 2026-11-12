import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { test, expect, type Page } from '@playwright/test';
import { installMocks } from './fixtures/mock';
import { ev, tokens } from './fixtures/sse';
import { cssVarColor, expectProjectTheme, PALETTE } from './fixtures/theme';

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
      await artifacts.getByRole('button', { name: 'Back to artifact list' }).click();
      await expect(page.getByTestId('artifact-item')).toBeVisible();
      await artifacts.getByRole('button', { name: 'Close artifacts' }).click();
      await expect(artifacts).toBeHidden();

      await page.getByRole('button', { name: 'Sources', exact: true }).click();
      const knowledge = page.getByTestId('knowledge-panel');
      await expect(knowledge).toHaveCSS('position', 'fixed');
      expect((await knowledge.boundingBox())?.width).toBe(width);
      await knowledge.getByRole('button', { name: 'Close knowledge panel' }).click();
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

  test('from xl up, Escape closes the side column and returns to its toggle', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await installMocks(page, { chatScript: script() });
    await page.goto('/');
    await send(page);
    const panel = page.getByTestId('artifact-panel');
    const toggle = page.getByTestId('artifacts-button');
    await panel.getByRole('button', { name: 'Back to artifact list' }).focus();
    await expect(toggle).toHaveAttribute('aria-pressed', 'true');

    // A menu above the column closes first.
    await page.getByTestId('composer-attach').click();
    await page.keyboard.press('Escape');
    await expect(page.getByTestId('attach-deep-research')).toBeHidden();
    await expect(panel).toBeVisible();

    await page.keyboard.press('Escape');
    await expect(panel).toBeHidden();
    await expect(toggle).toHaveAttribute('aria-pressed', 'false');
    await expect(toggle).toBeFocused();
  });
});

test.describe('header', () => {
  test('on a phone the header items stay on one line and the page does not scroll sideways', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await installMocks(page, { chatScript: script() });
    await page.goto('/');
    await send(page);
    await page.getByTestId('artifact-panel').getByRole('button', { name: 'Close artifacts' }).click();

    await expect(page.getByTestId('artifacts-button')).toHaveCSS('height', '32px');
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
    await page.getByTestId('artifact-panel').getByRole('button', { name: 'Close artifacts' }).click();
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
    await expect(page.getByRole('menuitem', { name: 'Help' })).toBeVisible();
    for (const name of ['Knowledge', 'Search papers', 'Docs', 'Upload']) {
      await expect(page.getByRole('menuitem', { name, exact: true })).toBeVisible();
    }
    await page.getByRole('menuitem', { name: 'Knowledge', exact: true }).click();
    await expect(page).toHaveURL(/\/knowledge$/);
  });

  // jsdom applies no CSS, so only a browser shows the `lg:hidden` items being skipped.
  test('the arrow keys reach every item past the ones hidden at 1280px', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await installMocks(page);
    await page.goto('/');
    await page.getByText('e2e@test.local').click();

    const settings = page.getByRole('menuitem', { name: 'Settings' });
    const docs = page.getByRole('menuitem', { name: 'Docs' });
    const help = page.getByRole('menuitem', { name: 'Help' });
    const signOut = page.getByRole('menuitem', { name: 'Sign out' });
    await expect(settings).toBeFocused();
    await expect(page.getByRole('menuitem', { name: 'Knowledge', exact: true })).toBeHidden();

    await page.keyboard.press('ArrowDown');
    await expect(docs).toBeFocused();
    await page.keyboard.press('ArrowDown');
    await expect(help).toBeFocused();
    await page.keyboard.press('ArrowDown');
    await expect(signOut).toBeFocused();
    await page.keyboard.press('ArrowDown');
    await expect(settings).toBeFocused();
    await page.keyboard.press('ArrowUp');
    await expect(signOut).toBeFocused();
    await page.keyboard.press('ArrowUp');
    await page.keyboard.press('ArrowUp');
    await page.keyboard.press('ArrowUp');
    await expect(settings).toBeFocused();
  });

  test('leaves those links to the header nav from md up', async ({ page }) => {
    await installMocks(page);
    await page.goto('/');
    await page.getByText('e2e@test.local').click();
    await expect(page.getByRole('menuitem', { name: 'Help' })).toBeVisible();
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

  // Reached from the keyboard: Shift+Tab back from the + button.
  async function tabIntoComposer(page: Page) {
    await page.getByTestId('composer-attach').focus();
    await page.keyboard.press('Shift+Tab');
    await expect(page.getByTestId('composer-textarea')).toBeFocused();
  }

  test('the composer draws its ring on the bubble, not across it', async ({ page }, info) => {
    await installMocks(page);
    await page.goto('/');
    const theme = await expectProjectTheme(page, info);
    const box = page.getByTestId('composer-textarea');
    await tabIntoComposer(page);
    await expect(box).toHaveCSS('outline-style', 'none');
    await expect(box.locator('..')).toHaveCSS('outline-style', 'solid');
    // The ring colour of the theme this project runs in, pinned, so a token gone
    // wrong fails here as well as a ring drawn in some other colour.
    await expect(box.locator('..')).toHaveCSS('outline-color', PALETTE[theme].accent);
  });

  test('the composer shows one focus line, not the ring and an accent border', async ({ page }) => {
    await installMocks(page);
    await page.goto('/');
    const bubble = page.getByTestId('composer-textarea').locator('..');
    const atRest = await bubble.evaluate((el) => getComputedStyle(el).borderTopColor);
    await tabIntoComposer(page);
    await bubble.evaluate((el) => Promise.all(el.getAnimations().map((a) => a.finished)));
    await expect(bubble).toHaveCSS('outline-style', 'solid');
    // A negative check against a pinned dark accent would pass in light whatever
    // the border did. So: the border keeps its resting colour, and that colour is
    // not this theme's accent.
    const accent = await cssVarColor(bubble, '--color-accent');
    expect(atRest).not.toBe(accent);
    await expect(bubble).toHaveCSS('border-top-color', atRest);
  });

  test('a click in the composer tints it instead of drawing the ring', async ({ page }) => {
    await installMocks(page);
    await page.goto('/');
    const box = page.getByTestId('composer-textarea');
    const bubble = box.locator('..');
    const atRest = await bubble.evaluate((el) => getComputedStyle(el).borderTopColor);
    await box.click();
    await expect(box).toBeFocused();
    await bubble.evaluate((el) => Promise.all(el.getAnimations().map((a) => a.finished)));
    await expect(bubble).toHaveCSS('outline-style', 'none');
    expect(await bubble.evaluate((el) => getComputedStyle(el).borderTopColor)).not.toBe(atRest);
    expect(await bubble.evaluate((el) => getComputedStyle(el).boxShadow)).not.toBe('none');
    // Typing keeps it gentle: the ring is decided when focus arrives.
    await page.keyboard.type('Hello');
    await expect(bubble).toHaveCSS('outline-style', 'none');
    // Leaving and coming back by keyboard brings the ring.
    await page.keyboard.press('Tab');
    await page.keyboard.press('Shift+Tab');
    await expect(box).toBeFocused();
    await expect(bubble).toHaveCSS('outline-style', 'solid');
  });

  test('every focus ring is one colour from its first frame', async ({ page }, info) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await installMocks(page, { conversations: [{ id: 'c1', title: 'A chat', created_at: new Date(0).toISOString(), updated_at: new Date(0).toISOString() }] });
    await page.goto('/');
    const ring = PALETTE[await expectProjectTheme(page, info)].accent;
    await expect(page.getByRole('link', { name: 'A chat' })).toBeVisible();
    const seen: string[] = [];
    // Header items, sidebar rows and the account button: read at once after each
    // Tab, as a transition on outline-color once faded the ring in from the text
    // colour, and only on the items that had transition-colors.
    for (let i = 0; i < 30; i++) {
      await page.keyboard.press('Tab');
      const now = await page.evaluate(() => {
        const el = document.activeElement as HTMLElement;
        if (el.id === 'composer-textarea') return null;
        const cs = getComputedStyle(el);
        return { name: el.getAttribute('aria-label') ?? el.textContent?.trim().slice(0, 24) ?? el.tagName, style: cs.outlineStyle, color: cs.outlineColor };
      });
      if (!now) break;
      expect(now.style, now.name).toBe('solid');
      expect(now.color, now.name).toBe(ring);
      seen.push(now.name);
    }
    // The account button is named by the signed-in user.
    for (const name of ['New chat', 'A chat', 'EE2E Tester', 'Sources', 'Docs']) {
      expect(seen.some((s) => s.startsWith(name)), name).toBe(true);
    }
  });

  const manyChats = (n: number) => Array.from({ length: n }, (_, i) => ({
    id: `c${i}`, title: `Chat ${i}`,
    created_at: new Date(Date.now() - i * 60_000).toISOString(),
    updated_at: new Date(Date.now() - i * 60_000).toISOString(),
  }));
  const inChatRow = (page: Page) =>
    page.evaluate(() => !!document.activeElement?.closest('[data-testid="chat-row"]'));

  test('the chat list is one Tab stop, walked with the arrow keys, with its ⋮ one Tab on', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await installMocks(page, { conversations: manyChats(12) });
    await page.goto('/');
    const first = page.getByRole('link', { name: 'Chat 0', exact: true });
    await expect(first).toBeVisible();

    // From the control before the list, one Tab lands on the first chat.
    await page.getByRole('button', { name: 'New project' }).focus();
    await page.keyboard.press('Tab');
    await expect(first).toBeFocused();
    await expect(first).toHaveCSS('outline-style', 'solid');
    await page.keyboard.press('Tab');
    await expect(page.getByRole('button', { name: 'More actions for Chat 0' })).toBeFocused();
    await page.keyboard.press('Tab');
    expect(await inChatRow(page)).toBe(false);

    // Back in, the ⋮ is hidden until its row holds focus, so Shift+Tab lands on the chat.
    await page.keyboard.press('Shift+Tab');
    await expect(first).toBeFocused();
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('ArrowDown');
    await expect(page.getByRole('link', { name: 'Chat 2', exact: true })).toBeFocused();
    await page.keyboard.press('End');
    const last = page.getByRole('link', { name: 'Chat 11', exact: true });
    await expect(last).toBeFocused();
    await expect(last).toBeInViewport();

    // Shift+F10 opens the row's menu, its items at 14px, Pin among them.
    await page.keyboard.press('Shift+F10');
    const pin = page.getByRole('menuitem', { name: 'Pin' });
    await expect(pin).toBeFocused();
    await expect(pin).toHaveCSS('font-size', '14px');
    await page.keyboard.press('Escape');
    await expect(page.getByRole('button', { name: 'More actions for Chat 11' })).toBeFocused();

    await page.keyboard.press('Shift+Tab');
    await expect(last).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/c\/c11$/);
  });

  test.describe('on a touch phone', () => {
    test.use({ hasTouch: true, isMobile: true });

    test('a chat row shows one action, not two', async ({ page }) => {
      await page.setViewportSize({ width: 390, height: 844 });
      await installMocks(page, { conversations: manyChats(3) });
      await page.goto('/');
      await page.getByRole('button', { name: 'Show sidebar' }).click();
      const row = page.getByTestId('chat-row').filter({ hasText: 'Chat 1' });
      await expect(row.getByRole('button', { name: 'More actions for Chat 1' })).toBeVisible();
      await expect(row.getByRole('button')).toHaveCount(1);
    });
  });
});

test.describe('demo', () => {
  for (const width of [390, 820, 1024, 1440]) {
    test(`the Slides button still works over an open artifact panel at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await installMocks(page, { chatScript: script() });
      await page.goto('/demo');
      await page.getByTestId('demo-next').click();
      await page.getByTestId('demo-next').click();
      await page.getByTestId('demo-run-chat').click();
      await expect(page.getByTestId('artifact-panel')).toBeVisible();

      await page.getByTestId('demo-slides').click({ timeout: 3000 });

      await expect(page.getByTestId('demo-deck')).toBeVisible();
      await expect(page).toHaveURL(/\/demo$/);
    });
  }

  test('Escape on the slides leaves the artifact column under them alone', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await installMocks(page, { chatScript: script() });
    await page.goto('/demo');
    await page.getByTestId('demo-next').click();
    await page.getByTestId('demo-next').click();
    await page.getByTestId('demo-run-chat').click();
    await expect(page.getByTestId('artifact-panel')).toBeVisible();
    await page.getByTestId('demo-slides').click();
    await expect(page.getByTestId('demo-deck')).toBeVisible();

    await page.keyboard.press('Escape');

    const toggle = page.getByTestId('artifacts-button');
    await expect(toggle).toHaveAttribute('aria-pressed', 'true');
    await expect(toggle).not.toBeFocused();
  });
});

test.describe('busy header', () => {
  const topics = ['alpha', 'beta', 'gamma'].map((slug) => ({ slug, label: slug, paper_count: 1 }));
  const manyArtifacts = () => [
    ev.conversation('conv-e2e'), ev.routing('chat'),
    ...Array.from({ length: 12 }, (_, i) => ev.artifactCreated({ id: `a-${i}`, title: `Report ${i}`, content_type: 'text/markdown', source: 'model_written' })),
    ...tokens(REPLY),
    ev.done(),
  ];

  for (const [width, sidebar] of [[1024, 'open'], [768, 'closed']] as const) {
    test(`keeps the last nav link inside the viewport at ${width}px with the sidebar ${sidebar}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 800 });
      await installMocks(page, { chatScript: manyArtifacts() });
      await page.route('**/api/tags', (route) => route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ topics, groups: [], contributors: [], contributor_count: 0 }),
      }));
      await page.goto('/');
      await send(page);
      await page.getByTestId('artifact-panel').getByRole('button', { name: 'Close artifacts' }).click();

      await page.getByTitle('Choose which sources to search').click();
      const knowledge = page.getByTestId('knowledge-panel');
      await knowledge.getByRole('button', { name: 'Topics (3)' }).click();
      for (const { label } of topics) await knowledge.getByRole('button', { name: label }).click();
      await knowledge.getByRole('button', { name: 'Close knowledge panel' }).click();

      const toggle = page.getByRole('button', { name: /sidebar$/ });
      if ((await toggle.getAttribute('aria-label')) !== (sidebar === 'open' ? 'Hide sidebar' : 'Show sidebar')) await toggle.click();

      const upload = page.getByRole('banner').getByRole('link', { name: 'Upload', exact: true });
      await expect(upload).toBeVisible();
      const box = await upload.boundingBox();
      expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(width);
    });
  }
});

test('the support button is hidden on a Knowledge browse page, and only there', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await installMocks(page);
  const button = page.locator('#munin-support-btn');
  const addButton = () => page.evaluate(() => {
    const el = document.createElement('button');
    el.id = 'munin-support-btn';
    el.textContent = '?';
    document.body.appendChild(el);
  });

  await page.goto('/knowledge/topic/example');
  await addButton();
  await expect(button).toBeHidden();

  await page.goto('/knowledge');
  await addButton();
  await expect(button).toBeVisible();
});

test.describe('support widget', () => {
  const WIDGET = fileURLToPath(new URL('../../static/shared/support-widget.js', import.meta.url));

  // The widget on a bare page, against the signed-out answer from /auth/me,
  // released only when the test calls the returned function.
  async function openWidget(page: Page) {
    let answer = () => {};
    const answered = new Promise<void>((resolve) => { answer = resolve; });
    const source = readFileSync(WIDGET, 'utf-8').replace(/\{\{env "[A-Z_]+"\}\}/g, '');
    await page.route('**/widget-page', (route) => route.fulfill({
      status: 200, contentType: 'text/html',
      body: '<!doctype html><html><body><script src="/shared/support-widget.js"></script></body></html>',
    }));
    await page.route('**/shared/support-widget.js', (route) => route.fulfill({ status: 200, contentType: 'application/javascript', body: source }));
    let responded = () => {};
    const response = new Promise<void>((resolve) => { responded = resolve; });
    await page.route('**/auth/me', async (route) => {
      await answered;
      await route.fulfill({ status: 401, contentType: 'application/json', body: '{}' });
      responded();
    });
    await page.goto('/widget-page');
    await page.locator('#munin-support-btn').click();
    await expect(page.locator('#munin-sp-message')).toBeVisible();
    return () => { answer(); return response; };
  }

  // The real widget on the real app, which the shared mocks otherwise blank out.
  async function appWithWidget(page: Page) {
    await installMocks(page);
    const source = readFileSync(WIDGET, 'utf-8').replace(/\{\{env "[A-Z_]+"\}\}/g, '');
    await page.route('**/shared/support-widget.js*', (route) => route.fulfill({ status: 200, contentType: 'application/javascript', body: source }));
    await page.goto('/');
  }

  test('Escape closes the widget and leaves a dialog beneath it alone', async ({ page }) => {
    await appWithWidget(page);
    await page.getByRole('button', { name: 'Search chats' }).click();
    const search = page.getByRole('textbox', { name: 'Search conversations' });
    await search.fill('kinase');

    await page.locator('#munin-support-btn').click();
    await expect(page.locator('#munin-sp-message')).toBeFocused();
    await page.keyboard.press('Escape');

    await expect(page.locator('#munin-support-overlay')).toHaveCount(0);
    await expect(search).toHaveValue('kinase');

    await page.keyboard.press('Escape');
    await expect(search).toHaveCount(0);
  });

  test('Help from the phone drawer closes it, and Tab moves on through the widget', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await appWithWidget(page);
    await page.getByRole('button', { name: 'Show sidebar' }).click();
    await page.getByText('e2e@test.local').click();
    await page.getByRole('menuitem', { name: 'Help' }).click();

    await expect(page.getByRole('button', { name: 'Close menu' })).toBeHidden();
    const message = page.locator('#munin-sp-message');
    await expect(message).toBeFocused();

    // Past Cancel to Send, which a trap fighting over Tab never let it reach, then round.
    await page.keyboard.press('Tab');
    await expect(page.getByRole('button', { name: 'Cancel' })).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(page.locator('#munin-sp-send')).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(page.getByRole('button', { name: 'Close support' })).toBeFocused();
  });

  test('a slow sign-in check keeps a message the visitor has started typing', async ({ page }) => {
    const answer = await openWidget(page);
    await page.locator('#munin-sp-message').fill('The upload page fails');
    await answer();
    await page.waitForTimeout(100);
    await expect(page.locator('#munin-sp-message')).toHaveValue('The upload page fails');
    await expect(page.locator('#munin-sp-message')).toBeFocused();
  });

  test('a visitor who has typed nothing gets the sign-in and email note', async ({ page }) => {
    const answer = await openWidget(page);
    await answer();
    await expect(page.locator('#munin-sp-message')).toHaveCount(0);
    await expect(page.locator('#munin-support-panel .sp-body')).toContainText('Sign in to send a message here');
  });
});
