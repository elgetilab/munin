import { test, expect } from '@playwright/test';
import { installMocks } from './fixtures/mock';
import { toolReply, clarificationReply } from './fixtures/scenarios';

test.describe('tools + attach + clarification', () => {
  test('the + menu lists the attach options and Deep Research', async ({ page }) => {
    await installMocks(page);
    await page.goto('/');
    await page.getByTestId('composer-attach').click();
    await expect(page.getByRole('button', { name: /Upload file/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /Upload image/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /Attach knowledge/i })).toBeVisible();
    await expect(page.getByTestId('attach-deep-research')).toBeVisible();
  });

  test('attach knowledge opens the group/topic/contributor picker', async ({ page }) => {
    await installMocks(page);
    await page.goto('/');
    await page.getByTestId('composer-attach').click();
    await page.getByRole('button', { name: /Attach knowledge/i }).click();
    await expect(page.getByPlaceholder(/Search groups, topics, contributors/i)).toBeVisible();
  });

  test('a web_search answer renders after the tool runs', async ({ page }) => {
    await installMocks(page, { chatScript: toolReply() });
    await page.goto('/');
    await page.getByTestId('composer-textarea').fill('what is protein folding');
    await page.getByTestId('composer-send').click();
    await expect(page.getByText('protein folding is well studied')).toBeVisible();
  });

  test('an ask_clarification turn renders the question and options', async ({ page }) => {
    await installMocks(page, { chatScript: clarificationReply() });
    await page.goto('/');
    await page.getByTestId('composer-textarea').fill('make me a game');
    await page.getByTestId('composer-send').click();
    await expect(page.getByText('Which style?')).toBeVisible();
    await expect(page.getByText('Single player')).toBeVisible();
  });
});
