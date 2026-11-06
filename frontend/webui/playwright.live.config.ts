import { defineConfig, devices } from '@playwright/test';

// Checks against the REAL deployed site with a real account, unlike
// playwright.config.ts. Never part of CI: these create and delete real chats and
// documents, spend real model tokens, and end by signing the test session out.
//
// MUNIN_LIVE_STATE names a file outside the repo that holds a session cookie, so
// treat it as a password and delete it afterwards.
//
//   Sign in by hand, which requests the login mail yourself (once per run):
//     npx playwright test -c playwright.live.config.ts --project=login --headed
//   Then run the checks:
//     npx playwright test -c playwright.live.config.ts --project=live
const statePath = process.env.MUNIN_LIVE_STATE;
if (!statePath) throw new Error('Set MUNIN_LIVE_STATE to a file outside the repo');

export default defineConfig({
  testDir: './e2e-live',
  outputDir: process.env.MUNIN_LIVE_OUTPUT ?? 'test-results-live',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: 'list',
  use: {
    ...devices['Desktop Chrome'],
    baseURL: process.env.MUNIN_LIVE_URL ?? 'https://chat.muninai.org',
    // Traces would store the session cookie and the account's chats.
    trace: 'off',
    actionTimeout: 15_000,
  },
  projects: [
    { name: 'login', testMatch: 'login.setup.ts', timeout: 600_000 },
    { name: 'live', testMatch: 'live.spec.ts', timeout: 240_000, use: { storageState: statePath } },
  ],
});
