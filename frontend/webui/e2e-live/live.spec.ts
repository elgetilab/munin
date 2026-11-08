import { readFileSync } from 'node:fs';
import { test, expect, type Page } from '@playwright/test';

// See playwright.live.config.ts. The tests run in file order, and each cleans up
// what it creates, because Sign out comes last and ends the session.

const RUN = 'playwright-live-check';

async function accountEmail(page: Page): Promise<string> {
  const origin = new URL(page.url()).hostname.split('.').slice(1).join('.');
  const res = await page.request.get(`https://auth.${origin}/auth/me`);
  expect(res.ok(), 'signed in').toBeTruthy();
  return (await res.json()).email;
}

async function openAccountMenu(page: Page) {
  await page.getByText(await accountEmail(page)).first().click();
}

function chatIdFrom(page: Page): string {
  const match = new URL(page.url()).pathname.match(/^\/c\/(.+)$/);
  expect(match, 'the chat has its own address').toBeTruthy();
  return match![1];
}

async function deleteChat(page: Page, id: string | null) {
  if (id) await page.request.delete(`/api/chats/${id}`);
}

interface Doc { id?: string; document_id?: string; filename: string }

async function docsNamed(page: Page, filename: string): Promise<Doc[]> {
  // An upload in progress holds up retrieval, so this can wait a long time.
  const res = await page.request.get('/api/documents', { timeout: 120_000 });
  if (!res.ok()) return [];
  const { documents } = await res.json() as { documents: Doc[] };
  return documents.filter((d) => d.filename === filename);
}

async function deleteDocs(page: Page, filename: string) {
  for (const d of await docsNamed(page, filename)) {
    await page.request.delete(`/api/documents/${d.document_id ?? d.id}`, { timeout: 120_000 });
  }
}

test('the app starts with no server errors or console errors', async ({ page }) => {
  const bad: string[] = [];
  const html = page.waitForResponse((r) => r.request().isNavigationRequest());
  page.on('response', (r) => { if (r.status() >= 500) bad.push(`${r.status()} ${new URL(r.url()).pathname}`); });
  page.on('pageerror', (e) => bad.push(`page error: ${e.message}`));
  await page.goto('/');
  await expect(page.getByTestId('composer-textarea')).toBeVisible();
  await page.waitForLoadState('networkidle');
  expect(bad).toEqual([]);
  // Without this the browser reuses the page after sign out, without asking Caddy.
  expect((await html).headers()['cache-control']).toBe('no-cache');
});

