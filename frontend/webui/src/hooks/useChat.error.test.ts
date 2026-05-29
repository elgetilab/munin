import { renderHook, act, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from '../test/msw-server';
import { useChatStore, _resetChatStoreForTests } from '../stores/chatStore';
import { clearActiveStream } from '../lib/api';

/**
 * P1 #13: hook-level error coverage for useChat.
 *
 * Pins down the resilience that exists in useChat.ts + lib/api.ts:
 * the save-always interrupted bubble, the Last-Event-ID reconnect
 * loop, the parser's silent-skip on malformed lines, and the new
 * "resume 5xx surfaces a banner" fix in streamChat / resumeChat.
 *
 * Companion to useChat.test.ts (happy path + SSE error event) and
 * api.reconnect.test.ts (api-layer reconnect mechanics).
 */

function sseBody(events: Array<{ event: string; data: unknown; id?: string }>): string {
  return events.map(e => {
    const idLine = e.id ? `id: ${e.id}\n` : '';
    return `${idLine}event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`;
  }).join('');
}

function sseResponse(events: Array<{ event: string; data: unknown; id?: string }>): Response {
  return new Response(sseBody(events), {
    headers: { 'Content-Type': 'text/event-stream' },
  });
}

beforeEach(() => {
  // P2 #26: chat store is module-scoped; reset before each test.
  _resetChatStoreForTests();
  try { sessionStorage.clear(); } catch { /* ignore */ }
  clearActiveStream();
});

// ────────────────────────────────────────────────────────────────────
// A. Initial HTTP failure (before any stream chunk)
// ────────────────────────────────────────────────────────────────────

describe('useChat — initial HTTP failure', () => {
  it('500 with JSON error envelope surfaces message via interrupted bubble', async () => {
    server.use(
      http.post('/api/chat/completions', () =>
        HttpResponse.json({ error: { message: 'vLLM is down' } }, { status: 500 }),
      ),
    );

    const { result } = renderHook(() => useChatStore());
    await act(async () => {
      await result.current.sendMessage('Hi', 'chat');
    });

    expect(result.current.error).toBe('vLLM is down');
    expect(result.current.streaming.phase).toBe('idle');
    const assistant = result.current.messages.find(m => m.role === 'assistant');
    expect(assistant).toBeDefined();
    expect(assistant!.interrupted).toBe(true);
    expect(assistant!.content).toContain('vLLM is down');
  });

  it('non-JSON 5xx body falls back to the "Request failed" sentinel', async () => {
    // res.json() throws -> the .catch() returns { error: { message: 'Request failed' } },
    // so the user-visible message is "Request failed" (not "HTTP 502").
    // This pins the current sentinel; if api.ts ever switches the
    // catch fallback to "HTTP {status}" the test must move with it.
    server.use(
      http.post('/api/chat/completions', () =>
        new HttpResponse('bad gateway', { status: 502 }),
      ),
    );

    const { result } = renderHook(() => useChatStore());
    await act(async () => {
      await result.current.sendMessage('Hi', 'chat');
    });

    expect(result.current.error).toBe('Request failed');
    const assistant = result.current.messages.find(m => m.role === 'assistant');
    expect(assistant!.interrupted).toBe(true);
    expect(assistant!.content).toContain('Request failed');
  });

  it('JSON 5xx without error.message falls back to "HTTP <status>"', async () => {
    // res.json() succeeds but the envelope has no .error.message; the
    // `|| `HTTP ${res.status}`` branch kicks in.
    server.use(
      http.post('/api/chat/completions', () =>
        HttpResponse.json({}, { status: 503 }),
      ),
    );

    const { result } = renderHook(() => useChatStore());
    await act(async () => {
      await result.current.sendMessage('Hi', 'chat');
    });

    expect(result.current.error).toBe('HTTP 503');
    const assistant = result.current.messages.find(m => m.role === 'assistant');
    expect(assistant!.interrupted).toBe(true);
    expect(assistant!.content).toContain('HTTP 503');
  });

  it('fetch rejection (DNS/network down) is caught and surfaced', async () => {
    server.use(
      http.post('/api/chat/completions', () => HttpResponse.error()),
    );

    const { result } = renderHook(() => useChatStore());
    await act(async () => {
      await result.current.sendMessage('Hi', 'chat');
    });

    expect(result.current.error).toBeTruthy();
    // The catch block in useChat synthesizes an interrupted Message.
    const assistant = result.current.messages.find(m => m.role === 'assistant');
    expect(assistant).toBeDefined();
    expect(assistant!.interrupted).toBe(true);
    expect(result.current.streaming.phase).toBe('idle');
  });
});

// ────────────────────────────────────────────────────────────────────
// B. Mid-stream drop / reconnect
// ────────────────────────────────────────────────────────────────────

describe('useChat — mid-stream drop + reconnect', () => {
  beforeEach(() => { vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] }); });
  afterEach(() => { vi.useRealTimers(); });

  it('drop without a stream_id does NOT trigger reconnect and produces no assistant bubble', async () => {
    // No `conversation` event → no stream_id captured → reconnect
    // loop skipped. This is the "frontend cannot distinguish clean
    // EOF from drop" case documented in useChat.test.ts row 12.
    server.use(
      http.post('/api/chat/completions', () =>
        sseResponse([
          { event: 'token', data: { content: 'orphan' } },
        ]),
      ),
    );

    const { result } = renderHook(() => useChatStore());
    await act(async () => {
      await result.current.sendMessage('Hi', 'chat');
    });

    // No retry was scheduled — no pending timers.
    expect(vi.getTimerCount()).toBe(0);
    expect(result.current.messages.filter(m => m.role === 'assistant').length).toBe(0);
  });

  it('drop after conversation event reconnects and stitches tokens into one bubble', async () => {
    let calls = 0;
    server.use(
      http.post('/api/chat/completions', () => {
        calls++;
        return sseResponse([
          { id: 'sx-1', event: 'conversation', data: { id: 'conv-x', title: '', is_new: true, stream_id: 'sx' } },
          { id: 'sx-2', event: 'token', data: { content: 'before ' } },
          // body ends here — reader sees EOF without `done`
        ]);
      }),
      http.get('/api/chat/completions/resume', () =>
        sseResponse([
          { id: 'sx-3', event: 'token', data: { content: 'after' } },
          { id: 'sx-4', event: 'done', data: { finish_reason: 'stop' } },
        ]),
      ),
    );

    const { result } = renderHook(() => useChatStore());
    let promise!: Promise<void>;
    act(() => { promise = result.current.sendMessage('Hi', 'chat'); });

    // Drain initial fetch + parse, then advance through the 1s backoff.
    await act(async () => { await vi.advanceTimersByTimeAsync(1100); });
    await act(async () => { await promise; });

    expect(calls).toBe(1);
    const assistant = result.current.messages.find(m => m.role === 'assistant');
    expect(assistant).toBeDefined();
    expect(assistant!.content).toBe('before after');
    // No interrupted marker — the resume completed cleanly.
    expect(assistant!.interrupted).toBeUndefined();
    expect(result.current.streaming.reconnecting).toBeNull();
  });

  it('resume returning 410 Gone surfaces a terminal error', async () => {
    server.use(
      http.post('/api/chat/completions', () =>
        sseResponse([
          { id: 'sg-1', event: 'conversation', data: { id: 'conv-g', title: '', is_new: true, stream_id: 'sg' } },
          { id: 'sg-2', event: 'token', data: { content: 'partial' } },
        ]),
      ),
      http.get('/api/chat/completions/resume', () =>
        HttpResponse.json({ error: { message: 'gone' } }, { status: 410 }),
      ),
    );

    const { result } = renderHook(() => useChatStore());
    let promise!: Promise<void>;
    act(() => { promise = result.current.sendMessage('Hi', 'chat'); });
    await act(async () => { await vi.advanceTimersByTimeAsync(1100); });
    await act(async () => { await promise; });

    expect(result.current.error).toContain('no longer available');
    const assistant = result.current.messages.find(m => m.role === 'assistant');
    expect(assistant!.interrupted).toBe(true);
    expect(assistant!.content).toContain('partial');
  });
});

