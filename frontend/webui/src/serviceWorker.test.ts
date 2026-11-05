import { describe, expect, it, vi } from 'vitest';
import source from '../public/sw.js?raw';

type FetchEvent = {
  request: { url: string; mode: string };
  respondWith: (response: Promise<unknown>) => void;
};

// Runs sw.js against stand-ins and returns what one fetch event answered with.
async function answer(url: string, network: () => Promise<unknown>, cached: unknown) {
  const handlers: Record<string, (event: FetchEvent) => void> = {};
  const self = { addEventListener: (name: string, fn: (event: FetchEvent) => void) => { handlers[name] = fn; } };
  const fetchStub = vi.fn(network);
  const caches = {
    match: vi.fn(async () => cached),
    open: vi.fn(async () => ({ put: vi.fn() })),
  };
  new Function('self', 'fetch', 'caches', source)(self, fetchStub, caches);

  let result: Promise<unknown> | undefined;
  handlers.fetch({
    request: { url, mode: 'no-cors' },
    respondWith: (response) => { result = response; },
  });
  return { response: await result, fetchStub };
}

const fresh = { ok: true, type: 'basic', clone: () => ({}) };

describe('service worker', () => {
  it('asks the network first for /shared, because those names do not change', async () => {
    const { response, fetchStub } = await answer(
      'https://chat.example.org/shared/support-widget.js', async () => fresh, 'stale copy');

    expect(response).toBe(fresh);
    expect(fetchStub).toHaveBeenCalledTimes(1);
    expect(fetchStub.mock.calls[0]).toEqual([expect.anything(), { cache: 'no-cache' }]);
  });

  it('falls back to the cached /shared file when offline', async () => {
    const { response } = await answer(
      'https://chat.example.org/shared/support-widget.js',
      async () => { throw new TypeError('offline'); },
      'last good copy');

    expect(response).toBe('last good copy');
  });

  it('keeps answering hashed assets from the cache', async () => {
    const { response, fetchStub } = await answer(
      'https://chat.example.org/assets/index-abc.js', async () => fresh, 'cached bundle');

    expect(response).toBe('cached bundle');
    expect(fetchStub).not.toHaveBeenCalled();
  });
});
