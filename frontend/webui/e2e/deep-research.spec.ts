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

  test('arming then sending renders the research inline and blocks the composer', async ({ page }) => {
    const state = await installMocks(page);
    await page.goto('/');
    await openAttachMenu(page);
    await page.getByTestId('attach-deep-research').click();
    await page.getByTestId('composer-textarea').fill('What are CRISPR off-target risks?');
    await page.getByTestId('composer-send').click();

    // The research renders inline (plan checklist), like normal tool use.
    await expect(page.getByTestId('research-timeline')).toBeVisible();
    await expect(page.getByTestId('research-plan-item').first()).toBeVisible();

    // The composer is blocked while it runs.
    await expect(page.getByTestId('dr-blocked')).toBeVisible();
    await expect(page.getByTestId('composer-textarea')).toBeDisabled();

    // As polls advance, a tool card (a search/read) and then a note appear, and
    // it finishes with a report link + the artifact surfacing.
    await expect(page.getByTestId('research-tool-row').first()).toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId('research-note').first()).toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId('research-report-link')).toBeVisible({ timeout: 15000 });
    expect(state.artifacts.some((a) => a.id === 'art-dr')).toBe(true);

    // Clicking the report link opens the artifact panel.
    await page.getByTestId('research-report-link').click();
    await expect(page.getByTestId('artifact-panel')).toBeVisible();
  });

  test('the inline research view contains no em-dash', async ({ page }) => {
    await installMocks(page);
    await page.goto('/');
    await openAttachMenu(page);
    await page.getByTestId('attach-deep-research').click();
    expect(await page.getByTestId('dr-armed').innerText()).not.toContain('—');
    await page.getByTestId('composer-textarea').fill('q');
    await page.getByTestId('composer-send').click();
    await expect(page.getByTestId('research-timeline')).toBeVisible();
    expect(await page.getByTestId('research-timeline').innerText()).not.toContain('—');
  });

  test('toggle is disabled in incognito (ephemeral) chats', async ({ page }) => {
    await installMocks(page);
    await page.goto('/');
    await page.getByTestId('ephemeral-toggle').click();
    await openAttachMenu(page);
    await expect(page.getByTestId('attach-deep-research')).toBeDisabled();
  });
});
