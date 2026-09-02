import { http, HttpResponse } from 'msw';
import { server } from '../test/msw-server';
import { streamChat, readActiveStream, clearActiveStream } from './api';
import type { SSEEvent } from './types';

/**
 * The dropped-connection round trip: streamChat's exponential-backoff
 * reconnect loop.
 *
 * WHY THIS FILE EXISTS. KNOWN-BUGS #2 ("SSE stream-resume endpoint failed for
 * users in production") was closed on the server side by driving the real
 * endpoint (backend/scripts/smoke-resume.py). The one item left open was the
 * browser half, whose stated verification was "start a long answer, drop WiFi
 * a few seconds". On a single-node cluster that means dropping the network
 * everyone else is using, so it never got done.
 *
 * It does not need a network. A mid-stream drop is a fetch body that ends
 * without a `done` event, which MSW can produce exactly. api.reconnect.test.ts
 * covers the wiring either side of the loop and says so explicitly:
 *
 *   "The exponential-backoff reconnect loop itself isn't unit-tested here
 *    (it would need fake timers + multi-fixture sequencing)"
 *
 * That loop IS the blip. This file supplies the fake timers and the
 * multi-fixture sequencing, so the scenario is checked on every test run
 * instead of once, by hand, at the cost of an outage.
 */

function sseBody(events: Array<{ event: string; data: unknown; id?: string }>): string {
  return events.map(e => {
    const idLine = e.id ? `id: ${e.id}\n` : '';
    return `${idLine}event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`;
  }).join('');
}

function sse(events: Array<{ event: string; data: unknown; id?: string }>): Response {
  return new Response(sseBody(events), {
    headers: { 'Content-Type': 'text/event-stream' },
  });
}

beforeEach(() => {
  try { localStorage.clear(); } catch { /* ignore */ }
  clearActiveStream();
});

describe('streamChat — dropped connection round trip', () => {
  it('resumes from Last-Event-ID after a mid-stream drop and delivers the rest', async () => {
    vi.useFakeTimers();
    try {
      // POST dies after two events: no `done`, which is what a blip looks like.
      server.use(
        http.post('/api/chat/completions', () => sse([
          { id: 's1-1', event: 'conversation', data: { id: 'c1', title: 'T', is_new: true, stream_id: 's1' } },
          { id: 's1-2', event: 'token', data: { content: 'before ' } },
        ])),
        // The reconnect must carry the checkpoint, and is served the tail.
        http.get('/api/chat/completions/resume', ({ request }) => {
          const url = new URL(request.url);
          if (url.searchParams.get('stream_id') !== 's1') {
            return new HttpResponse(null, { status: 404 });
          }
          if (request.headers.get('Last-Event-ID') !== 's1-2') {
            // Resuming from the wrong checkpoint would silently duplicate or
            // lose text, so fail loudly rather than serving the tail anyway.
            return new HttpResponse(null, { status: 400 });
          }
          return sse([
            { id: 's1-3', event: 'token', data: { content: 'after' } },
            { id: 's1-4', event: 'done', data: { finish_reason: 'stop' } },
          ]);
        }),
      );

      const events: SSEEvent[] = [];
      const p = streamChat(
        { persona: 'chat', messages: [], rag: { enabled: false }, stream: true },
        e => events.push(e),
      );
      // Let the loop reach its first backoff sleep, then run it out.
      await vi.advanceTimersByTimeAsync(2000);
      await p;

      const types = events.map(e => e.type);
      const text = events
        .filter(e => e.type === 'token')
        .map(e => (e.data as { content: string }).content)
        .join('');

      expect(types).toContain('reconnecting');       // the loop actually ran
      expect(types).toContain('done');               // and reached a terminal state
      expect(text).toBe('before after');             // no gap, no duplication
      expect(readActiveStream()).toBeNull();         // pointer cleared on done
    } finally {
      vi.useRealTimers();
    }
  });

  it('gives up with an error banner once the backoff schedule is exhausted', async () => {
    vi.useFakeTimers();
    try {
      server.use(
        http.post('/api/chat/completions', () => sse([
          { id: 's2-1', event: 'conversation', data: { id: 'c2', title: 'T', is_new: true, stream_id: 's2' } },
          { id: 's2-2', event: 'token', data: { content: 'partial' } },
        ])),
        // Every resume also dies without `done`: a genuinely unreachable server.
        http.get('/api/chat/completions/resume', () => sse([
          { id: 's2-3', event: 'token', data: { content: '' } },
        ])),
      );

      const events: SSEEvent[] = [];
      const p = streamChat(
        { persona: 'chat', messages: [], rag: { enabled: false }, stream: true },
        e => events.push(e),
      );
      // Full schedule is 1+2+4+8+16+30 = 61s.
      await vi.advanceTimersByTimeAsync(70000);
      await p;

      const errs = events.filter(e => e.type === 'error');
      expect(events.filter(e => e.type === 'reconnecting')).toHaveLength(6);
      expect(errs).toHaveLength(1);                  // one banner, not six
      expect(readActiveStream()).toBeNull();         // and the pointer is cleared
    } finally {
      vi.useRealTimers();
    }
  });
});