// ────────────────────────────────────────────────────────────────────
// C. Malformed events
// ────────────────────────────────────────────────────────────────────

describe('useChat — malformed events', () => {
  it('invalid JSON in a data line is silently skipped; surrounding tokens still land', async () => {
    server.use(
      http.post('/api/chat/completions', () =>
        new Response(
          `event: token\ndata: ${JSON.stringify({ content: 'before ' })}\n\n` +
          `event: token\ndata: {not valid json\n\n` +
          `event: token\ndata: ${JSON.stringify({ content: 'after' })}\n\n` +
          `event: done\ndata: ${JSON.stringify({ finish_reason: 'stop' })}\n\n`,
          { headers: { 'Content-Type': 'text/event-stream' } },
        ),
      ),
    );

    const { result } = renderHook(() => useChatStore());
    await act(async () => {
      await result.current.sendMessage('Hi', 'chat');
    });

    const assistant = result.current.messages.find(m => m.role === 'assistant');
    expect(assistant).toBeDefined();
    // The malformed line is dropped; the two valid tokens concatenate.
    expect(assistant!.content).toBe('before after');
    expect(result.current.error).toBeNull();
  });

  it('orphan data line without an event: prefix does not crash the parser', async () => {
    server.use(
      http.post('/api/chat/completions', () =>
        new Response(
          // First a stray `data:` with no preceding `event:` — parser
          // requires `currentEvent` truthy to dispatch, so this is a no-op.
          `data: ${JSON.stringify({ content: 'orphan' })}\n\n` +
          `event: token\ndata: ${JSON.stringify({ content: 'real' })}\n\n` +
          `event: done\ndata: ${JSON.stringify({ finish_reason: 'stop' })}\n\n`,
          { headers: { 'Content-Type': 'text/event-stream' } },
        ),
      ),
    );

    const { result } = renderHook(() => useChatStore());
    await act(async () => {
      await result.current.sendMessage('Hi', 'chat');
    });

    const assistant = result.current.messages.find(m => m.role === 'assistant');
    expect(assistant!.content).toBe('real');
  });

  it('unknown event type is ignored without corrupting subsequent state', async () => {
    server.use(
      http.post('/api/chat/completions', () => sseResponse([
        { event: 'future_thing', data: { whatever: true } },
        { event: 'token', data: { content: 'hello' } },
        { event: 'done', data: { finish_reason: 'stop' } },
      ])),
    );

    const { result } = renderHook(() => useChatStore());
    await act(async () => {
      await result.current.sendMessage('Hi', 'chat');
    });

    const assistant = result.current.messages.find(m => m.role === 'assistant');
    expect(assistant!.content).toBe('hello');
    expect(result.current.error).toBeNull();
  });
});

