import { test, expect, type Page } from '@playwright/test';
import { installMocks } from './fixtures/mock';
import { conversationSummary } from './fixtures/scenarios';

// A session that ends while the tab is open (lib/session.ts). On the live site
// Caddy then redirects /api requests to the login page, which fetch sees as a
// network error, and /auth/me answers 401. Routes added after installMocks win.

async function loadWithDraft(page: Page) {
  await installMocks(page, { conversations: [conversationSummary('cA', 'Conversation A')] });
  await page.goto('/');
  await expect(page.getByText('Conversation A')).toBeVisible();
  await page.locator('#composer-textarea').fill('A draft I have not sent');
}

test('an API request that fails once the session has ended shows a notice and keeps the page', async ({ page }) => {
  await loadWithDraft(page);
  await page.route(/\/auth\/me$/, (route) => route.fulfill({ status: 401, body: '' }));
  await page.route(/\/api\/chats(\?|$)/, (route) => route.abort());

  await page.getByRole('button', { name: 'Search chats' }).click();

  const notice = page.getByTestId('session-notice');
  await expect(notice).toBeVisible();
  await expect(notice).toContainText('Your session has ended.');
  const link = notice.getByRole('link', { name: 'Sign in again' });
  await expect(link).toHaveAttribute('href', /\/login\?redirect=/);
  const href = await link.getAttribute('href');
  expect(decodeURIComponent(href!.split('redirect=')[1])).toBe(page.url());

  // Nothing navigated, and the draft is still there to copy.
  await page.keyboard.press('Escape');
  await expect(page).toHaveURL(/\/$/);
  await expect(page.locator('#composer-textarea')).toHaveValue('A draft I have not sent');
});

test('a failed API request while still signed in shows no notice', async ({ page }) => {
  await loadWithDraft(page);
  let checks = 0;
  await page.route(/\/auth\/me$/, (route) => {
    checks += 1;
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ email: 'e2e@test.local', name: 'E2E' }) });
  });
  await page.route(/\/api\/chats(\?|$)/, (route) => route.abort());

  await page.getByRole('button', { name: 'Search chats' }).click();

  await expect.poll(() => checks).toBe(1);
  await expect(page.getByTestId('session-notice')).toBeHidden();
});
