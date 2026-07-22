import { test, expect } from '@playwright/test';
import { installMocks } from './fixtures/mock';
import { conversationSummary, artifactSummary } from './fixtures/scenarios';

// Regression guards for the bugs found by hand-testing on 2026-07-22.

test.describe('regressions', () => {
  // Bug a: starting Deep Research in a fresh chat opened an empty window - the
  // user never saw their question. The backend now records the question as a
  // user message; the client switches to that conversation and shows it.
  test('deep research in a fresh chat shows the question in the conversation', async ({ page }) => {
    await installMocks(page);
    await page.goto('/');
    await page.getByTestId('composer-attach').click();
    await page.getByTestId('attach-deep-research').click();
    const q = 'How do kinase inhibitors partition into lipid bilayers?';
    await page.getByTestId('composer-textarea').fill(q);
    await page.getByTestId('composer-send').click();
    // The main conversation area shows the question as a sent message, and the
    // research renders inline below it.
    await expect(page.getByText(q)).toBeVisible();
    await expect(page.getByTestId('research-timeline')).toBeVisible();
  });

  // Bug c: switching conversations kept the previous conversation's selected
  // artifact id, so the panel refetched it against the new conversation and got
  // "artifact not found". Selection now resets on switch.
  test('switching conversations does not raise an artifact-not-found error', async ({ page }) => {
    const artA = artifactSummary('art-A', 'Report A');
    await installMocks(page, {
      conversations: [conversationSummary('cA', 'Conversation A'), conversationSummary('cB', 'Conversation B')],
      perConversationArtifacts: { cA: [artA], cB: [] },
    });
    await page.goto('/');

    // Open conversation A and select its artifact (detail view).
    await page.getByTestId('chat-row').filter({ hasText: 'Conversation A' }).click();
    await expect(page.getByTestId('artifacts-button')).toContainText('Artifacts (1)');
    await page.getByTestId('artifacts-button').click();
    await page.getByTestId('artifact-item').filter({ hasText: 'Report A' }).click();
    await expect(page.getByTestId('artifact-panel').getByRole('heading', { name: 'Report A' })).toBeVisible();

    // Switch to conversation B. The stale selection must not be refetched.
    await page.getByTestId('chat-row').filter({ hasText: 'Conversation B' }).click();
    await expect(page.getByText(/artifact not found/i)).toHaveCount(0);
    await expect(page.getByText(/Failed to load artifact/i)).toHaveCount(0);
  });
});
