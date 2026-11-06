import { test, expect } from '@playwright/test';
import { installMocks } from './fixtures/mock';
import { conversationSummary } from './fixtures/scenarios';

// Browser behaviour first seen on the live site, or only checked there so far.
// The real backend side of these is in e2e-live/.

test('a chat link that no longer exists says so and offers a new chat', async ({ page }) => {
  await installMocks(page);
  await page.route(/\/api\/chats\/gone$/, (route) =>
    route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"Not found"}' }));
  await page.goto('/c/gone');
  await expect(page.getByText('This chat could not be opened', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: 'Start a new chat' }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByTestId('composer-textarea')).toBeVisible();
});

test('Show more adds the next page of chats to the sidebar', async ({ page }) => {
  const all = Array.from({ length: 60 }, (_, i) => conversationSummary(`c${i}`, `Chat number ${i}`));
  await installMocks(page);
  await page.route(/\/api\/chats\?/, (route) => {
    const q = new URL(route.request().url()).searchParams;
    if (q.get('pinned_only')) {
      return route.fulfill({ json: { conversations: [], total: 0 } });
    }
    const offset = Number(q.get('offset') ?? 0);
    const limit = Number(q.get('limit') ?? 50);
    return route.fulfill({ json: { conversations: all.slice(offset, offset + limit), total: all.length } });
  });
  await page.goto('/');
  await expect(page.getByTestId('chat-row')).toHaveCount(50);
  await page.getByRole('button', { name: 'Show more' }).click();
  await expect(page.getByTestId('chat-row')).toHaveCount(60);
  await expect(page.getByRole('button', { name: 'Show more' })).toBeHidden();
});

test('the Archived group opens and lists archived projects', async ({ page }) => {
  await installMocks(page);
  await page.route(/\/api\/projects(\?|$)/, (route) => route.fulfill({
    json: {
      projects: [{
        id: 'p-old', name: 'Old project', archived: true, chat_count: 0,
        created_at: new Date(0).toISOString(), updated_at: new Date(0).toISOString(),
      }],
      total: 1,
    },
  }));
  await page.goto('/');
  const group = page.getByRole('button', { name: /Archived \(1\)/ });
  await expect(group).toHaveAttribute('aria-expanded', 'false');
  await group.click();
  await expect(page.getByTestId('archived-project')).toContainText('Old project');
});

test('Cancel stops the upload in the browser and frees the composer', async ({ page }) => {
  await installMocks(page);
  // Never answered, so the upload stays in flight until it is cancelled.
  await page.route(/\/api\/documents\/upload$/, () => {});
  let cancelled = '';
  await page.route(/\/cancel$/, (route) => {
    cancelled = new URL(route.request().url()).pathname;
    return route.fulfill({ json: { cancelled: true, deleted: false } });
  });
  const failed = page.waitForEvent('requestfailed', (r) => r.url().endsWith('/api/documents/upload'));
  await page.goto('/');
  await page.getByTestId('composer-textarea').fill('Question about the file');
  await page.locator('input[type="file"]').first().setInputFiles({
    name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('some notes'),
  });
  await expect(page.getByText(/Uploading notes\.txt/)).toBeVisible();
  await expect(page.getByTestId('composer-send')).toBeDisabled();

  await page.getByRole('button', { name: 'Cancel upload' }).click();
  await failed;
  await expect(page.getByText(/Uploading notes\.txt/)).toBeHidden();
  await expect(page.getByTestId('composer-send')).toBeEnabled();
  await expect.poll(() => cancelled).toMatch(/^\/api\/documents\/upload\/[\w-]+\/cancel$/);
});

test('if the server cannot be told about a Cancel, the user hears about it', async ({ page }) => {
  await installMocks(page);
  await page.route(/\/api\/documents\/upload$/, () => {});
  await page.route(/\/cancel$/, (route) => route.fulfill({ status: 502, body: 'Bad Gateway' }));
  await page.goto('/');
  await page.locator('input[type="file"]').first().setInputFiles({
    name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('some notes'),
  });
  await page.getByRole('button', { name: 'Cancel upload' }).click();
  await expect(page.getByText(/server may have kept it/)).toBeVisible();
});

test('a signed-out page goes to the login page and comes back to the same place', async ({ page }) => {
  await installMocks(page);
  await page.route(/\/auth\/me$/, (route) => route.fulfill({ status: 401, json: { error: 'Not authenticated' } }));
  await page.route(/\/login\?/, (route) =>
    route.fulfill({ status: 200, contentType: 'text/html', body: '<h1>Login page</h1>' }));
  await page.goto('/c/some-chat');
  await expect(page.getByRole('heading', { name: 'Login page' })).toBeVisible();
  const redirect = new URL(page.url()).searchParams.get('redirect');
  expect(redirect).toMatch(/\/c\/some-chat$/);
});

test('a failing sign-in check that is not 401 stays on the page', async ({ page }) => {
  await installMocks(page);
  await page.route(/\/auth\/me$/, (route) => route.fulfill({ status: 502, body: 'Bad Gateway' }));
  await page.goto('/');
  await expect(page.getByTestId('composer-textarea')).toBeVisible();
  await expect(page).toHaveURL(/localhost:\d+\/$/);
});

test('Sign out posts to the auth service as a whole-page navigation', async ({ page }) => {
  await installMocks(page);
  let method = '';
  await page.route(/\/logout$/, (route) => {
    method = route.request().method();
    return route.fulfill({ status: 200, contentType: 'text/html', body: '<h1>Signed out page</h1>' });
  });
  await page.goto('/');
  await page.getByText('e2e@test.local').click();
  await page.getByRole('menuitem', { name: 'Sign out' }).click();
  await expect(page.getByRole('heading', { name: 'Signed out page' })).toBeVisible();
  expect(method).toBe('POST');
});
