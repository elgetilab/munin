import { test, expect, type Frame, type Page } from '@playwright/test';
import { installMocks } from './fixtures/mock';
import { ev, tokens } from './fixtures/sse';

// The real viewer in a real browser: 3Dmol.js, WebGL and the sandboxed frame.
// Only the network is mocked.
const WIDGET_TYPE = 'application/vnd.munin.widget+json';
const ID = 'art_protein';
const SPEC = { widget: 'protein', source: 'pdb', id: '1CRN', style: 'cartoon', color: 'spectrum', highlight: [] };

// With or without ?version=, and not the /structure route beneath it.
const ARTIFACT_URL = new RegExp(`/api/chats/conv-e2e/artifacts/${ID}(\\?|$)`);

// A short alpha helix, as C-alpha atoms.
const PDB = Array.from({ length: 20 }, (_, i) => {
  const angle = (i * 100 * Math.PI) / 180;
  const n = (v: number) => v.toFixed(3).padStart(8);
  return `ATOM  ${String(i + 1).padStart(5)}  CA  ALA A${String(i + 1).padStart(4)}    ${n(2.3 * Math.cos(angle))}${n(2.3 * Math.sin(angle))}${n(1.5 * i)}  1.00 10.00           C`;
}).join('\n');

async function openViewer(page: Page) {
  const result = { id: ID, title: '1CRN: Crambin', content_type: WIDGET_TYPE, version: 1, source: 'model_written' };
  await installMocks(page, {
    chatScript: [
      ev.conversation('conv-e2e'), ev.routing('chat'),
      ev.toolCall('show_protein', { pdb_id: '1CRN' }, 'tc-protein'),
      ev.toolResult('tc-protein', result),
      ev.artifactCreated(result),
      ...tokens('Here is crambin.'),
      ev.done(),
    ],
  });
  await page.route(ARTIFACT_URL, route => route.fulfill({
    json: { ...result, latest_version: 1, content: JSON.stringify(SPEC) },
  }));
  await page.route(`**/api/chats/conv-e2e/artifacts/${ID}/structure*`, route => route.fulfill({
    json: { format: 'pdb', data: PDB },
  }));
  await page.goto('/');
  await page.getByTestId('composer-textarea').fill('show me crambin');
  await page.getByTestId('composer-send').click();
  await expect(page.getByTestId('protein-viewer')).toHaveAttribute('data-status', 'rendered', { timeout: 20000 });
}

async function widgetFrame(page: Page): Promise<Frame> {
  const handle = await page.getByTestId('widget-frame').first().elementHandle();
  const frame = await handle!.contentFrame();
  return frame!;
}

test.describe('protein viewer', () => {
  test('the controls show keyboard focus despite outline-none', async ({ page }) => {
    await openViewer(page);
    const viewer = page.getByTestId('protein-viewer');
    await viewer.getByRole('button', { name: 'Spin' }).focus();
    await page.keyboard.press('Shift+Tab');
    const color = viewer.getByLabel('Color');
    await expect(color).toBeFocused();
    await expect(color).toHaveClass(/outline-none/);
    await expect(color).toHaveCSS('outline', 'rgb(88, 166, 255) solid 2px');
    await page.keyboard.press('Tab');
    await expect(viewer.getByRole('button', { name: 'Spin' })).toHaveCSS('outline', 'rgb(88, 166, 255) solid 2px');
  });

  test('renders in the message and leaves the panel closed', async ({ page }) => {
    await openViewer(page);
    await expect(page.getByTestId('artifact-panel')).toBeHidden();
    await expect((await widgetFrame(page)).locator('canvas').first()).toBeVisible();
  });

  test('the controls keep it rendering, and Expand opens the panel', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', e => errors.push(e.message));
    await openViewer(page);
    const viewer = page.getByTestId('protein-viewer');
    for (const style of ['stick', 'sphere', 'line', 'cartoon']) await viewer.getByLabel('Style').selectOption(style);
    for (const color of ['chain', 'secondary', 'bfactor', 'spectrum']) await viewer.getByLabel('Color').selectOption(color);
    await viewer.getByRole('button', { name: 'Spin' }).click();
    await viewer.getByRole('button', { name: 'Stop' }).click();
    await viewer.getByRole('button', { name: 'Reset view' }).click();
    await expect(viewer).toHaveAttribute('data-status', 'rendered');

    await viewer.getByRole('button', { name: 'Expand' }).click();
    await expect(page.getByTestId('artifact-panel').getByTestId('protein-viewer')).toHaveAttribute('data-status', 'rendered', { timeout: 20000 });
    expect(errors).toEqual([]);
  });

  test('a structure that cannot load shows why, with no broken viewer', async ({ page }) => {
    await installMocks(page, {
      chatScript: [
        ev.conversation('conv-e2e'),
        ev.toolCall('show_protein', { pdb_id: '1CRN' }, 'tc-protein'),
        ev.toolResult('tc-protein', { id: ID, title: 'Crambin', content_type: WIDGET_TYPE, version: 1 }),
        ev.done(),
      ],
    });
    await page.route(ARTIFACT_URL, route => route.fulfill({
      json: { id: ID, content: JSON.stringify(SPEC) },
    }));
    await page.route(`**/api/chats/conv-e2e/artifacts/${ID}/structure*`, route => route.fulfill({
      status: 502,
      json: { detail: { error: { message: 'could not reach the structure database' } } },
    }));
    await page.goto('/');
    await page.getByTestId('composer-textarea').fill('show me crambin');
    await page.getByTestId('composer-send').click();
    await expect(page.getByRole('alert')).toContainText('could not reach the structure database');
    await expect(page.getByTestId('widget-frame')).toHaveCount(0);
  });
});

