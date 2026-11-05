import { test, expect } from '@playwright/test';
import { installMocks } from './fixtures/mock';
import { artifactReply, sandboxFileReply } from './fixtures/scenarios';

// The heaviest real usage: LaTeX->PDF, run_python->PNG, create_artifact->html.
// A chat reply that emits `artifact_created` should surface in the Artifacts
// panel (which auto-opens and shows the new artifact with a Download action).
async function sendFor(page: import('@playwright/test').Page, prompt: string) {
  await page.getByTestId('composer-textarea').fill(prompt);
  await page.getByTestId('composer-send').click();
}

async function openArtifacts(page: import('@playwright/test').Page) {
  await expect(page.getByTestId('artifacts-button')).toBeVisible();
  if (!(await page.getByTestId('artifact-panel').isVisible().catch(() => false))) {
    await page.getByTestId('artifacts-button').click();
  }
  await expect(page.getByTestId('artifact-panel')).toBeVisible();
}

test.describe('artifacts', () => {
  test('an html artifact appears in the Artifacts panel', async ({ page }) => {
    await installMocks(page, { chatScript: artifactReply({ id: 'a-html', title: 'Pong Game', content_type: 'text/html' }) });
    await page.goto('/');
    await sendFor(page, 'make me pong');
    await expect(page.getByTestId('artifacts-button')).toContainText('Artifacts (1)');
    await openArtifacts(page);
    const panel = page.getByTestId('artifact-panel');
    await expect(panel.getByRole('heading', { name: 'Pong Game' })).toBeVisible();
    await expect(panel.getByRole('button', { name: /Download/i })).toBeVisible();
  });

  test('a compiled PDF artifact appears with a download action', async ({ page }) => {
    await installMocks(page, { chatScript: artifactReply({ id: 'a-pdf', title: 'paper.pdf', content_type: 'application/pdf', filename: 'paper.pdf', external_url: '/api/artifacts/conv-e2e/a-pdf', source: 'sandbox' }) });
    await page.goto('/');
    await sendFor(page, 'compile my latex');
    await expect(page.getByTestId('artifacts-button')).toContainText('Artifacts (1)');
    await openArtifacts(page);
    const panel = page.getByTestId('artifact-panel');
    await expect(panel.getByRole('heading', { name: 'paper.pdf' })).toBeVisible();
    await expect(panel.getByRole('button', { name: /Download/i })).toBeVisible();
  });

  test('a PNG plot artifact renders in the panel', async ({ page }) => {
    await installMocks(page, { chatScript: artifactReply({ id: 'a-png', title: 'figure.png', content_type: 'image/png', filename: 'figure.png', external_url: '/api/artifacts/conv-e2e/a-png', source: 'sandbox' }) });
    await page.goto('/');
    await sendFor(page, 'plot sin(x)');
    await openArtifacts(page);
    const panel = page.getByTestId('artifact-panel');
    await expect(panel.getByRole('heading', { name: 'figure.png' })).toBeVisible();
    await expect(panel.getByRole('img', { name: 'figure.png' })).toBeVisible();
  });

  test('the artifacts panel can list multiple artifacts', async ({ page }) => {
    // Navigate back from the auto-selected detail view to the list.
    await installMocks(page, { chatScript: artifactReply({ id: 'a-html', title: 'Report One', content_type: 'text/markdown' }) });
    await page.goto('/');
    await sendFor(page, 'write a report');
    await openArtifacts(page);
    // The "back to list" control (a left-arrow) returns to the list view.
    await page.getByTestId('artifact-panel').getByRole('button', { name: 'Back to artifact list' }).click();
    await expect(page.getByTestId('artifact-item').filter({ hasText: 'Report One' })).toBeVisible();
  });
});

test.describe('inline artifacts', () => {
  test('a plot shows in the message and leaves the panel closed', async ({ page }) => {
    await installMocks(page, { chatScript: sandboxFileReply({ id: 'a-png', filename: 'figure.png', content_type: 'image/png', size_bytes: 2048 }) });
    await page.route('**/api/artifacts/conv-e2e/a-png', route => route.fulfill({
      contentType: 'image/png',
      // A 1x1 PNG, because the catch-all mock's JSON would fail to load as an image.
      body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAEBgIApD5fRAAAAABJRU5ErkJggg==', 'base64'),
    }));
    await page.goto('/');
    await sendFor(page, 'plot sin(x)');
    await expect(page.getByTestId('artifact-preview').getByRole('img', { name: 'figure.png' })).toBeAttached();
    await expect(page.getByTestId('artifact-panel')).toBeHidden();
  });

  test('a PDF card opens the panel when clicked', async ({ page }) => {
    await installMocks(page, { chatScript: sandboxFileReply({ id: 'a-pdf', filename: 'paper.pdf', content_type: 'application/pdf', size_bytes: 2097152 }) });
    await page.goto('/');
    await sendFor(page, 'compile my latex');
    const card = page.getByTestId('artifact-card');
    await expect(card).toContainText('paper.pdf');
    await expect(page.getByTestId('artifact-panel')).toBeHidden();
    await card.click();
    await expect(page.getByTestId('artifact-panel').getByRole('heading', { name: 'paper.pdf' })).toBeVisible();
  });
});
