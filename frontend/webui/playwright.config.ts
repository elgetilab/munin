import { defineConfig, devices } from '@playwright/test';

// Browser E2E tests (mocked network) for the chat UI. They run against the real
// production bundle served by `vite preview`, with all /api + /auth traffic
// intercepted in-test (see e2e/fixtures/mock.ts), so no backend/GPU/auth is
// needed and runs are deterministic.
const PORT = 4173;

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.spec.ts',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: 'on-first-retry',
    // The app calls /auth/me and /api/* on boot; tests stub these. Fail fast if
    // a real request somehow escapes the mocks.
    actionTimeout: 10_000,
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: {
    command: `npm run build && npm run preview -- --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
