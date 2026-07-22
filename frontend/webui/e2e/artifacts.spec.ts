import { test, expect } from '@playwright/test';
import { installMocks } from './fixtures/mock';
import { artifactReply } from './fixtures/scenarios';

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
    await page.getByTestId('artifact-panel').getByRole('button', { name: '←' }).click();
    await expect(page.getByTestId('artifact-item').filter({ hasText: 'Report One' })).toBeVisible();
  });
});