// ────────────────────────────────────────────────────────────────────
// D. Extras
// ────────────────────────────────────────────────────────────────────

describe('useChat — resume 5xx (P1 #13 fix)', () => {
  beforeEach(() => { vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] }); });
  afterEach(() => { vi.useRealTimers(); });

  it('resume returning 500 surfaces "Stream resume failed" instead of silently freezing', async () => {
    // Pre-fix behavior: the outer while-loop's `outcome !== 'error'`
    // guard exited silently, leaving the user with a partial bubble
    // and no banner. The fix in streamChat emits a terminal error
    // event before returning; this test pins it.
    server.use(
      http.post('/api/chat/completions', () =>
        sseResponse([
          { id: 'sf-1', event: 'conversation', data: { id: 'conv-f', title: '', is_new: true, stream_id: 'sf' } },
          { id: 'sf-2', event: 'token', data: { content: 'partial' } },
        ]),
      ),
      http.get('/api/chat/completions/resume', () =>
        HttpResponse.json({ error: { message: 'boom' } }, { status: 500 }),
      ),
    );

    const { result } = renderHook(() => useChatStore());
    let promise!: Promise<void>;
    act(() => { promise = result.current.sendMessage('Hi', 'chat'); });
    await act(async () => { await vi.advanceTimersByTimeAsync(1100); });
    await act(async () => { await promise; });

    expect(result.current.error).toBe('Stream resume failed; please retry.');
    const assistant = result.current.messages.find(m => m.role === 'assistant');
    expect(assistant!.interrupted).toBe(true);
    expect(assistant!.content).toContain('partial');
  });
});

describe('useChat — AbortError mid-resume', () => {
  // Use real timers here. Fake timers tangle with @testing-library's
  // waitFor + React 19's microtask scheduling, and the 1s wait is
  // acceptable for a single targeted test.
  it('stopGenerating during the backoff sleep exits cleanly without spurious error', async () => {
    server.use(
      http.post('/api/chat/completions', () =>
        sseResponse([
          { id: 'sa-1', event: 'conversation', data: { id: 'conv-a', title: '', is_new: true, stream_id: 'sa' } },
          { id: 'sa-2', event: 'token', data: { content: 'mid' } },
        ]),
      ),
      // The resume GET should never be reached because we abort during sleep.
      http.get('/api/chat/completions/resume', () => {
        throw new Error('resume should not be called after abort');
      }),
    );

    const { result } = renderHook(() => useChatStore());
    let promise!: Promise<void>;
    act(() => { promise = result.current.sendMessage('Hi', 'chat'); });

    // Wait for initial fetch + parse so the reconnect loop has reached _sleep.
    await waitFor(() => {
      expect(result.current.streaming.content).toBe('mid');
    });
    // Abort while the 1000ms backoff is in flight.
    act(() => { result.current.stopGenerating(); });
    await act(async () => { await promise; });

    // AbortError is swallowed (_sleep's catch returns, streamChat
    // returns cleanly). No interrupted bubble synthesized, no banner.
    expect(result.current.error).toBeNull();
    expect(result.current.streaming.phase).toBe('done');
    // The partial assistant content was never persisted as a message
    // (no done, no error event arrived).
    expect(result.current.messages.filter(m => m.role === 'assistant').length).toBe(0);
  });
});