test.describe('widget sandbox', () => {
  test('code in the frame cannot reach the app, its cookies or the network', async ({ page, context }) => {
    await openViewer(page);
    await context.addCookies([{ name: 'session', value: 'secret', url: new URL(page.url()).origin }]);
    const frame = await widgetFrame(page);
    // The browser reports a request it then refuses, so what counts is the reason
    // each one failed. Anything allowed through would reach these routes instead.
    const refusals: string[] = [];
    const delivered: string[] = [];
    page.on('requestfailed', r => { if (r.frame() === frame) refusals.push(r.failure()?.errorText ?? ''); });
    await page.route('https://example.org/**', route => { delivered.push(route.request().url()); return route.abort(); });
    await page.route('**/api/chats', route => {
      if (route.request().frame() === frame) delivered.push(route.request().url());
      return route.fallback();
    });

    const probes = await frame.evaluate(async (origin: string) => {
      const blocked = async (attempt: () => unknown) => {
        try {
          await attempt();
          return false;
        } catch {
          return true;
        }
      };
      return {
        origin: window.origin,
        cookie: await blocked(() => document.cookie),
        storage: await blocked(() => window.localStorage.getItem('x')),
        parentDom: await blocked(() => window.parent.document.body),
        parentLocation: await blocked(() => window.parent.location.href),
        fetchApp: await blocked(() => fetch(`${origin}/api/chats`, { credentials: 'include' })),
        fetchOutside: await blocked(() => fetch('https://example.org/')),
        xhr: await blocked(() => new Promise((resolve, reject) => {
          const request = new XMLHttpRequest();
          request.open('GET', 'https://example.org/');
          request.onload = resolve;
          request.onerror = reject;
          request.send();
        })),
        image: await blocked(() => new Promise((resolve, reject) => {
          const image = new Image();
          image.onload = resolve;
          image.onerror = reject;
          image.src = 'https://example.org/x.png';
        })),
        script: await blocked(() => new Promise((resolve, reject) => {
          const script = document.createElement('script');
          script.onload = resolve;
          script.onerror = reject;
          script.src = 'https://example.org/x.js';
          document.head.appendChild(script);
        })),
      };
    }, new URL(page.url()).origin);

    expect(probes).toEqual({
      origin: 'null',
      cookie: true,
      storage: true,
      parentDom: true,
      parentLocation: true,
      fetchApp: true,
      fetchOutside: true,
      xhr: true,
      image: true,
      script: true,
    });
    expect(delivered).toEqual([]);
    expect(refusals.filter(reason => reason !== 'csp')).toEqual([]);
  });

  test('a message from the frame cannot steer the app', async ({ page }) => {
    await openViewer(page);
    const frame = await widgetFrame(page);
    await frame.evaluate(() => {
      window.parent.postMessage({ type: 'error', message: '<img src=x onerror=alert(1)>' }, '*');
    });
    // Shown as plain text, never as markup.
    await expect(page.getByRole('alert')).toHaveText('<img src=x onerror=alert(1)>');
    await expect(page.getByRole('alert').locator('img')).toHaveCount(0);
  });
});