test('the sidebar lists real chats, with Show more and Archived when there are any', async ({ page }) => {
  await page.goto('/');
  await page.waitForLoadState('networkidle');
  const rows = page.getByTestId('chat-row');
  const before = await rows.count();
  test.info().annotations.push({ type: 'chats shown', description: String(before) });

  const more = page.getByRole('button', { name: 'Show more' }).first();
  if (await more.isVisible()) {
    await more.click();
    await expect.poll(() => rows.count()).toBeGreaterThan(before);
    test.info().annotations.push({ type: 'Show more', description: 'exercised' });
  } else {
    test.info().annotations.push({ type: 'Show more', description: 'not offered: too few chats' });
  }

  const archived = page.getByRole('button', { name: /^Archived \(/ });
  if (await archived.isVisible()) {
    await archived.click();
    await expect(page.getByTestId('archived-project').first()).toBeVisible();
    test.info().annotations.push({ type: 'Archived', description: 'exercised' });
  } else {
    test.info().annotations.push({ type: 'Archived', description: 'not offered: none archived' });
  }
});

test('a chat link that does not exist shows the error and a way out', async ({ page }) => {
  await page.goto('/c/00000000-0000-4000-8000-000000000000');
  await expect(page.getByText('This chat could not be opened', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: 'Start a new chat' }).click();
  await expect(page).toHaveURL(/\/$/);
});

test('a real reply arrives, survives a reload, and Back behaves around Settings', async ({ page }) => {
  let id: string | null = null;
  try {
    await page.goto('/');
    await page.getByTestId('composer-textarea').fill(`${RUN}: reply with exactly the word READY and nothing else.`);
    await page.getByTestId('composer-send').click();
    await expect(page.getByText('READY', { exact: true })).toBeVisible({ timeout: 120_000 });
    await expect(page.getByTestId('composer-stop')).toBeHidden({ timeout: 60_000 });
    id = chatIdFrom(page);

    await page.reload();
    await expect(page.getByText('READY', { exact: true })).toBeVisible();

    await openAccountMenu(page);
    await page.getByRole('menuitem', { name: 'Settings' }).click();
    const settings = page.getByRole('heading', { name: 'Settings' });
    await expect(settings).toBeVisible();
    await page.getByRole('button', { name: 'Temporary chat' }).click();
    await expect(settings).toBeHidden();
    await expect(page).toHaveURL(/\/$/);
    await page.goBack();
    await expect(page).toHaveURL(new RegExp(`/c/${id}$`));
    await expect(settings).toBeHidden();
  } finally {
    await deleteChat(page, id);
  }
});

test('a small document uploads and is ready to send', async ({ page }) => {
  const name = `${RUN}-small.txt`;
  try {
    await page.goto('/');
    await page.locator('input[type="file"]').first().setInputFiles({
      name, mimeType: 'text/plain', buffer: Buffer.from('A short note for the live upload check.\n'),
    });
    await expect(page.getByText(`${name} (ready to send)`)).toBeVisible({ timeout: 120_000 });
  } finally {
    await deleteDocs(page, name);
  }
});

test('during an upload, retrieval keeps answering other requests', async ({ page }) => {
  const name = `${RUN}-busy.txt`;
  const line = 'Proteins fold into shapes that decide what they can do in a cell. ';
  // About 260 KB, so the server is still extracting and embedding for a while
  // after the bytes are sent. At 900 lines it was done before the first probe.
  const body = Array.from({ length: 3600 }, (_, i) => `${i} ${line}`).join('\n');
  try {
    await page.goto('/');
    await page.locator('input[type="file"]').first().setInputFiles({
      name, mimeType: 'text/plain', buffer: Buffer.from(body),
    });
    const ready = page.getByText(`${name} (ready to send)`);

    // Probe from the moment the file is picked until it is ready, so a quick
    // upload cannot slip past the loop. /api/chats goes all the way to retrieval.
    let worst = 0;
    let probes = 0;
    const deadline = Date.now() + 180_000;
    while (!(await ready.isVisible())) {
      expect(Date.now(), 'upload finished within 3 minutes').toBeLessThan(deadline);
      const started = Date.now();
      const res = await page.request.get('/api/chats?limit=1', { timeout: 60_000 });
      expect(res.ok()).toBeTruthy();
      worst = Math.max(worst, Date.now() - started);
      probes += 1;
      await page.waitForTimeout(500);
    }
    test.info().annotations.push({ type: 'slowest request during upload', description: `${worst} ms over ${probes} requests` });
    expect(probes, 'requests timed while the upload ran').toBeGreaterThan(0);
    expect(worst).toBeLessThan(10_000);
  } finally {
    await deleteDocs(page, name);
  }
});

test('Cancel during an upload removes the document on the server too', async ({ page }) => {
  const name = `${RUN}-cancel.txt`;
  // Enough text that the server is still embedding when Cancel arrives, and small
  // enough to hold up retrieval for only a few seconds.
  const line = 'Proteins fold into shapes that decide what they can do in a cell. ';
  const body = Array.from({ length: 900 }, (_, i) => `${i} ${line}`).join('\n');
  try {
    await page.goto('/');
    await page.locator('input[type="file"]').first().setInputFiles({
      name, mimeType: 'text/plain', buffer: Buffer.from(body),
    });
    await expect(page.getByText(/\.\.\. 100%/)).toBeVisible({ timeout: 30_000 });
    await page.getByRole('button', { name: 'Cancel upload' }).click();
    await expect(page.getByText(/Uploading /)).toBeHidden();
    await expect(page.getByText(/server may have kept it/)).toBeHidden();

    // The file is listed until embedding, which cannot be interrupted, has finished.
    await expect.poll(() => docsNamed(page, name), { timeout: 120_000, intervals: [2_000] }).toEqual([]);
    await page.waitForTimeout(10_000);
    expect(await docsNamed(page, name), 'and it stays gone').toEqual([]);
  } finally {
    await deleteDocs(page, name);
  }
});

test('the ? button is hidden on a Knowledge browse page only, and Help stays in the menu', async ({ page }) => {
  const button = page.locator('#munin-support-btn');
  await page.goto('/knowledge');
  await expect(button).toBeVisible({ timeout: 20_000 });
  await page.goto('/knowledge/topic/example');
  await expect(page.locator('body.munin-browsing-knowledge')).toHaveCount(1);
  await expect(button).toBeHidden();
  await page.goto('/');
  await expect(button).toBeVisible({ timeout: 20_000 });
  await openAccountMenu(page);
  await expect(page.getByRole('menuitem', { name: 'Help' })).toBeVisible();
});

test('a real sandbox file downloads under its own name with its content', async ({ page }) => {
  let id: string | null = null;
  try {
    await page.goto('/');
    await page.getByTestId('composer-textarea').fill(
      `${RUN}: use the code sandbox to create a CSV file called live-check.csv with the header a,b and the row 1,2, and give it to me as a downloadable file.`,
    );
    await page.getByTestId('composer-send').click();
    await expect(page.getByTestId('artifacts-button')).toContainText('Artifacts (', { timeout: 200_000 });
    id = chatIdFrom(page);
    await expect(page.getByTestId('composer-stop')).toBeHidden({ timeout: 200_000 });

    const panel = page.getByTestId('artifact-panel');
    if (!await panel.isVisible()) await page.getByTestId('artifacts-button').click();
    const listed = panel.getByText(/live-check/).first();
    if (await listed.isVisible()) await listed.click();

    const [download] = await Promise.all([
      page.waitForEvent('download', { timeout: 30_000 }),
      panel.getByRole('button', { name: /Download/i }).first().click(),
    ]);
    const filename = download.suggestedFilename();
    test.info().annotations.push({ type: 'downloaded as', description: filename });
    expect(filename).toMatch(/live-check/);
    expect(filename, 'no doubled extension').not.toMatch(/\.(\w+)\.\1$/);
    const content = readFileSync(await download.path(), 'utf-8');
    expect(content).toContain('a,b');
    expect(content).toContain('1,2');
  } finally {
    await deleteChat(page, id);
  }
});

test('on a phone, Admin and Knowledge do not scroll the page sideways', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const sideways = () => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);

  await page.goto('/knowledge');
  await page.waitForLoadState('networkidle');
  expect(await sideways(), 'Knowledge').toBeLessThanOrEqual(0);

  await page.goto('/');
  await page.getByRole('button', { name: 'Show sidebar' }).click();
  await openAccountMenu(page);
  const admin = page.getByRole('menuitem', { name: /Admin/ });
  if (await admin.isVisible()) {
    await admin.click();
    await page.waitForLoadState('networkidle');
    expect(await sideways(), 'Admin').toBeLessThanOrEqual(0);
    test.info().annotations.push({ type: 'Admin', description: 'exercised' });
  } else {
    test.info().annotations.push({ type: 'Admin', description: 'not offered: not an admin account' });
  }
});

// Must stay last: it ends the session every test above uses.
test('Sign out ends the session on the server and returns to the login page', async ({ page }) => {
  await page.goto('/');
  const session = (await page.context().cookies()).find((c) => c.name === 'munin_session');
  expect(session, 'session cookie').toBeTruthy();
  await openAccountMenu(page);
  await page.getByRole('menuitem', { name: 'Sign out' }).click();
  await expect(page).toHaveURL(/auth\.[^/]+\/login/);

  // Sent by hand, because the browser has dropped the cookie: the server itself
  // must refuse it, not just the browser forget it.
  const check = await page.request.get(page.url().replace(/\/login.*$/, '/auth/me'), {
    headers: { Cookie: `munin_session=${session!.value}` },
  });
  expect(check.status(), 'the session is gone').toBe(401);

  await page.goto(test.info().project.use.baseURL as string);
  await expect(page).toHaveURL(/auth\.[^/]+\/login/);
});
