import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

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
  },
});
