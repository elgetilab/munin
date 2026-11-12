import { test, expect } from '@playwright/test';
import { installMocks } from './fixtures/mock';
import { conversationSummary, artifactSummary, simpleReply } from './fixtures/scenarios';
import { buildSSE } from './fixtures/sse';

// Regression guards for the bugs found by hand-testing on 2026-07-22.

// Overlays and the browser history (lib/nav.ts) need real history traversal.
test.describe('overlays and Back', () => {
  test('Settings under the phone drawer does not come back after New chat', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await installMocks(page, { conversations: [conversationSummary('cA', 'Conversation A')] });
    await page.goto('/c/cA');

    const showSidebar = page.getByRole('button', { name: 'Show sidebar' });
    await showSidebar.click();
    await page.getByText('e2e@test.local').click();
    await page.getByRole('menuitem', { name: 'Settings' }).click();
    await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible();

    await showSidebar.click();
    await page.getByRole('link', { name: 'New chat' }).click();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole('heading', { name: 'Settings' })).toBeHidden();

    await page.goBack();
    await expect(page).toHaveURL(/\/c\/cA$/);
    await expect(page.getByRole('heading', { name: 'Settings' })).toBeHidden();
  });

  test('Settings opened before a new chat has its id: Back closes it and keeps the chat', async ({ page }) => {
    await installMocks(page);
    let release = () => {};
    const held = new Promise<void>((resolve) => { release = resolve; });
    await page.route('**/api/chat/completions', async (route) => {
      await held;
      await route.fulfill({ status: 200, contentType: 'text/event-stream', body: buildSSE(simpleReply('The kept reply.')) });
    });
    await page.goto('/');

    await page.getByTestId('composer-textarea').fill('First question');
    await page.getByTestId('composer-send').click();
    await page.getByText('e2e@test.local').click();
    await page.getByRole('menuitem', { name: 'Settings' }).click();
    const heading = page.getByRole('heading', { name: 'Settings' });
    await expect(heading).toBeVisible();

    release();
    await expect(page).toHaveURL(/\/c\/conv-e2e$/);
    await expect(heading).toBeVisible();

    await page.goBack();
    await expect(heading).toBeHidden();
    await expect(page).toHaveURL(/\/c\/conv-e2e$/);
    await expect(page.getByText('First question')).toBeVisible();
  });
  test('Temporary chat closes Settings, so Back does not reopen the old chat with it', async ({ page }) => {
    await installMocks(page, { conversations: [conversationSummary('cA', 'Conversation A')] });
    await page.goto('/c/cA');
    await page.getByText('e2e@test.local').click();
    await page.getByRole('menuitem', { name: 'Settings' }).click();
    const heading = page.getByRole('heading', { name: 'Settings' });
    await expect(heading).toBeVisible();

    await page.getByRole('button', { name: 'Temporary chat' }).click();
    await expect(heading).toBeHidden();
    await expect(page).toHaveURL(/\/$/);

    await page.goBack();
    await expect(page).toHaveURL(/\/c\/cA$/);
    await expect(heading).toBeHidden();
  });
});

test.describe('an edited artifact beside the chat', () => {
  test('asks before the Sources pill or another chat takes the draft away', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await installMocks(page, {
      conversations: [conversationSummary('cA', 'Conversation A'), conversationSummary('cB', 'Conversation B')],
      perConversationArtifacts: { cA: [artifactSummary('art-A', 'Report A')], cB: [] },
    });
    await page.goto('/c/cA');
    await page.getByTestId('artifacts-button').click();
    await page.getByTestId('artifact-item').filter({ hasText: 'Report A' }).click();
    await page.getByRole('button', { name: 'Edit', exact: true }).click();
    const editor = page.getByRole('textbox', { name: 'Artifact content' });
    await editor.fill('A changed draft');

    const prompts: string[] = [];
    let accept = false;
    page.on('dialog', (dialog) => {
      prompts.push(dialog.message());
      void (accept ? dialog.accept() : dialog.dismiss());
    });

    await page.getByTestId('artifacts-button').click();
    await expect(editor).toHaveValue('A changed draft');
    await page.getByTestId('chat-row').filter({ hasText: 'Conversation B' }).click();
    await expect(page).toHaveURL(/\/c\/cA$/);
    await expect(editor).toHaveValue('A changed draft');
    expect(prompts).toEqual(['Discard your changes to this artifact?', 'Discard your changes to this artifact?']);

    accept = true;
    await page.getByTestId('chat-row').filter({ hasText: 'Conversation B' }).click();
    await expect(page).toHaveURL(/\/c\/cB$/);
    await expect(prompts).toHaveLength(3);
  });

  test('keeping the draft also keeps the view that is open beside it', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await installMocks(page, {
      conversations: [conversationSummary('cA', 'Conversation A'), conversationSummary('cB', 'Conversation B')],
      perConversationArtifacts: { cA: [artifactSummary('art-A', 'Report A')], cB: [] },
    });
    await page.goto('/c/cA');
    await page.getByTestId('artifacts-button').click();
    await page.getByTestId('artifact-item').filter({ hasText: 'Report A' }).click();
    await page.getByRole('button', { name: 'Edit', exact: true }).click();
    const editor = page.getByRole('textbox', { name: 'Artifact content' });
    await editor.fill('A changed draft');
    await page.getByText('e2e@test.local').click();
    await page.getByRole('menuitem', { name: 'Settings' }).click();
    const heading = page.getByRole('heading', { name: 'Settings' });
    await expect(heading).toBeVisible();

    page.on('dialog', (dialog) => { void dialog.dismiss(); });
    await page.getByTestId('chat-row').filter({ hasText: 'Conversation B' }).click();
    await expect(heading).toBeVisible();
    await page.getByRole('link', { name: 'New chat' }).click();
    await expect(heading).toBeVisible();
    await expect(page).toHaveURL(/\/c\/cA$/);
    await expect(editor).toHaveValue('A changed draft');
  });
});

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
    await expect(page.getByTestId('artifacts-button')).toHaveAccessibleName('Artifacts (1)');
    await page.getByTestId('artifacts-button').click();
    await page.getByTestId('artifact-item').filter({ hasText: 'Report A' }).click();
    await expect(page.getByTestId('artifact-panel').getByRole('heading', { name: 'Report A' })).toBeVisible();

    // Switch to conversation B. The stale selection must not be refetched.
    await page.getByTestId('chat-row').filter({ hasText: 'Conversation B' }).click();
    await expect(page.getByText(/artifact not found/i)).toHaveCount(0);
    await expect(page.getByText(/Failed to load artifact/i)).toHaveCount(0);
  });
});
