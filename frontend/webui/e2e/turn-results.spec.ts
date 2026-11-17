import { test, expect } from '@playwright/test';
import { installMocks } from './fixtures/mock';
import { ev, tokens, type SseEvent } from './fixtures/sse';

// Regression (docs/agent-track/TURN-RESULTS-PLAN.md, chat 13550817): a turn in
// which the model corrects itself must show ONE result and ONE answer. The
// wrong first attempt and the narration written before the tool calls belong
// in the work log, not in the message. Unit tests: src/turnResults.test.tsx;
// backend: backend/retrieval/tests/test_turn_results.py.

const STEP1 = "I'll write the script. It reads the file with the old API (version 1).";
const STEP2 = 'That API was wrong for this file format, so I rewrote it.';
const ANSWER = 'Here is the corrected unpacker: it streams the archive and writes both output files.';

const card = (id: string) => ({ id, title: 'unpacker.py', content_type: 'text/x-python', version: 1 });

function selfCorrectingTurn(): SseEvent[] {
  return [
    ev.conversation('conv-e2e'), ev.routing('code'),
    ...tokens(STEP1),
    { event: 'tool_call', data: { id: 'c1', name: 'create_artifact', arguments: { title: 'unpacker.py' } } },
    { event: 'tool_result', data: { id: 'c1', name: 'create_artifact', result: card('art_1'), duration_ms: 20 } },
    ...tokens(STEP2),
    { event: 'tool_call', data: { id: 'c2', name: 'create_artifact', arguments: { title: 'unpacker.py', replaces: ['art_1'] } } },
    { event: 'tool_result', data: { id: 'c2', name: 'create_artifact', result: card('art_2'), duration_ms: 20 } },
    { event: 'artifact_superseded', data: { artifact_id: 'art_1', superseded_by: 'art_2', tool_call_id: 'c1' } },
    ...tokens(ANSWER),
    { event: 'done', data: { finish_reason: 'stop', terminal_reason: 'done', narration_in_log: true } },
  ];
}

test.describe('self-correcting turn', () => {
  test('shows one result and only the answer; the attempt and narration sit in the work log', async ({ page }) => {
    await installMocks(page, { chatScript: selfCorrectingTurn() });
    await page.goto('/');
    await page.getByTestId('composer-textarea').fill('write me an unpacker');
    await page.getByTestId('composer-send').click();

    const reply = page.locator('[data-testid="inline-artifacts"]').last();
    await expect(reply).toBeVisible();
    await expect(page.getByTestId('artifact-card')).toHaveCount(1);

    // The answer is in the message; the abandoned first attempt is not.
    await expect(page.getByText(ANSWER)).toBeVisible();
    await expect(page.getByText(STEP1)).toBeHidden();

    // The work log holds both narration steps and the replaced attempt.
    await page.getByTestId('activity-line').last().click();
    await expect(page.getByTestId('step-text')).toHaveText([STEP1, STEP2]);
    await expect(page.getByTestId('superseded-note')).toContainText('Earlier attempt (replaced): unpacker.py');
  });
});
