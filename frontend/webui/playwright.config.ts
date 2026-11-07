import { defineConfig, devices } from '@playwright/test';

// Browser E2E tests (mocked network) for the chat UI. They run against the real
// production bundle served by `vite preview`, with all /api + /auth traffic
// intercepted in-test (see e2e/fixtures/mock.ts), so no backend/GPU/auth is
// needed and runs are deterministic.
//
// MUNIN_E2E_PORT moves the preview server off 4173, so two runs on one machine
// do not share (or fight over) one server.
const PORT = Number(process.env.MUNIN_E2E_PORT) || 4173;

// The theme comes from the munin_theme cookie, and dark is the default without
// one (src/lib/theme.ts). The system setting is pinned too, so a test that picks
// "Match system" knows what it gets. Cookies ignore ports, so this one also
// reaches a server on MUNIN_E2E_PORT.
const lightCookie = {
  cookies: [{
    name: 'munin_theme', value: 'light', domain: 'localhost', path: '/',
    expires: -1, httpOnly: false, secure: false, sameSite: 'Lax' as const,
  }],
  origins: [],
};

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
    { name: 'chromium', use: { ...devices['Desktop Chrome'], colorScheme: 'dark' } },
    {
      // Every spec again, in light. theme.spec.ts sets the theme itself in each
      // test, so it runs once, above.
      name: 'chromium-light',
      testIgnore: '**/theme.spec.ts',
      use: { ...devices['Desktop Chrome'], colorScheme: 'light', storageState: lightCookie },
    },
  ],
  webServer: {
    command: `npm run build && npm run preview -- --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
