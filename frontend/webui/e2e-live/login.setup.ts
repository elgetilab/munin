import { test, expect } from '@playwright/test';

// A person signs in by hand in the window this opens. Nothing here types an
// address or asks for a login mail.
test('sign in by hand and keep the session for the live checks', async ({ page, context, baseURL }) => {
  const statePath = process.env.MUNIN_LIVE_STATE;
  expect(statePath, 'MUNIN_LIVE_STATE').toBeTruthy();
  await page.goto('/');
  await page.waitForURL((url) => url.origin === baseURL, { timeout: 590_000 });
  await expect(page.getByTestId('composer-textarea')).toBeVisible({ timeout: 30_000 });
  await context.storageState({ path: statePath });
});
