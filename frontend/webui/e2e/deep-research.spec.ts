import { test, expect } from '@playwright/test';
import { installMocks } from './fixtures/mock';

// The area that just shipped three regressions by hand-testing: the toggle being
// disabled in a new chat, the missing progress line, and an em-dash in the copy.
// These guard all three directly.

async function openAttachMenu(page: import('@playwright/test').Page) {
  await page.getByTestId('composer-attach').click();
  await expect(page.getByTestId('attach-deep-research')).toBeVisible();
}

test.describe('deep research', () => {
  test('toggle is enabled and armable in a brand-new chat', async ({ page }) => {
    // The exact bug that shipped: a fresh chat has no conversation id, and the
    // toggle used to be disabled. It must be enabled now.
    await installMocks(page);
    await page.goto('/');
    await openAttachMenu(page);
    await expect(page.getByTestId('attach-deep-research')).toBeEnabled();
    await page.getByTestId('attach-deep-research').click();
    await expect(page.getByTestId('dr-armed')).toBeVisible();
  });

  test('arming then sending starts a background job and shows live progress', async ({ page }) => {
    const state = await installMocks(page);
    await page.goto('/');
    await openAttachMenu(page);
    await page.getByTestId('attach-deep-research').click();
    await page.getByTestId('composer-textarea').fill('What are CRISPR off-target risks?');
    await page.getByTestId('composer-send').click();

    // Live status line appears with a step and a ticking elapsed timer.
    const status = page.getByTestId('dr-status');
    await expect(status).toBeVisible();
    await expect(page.getByTestId('dr-status-step')).toContainText(/Planning|Researching|Writing/);
    await expect(page.getByTestId('dr-status-elapsed')).toContainText(/\d+s/);

    // As the mocked status progresses across polls, it reaches the report and the
    // artifact list refetch surfaces it.
    await expect(page.getByText(/report is in Artifacts/i)).toBeVisible({ timeout: 15000 });
    expect(state.artifacts.some((a) => a.id === 'art-dr')).toBe(true);
  });

  test('deep research status copy contains no em-dash', async ({ page }) => {
    await installMocks(page);
    await page.goto('/');
    await openAttachMenu(page);
    await page.getByTestId('attach-deep-research').click();
    const armed = await page.getByTestId('dr-armed').innerText();
    expect(armed).not.toContain('—'); // em dash
    await page.getByTestId('composer-textarea').fill('q');
    await page.getByTestId('composer-send').click();
    const status = await page.getByTestId('dr-status').innerText();
    expect(status).not.toContain('—');
  });

  test('toggle is disabled in incognito (ephemeral) chats', async ({ page }) => {
    await installMocks(page);
    await page.goto('/');
    await page.getByTestId('ephemeral-toggle').click();
    await openAttachMenu(page);
    await expect(page.getByTestId('attach-deep-research')).toBeDisabled();
  });
});