describe('useChat — sessionStorage on terminal SSE error', () => {
  it('clears munin.active_stream when the server emits an error event after stream_id capture', async () => {
    server.use(
      http.post('/api/chat/completions', () => sseResponse([
        { id: 'se-1', event: 'conversation', data: { id: 'conv-e', title: '', is_new: true, stream_id: 'se' } },
        { id: 'se-2', event: 'token', data: { content: 'partial ' } },
        { id: 'se-3', event: 'error', data: { message: 'kaboom' } },
      ])),
    );

    const { result } = renderHook(() => useChatStore());
    await act(async () => {
      await result.current.sendMessage('Hi', 'chat');
    });

    // Hook surface: interrupted bubble + error banner.
    expect(result.current.error).toBe('kaboom');
    const assistant = result.current.messages.find(m => m.role === 'assistant');
    expect(assistant!.interrupted).toBe(true);
    expect(assistant!.content).toContain('partial');
    // The api layer must have cleared the persisted active stream.
    expect(sessionStorage.getItem('munin.active_stream')).toBeNull();
  });
});

describe('useChat — retrying + reconnecting interleave', () => {
  beforeEach(() => { vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] }); });
  afterEach(() => { vi.useRealTimers(); });

  it('retrying indicator (vLLM backoff) and reconnecting indicator (SSE drop) flip independently', async () => {
    // Initial stream: `retrying` (vLLM transient) → tokens → EOF (drop).
    // Resume: more tokens → done. Verify the hook ends with both
    // indicators cleared and the bubble fully stitched.
    server.use(
      http.post('/api/chat/completions', () =>
        sseResponse([
          { id: 'sr-1', event: 'conversation', data: { id: 'conv-r', title: '', is_new: true, stream_id: 'sr' } },
          { id: 'sr-2', event: 'retrying', data: { attempt: 1, max_attempts: 5, delay_s: 0.5, reason: 'vllm 503' } },
          { id: 'sr-3', event: 'token', data: { content: 'A' } },
        ]),
      ),
      http.get('/api/chat/completions/resume', () =>
        sseResponse([
          { id: 'sr-4', event: 'token', data: { content: 'B' } },
          { id: 'sr-5', event: 'done', data: { finish_reason: 'stop' } },
        ]),
      ),
    );

    const { result } = renderHook(() => useChatStore());
    let promise!: Promise<void>;
    act(() => { promise = result.current.sendMessage('Hi', 'chat'); });
    await act(async () => { await vi.advanceTimersByTimeAsync(1100); });
    await act(async () => { await promise; });

    const assistant = result.current.messages.find(m => m.role === 'assistant');
    expect(assistant!.content).toBe('AB');
    // Both indicators must be null at end-of-stream.
    expect(result.current.streaming.retrying).toBeNull();
    expect(result.current.streaming.reconnecting).toBeNull();
    expect(result.current.streaming.phase).toBe('idle');
  });
});

describe('useChat — error reset on next sendMessage', () => {
  it('clearing happens at the start of the next send so a successful retry hides the banner', async () => {
    // First send: server 500.
    server.use(
      http.post('/api/chat/completions', () =>
        HttpResponse.json({ error: { message: 'first attempt failed' } }, { status: 500 }),
      ),
    );

    const { result } = renderHook(() => useChatStore());
    await act(async () => {
      await result.current.sendMessage('Hi', 'chat');
    });
    expect(result.current.error).toBe('first attempt failed');

    // Second send: server returns clean done. Error must clear.
    server.use(
      http.post('/api/chat/completions', () => sseResponse([
        { event: 'token', data: { content: 'recovered' } },
        { event: 'done', data: { finish_reason: 'stop' } },
      ])),
    );

    await act(async () => {
      await result.current.sendMessage('Try again', 'chat');
    });

    expect(result.current.error).toBeNull();
    // Both interrupted bubble from first attempt AND the recovered
    // bubble from the second are present; the user can see the history.
    const assistants = result.current.messages.filter(m => m.role === 'assistant');
    expect(assistants.length).toBe(2);
    expect(assistants[0].interrupted).toBe(true);
    expect(assistants[1].content).toBe('recovered');
  });
});
