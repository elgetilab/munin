import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// From Node 25 the runtime has its own global localStorage, which hides jsdom's,
// so every test that touches storage fails with "setItem is not a function".
// Turn it off there; CI and the Docker build run Node 22, which leaves it off.
const nodeMajor = Number(process.versions.node.split('.')[0]);
const execArgv = nodeMajor >= 25 ? ['--no-experimental-webstorage'] : [];

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    // Run as if served from the reference deployment's chat host, so the
    // runtime-derived site URLs (src/lib/urls.ts) are the ones the msw
    // handlers mock, and urls.test.ts pins what production resolves.
    environmentOptions: { jsdom: { url: 'https://chat.muninai.org/' } },
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    css: false,
    execArgv,
  },
});
