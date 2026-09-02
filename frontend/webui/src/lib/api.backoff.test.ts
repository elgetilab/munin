import { http, HttpResponse } from 'msw';
import { server } from '../test/msw-server';
import { streamChat, readActiveStream, clearActiveStream } from './api';
import type { SSEEvent } from './types';

/**
 * Exhausting streamChat's reconnect backoff schedule.
 *
 * SCOPE, and what already covers the rest. KNOWN-BUGS #2 left "the browser
 * half" open, with the stated check being "drop WiFi a few seconds". Auditing
 * what a dropped connection already exercises, most of it was covered:
 *
 *   - reassembly after a drop      useChat.error.test.ts, "drop after
 *                                  conversation event reconnects and stitches
 *                                  tokens into one bubble"
 *   - the Last-Event-ID checkpoint api.reconnect.test.ts (verified: it is the
 *                                  suite that fails if the header is removed;
 *                                  the hook and store suites do NOT catch that)
 *   - reopen / 410 -> reload       chatStore.background.test.ts
 *
 * What NOTHING covered is the END of the schedule. Every other test advances
 * fake timers by 1100ms, one backoff step. The schedule is
 * [1s, 2s, 4s, 8s, 16s, 30s] = 61s, so the give-up path had never run, and it
 * is the one a user in a tunnel actually hits.
 *
 * A blip needs no network: it is a fetch body that ends with no `done` event,
 * which MSW produces exactly. That matters here because the manual
 * alternative, on a single-node cluster, is dropping the network every user
 * shares, which is why this went unchecked for three months.
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

describe('streamChat — mid-stream 410', () => {
  it('reloads the transcript via stream_gone instead of raising a banner', async () => {
    vi.useFakeTimers();
    try {
      server.use(
        http.post('/api/chat/completions', () => sse([
          { id: 's3-1', event: 'conversation', data: { id: 'c3', title: 'T', is_new: true, stream_id: 's3' } },
          { id: 's3-2', event: 'token', data: { content: 'partial' } },
        ])),
        // 410 mid-stream. Production 2026-09-02: this arrived ~59s BEFORE the
        // stream was promoted to background, so the turn was alive and its
        // answer landed in chat_store anyway. A banner claiming the work is
        // gone is then simply false; the transcript has it.
        http.get('/api/chat/completions/resume', () => new HttpResponse(null, { status: 410 })),
      );

      const events: SSEEvent[] = [];
      const p = streamChat(
        { persona: 'chat', messages: [], rag: { enabled: false }, stream: true },
        e => events.push(e),
      );
      await vi.advanceTimersByTimeAsync(2000);
      await p;

      expect(events.filter(e => e.type === 'stream_gone')).toHaveLength(1);
      expect(events.filter(e => e.type === 'error')).toHaveLength(0);
      expect(readActiveStream()).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('streamChat — backoff schedule exhaustion', () => {
  it('gives up after the full schedule with exactly one banner, not one per attempt', async () => {
    vi.useFakeTimers();
    try {
      server.use(
        http.post('/api/chat/completions', () => sse([
          { id: 's2-1', event: 'conversation', data: { id: 'c2', title: 'T', is_new: true, stream_id: 's2' } },
          { id: 's2-2', event: 'token', data: { content: 'partial' } },
          // no `done`: the connection died mid-answer
        ])),
        // Every resume also ends without `done`, i.e. a server that never
        // recovers, so the loop runs to the end of its schedule.
        http.get('/api/chat/completions/resume', () => sse([
          { id: 's2-3', event: 'token', data: { content: '' } },
        ])),
      );

      const events: SSEEvent[] = [];
      const p = streamChat(
        { persona: 'chat', messages: [], rag: { enabled: false }, stream: true },
        e => events.push(e),
      );
      // 1+2+4+8+16+30 = 61s; 70s clears it with margin.
      await vi.advanceTimersByTimeAsync(70000);
      await p;

      // One indicator per attempt is right; one BANNER per attempt would
      // bury the user in six identical errors for a single outage.
      expect(events.filter(e => e.type === 'reconnecting')).toHaveLength(6);
      expect(events.filter(e => e.type === 'error')).toHaveLength(1);
      // The pointer must not survive a give-up, or the next mount would
      // try to resume a stream that is never coming back.
      expect(readActiveStream()).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});
