import { test, expect } from '@playwright/test';
import { installMocks } from './fixtures/mock';
import { simpleReply, toolReply, errorReply } from './fixtures/scenarios';

test.describe('core chat', () => {
  test('composer renders; send is gated on input', async ({ page }) => {
    await installMocks(page);
    await page.goto('/');
    const box = page.getByTestId('composer-textarea');
    await expect(box).toBeVisible();
    await expect(page.getByTestId('composer-send')).toBeDisabled();
    await box.fill('Hello Munin');
    await expect(page.getByTestId('composer-send')).toBeEnabled();
  });

  test('sending shows the user message and the streamed assistant reply', async ({ page }) => {
    const state = await installMocks(page, { chatScript: simpleReply('Hello from Munin. This is a test reply.') });
    await page.goto('/');
    await page.getByTestId('composer-textarea').fill('Hi there');
    await page.getByTestId('composer-send').click();
    await expect(page.getByText('Hi there')).toBeVisible();
    await expect(page.getByText('Hello from Munin. This is a test reply.')).toBeVisible();
    expect(state.unmocked, `unmocked requests: ${state.unmocked.join(', ')}`).toEqual([]);
  });

  test('a tool-using reply renders the final answer', async ({ page }) => {
    await installMocks(page, { chatScript: toolReply() });
    await page.goto('/');
    await page.getByTestId('composer-textarea').fill('what is protein folding');
    await page.getByTestId('composer-send').click();
    await expect(page.getByText('protein folding is well studied')).toBeVisible();
  });

  test('a mid-stream error surfaces, not a silent hang', async ({ page }) => {
    await installMocks(page, { chatScript: errorReply() });
    await page.goto('/');
    await page.getByTestId('composer-textarea').fill('trigger error');
    await page.getByTestId('composer-send').click();
    await expect(page.getByText(/vLLM unavailable|error|something went wrong/i).first()).toBeVisible();
  });
});
