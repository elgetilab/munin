import { renderHook, act } from '@testing-library/react';
import { http } from 'msw';
import { server } from '../test/msw-server';
import {
  useChatStore,
  _resetChatStoreForTests,
  DEFAULT_CHARS_PER_SEC,
} from './chatStore';

/**
 * Token-pacer tests (Bug 2 follow-up to P0 hardening).
 *
 * Local vLLM emits 'token' SSE events at 100+ tok/s, which dumps the
 * entire assistant reply in a burst. The store paces them at
 * `DEFAULT_TOKEN_RATE_HZ` so the user sees a typewriter while the GPU
 * slot is released at full network speed. These tests verify:
 *
 * 1. The default constant is 60 (the chosen readable rate).
 * 2. With a finite rate, `done` is deferred until the pacer drains, so
 *    the assistant bubble doesn't snap to the full text mid-typewriter.
 * 3. After the pacer drains, the assistant Message carries the FULL
 *    received content (not just what was visible at done time).
 * 4. `_resetChatStoreForTests` sets rate to `Infinity` so existing
 *    synchronous tests continue to see immediate updates.
 *
 * Why fake timers: with pacing on, `sendMessage` does not resolve
 * until the pacer drains. Real timers would make the test wait the
 * full visual duration; fake timers let us advance instantly.
 */

function sseResponse(events: Array<{ event: string; data: unknown }>): Response {
  const body = events
    .map(e => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`)
    .join('');
  return new Response(body, {
    headers: { 'Content-Type': 'text/event-stream' },
  });
}

describe('chatStore token pacer', () => {
  beforeEach(() => {
    _resetChatStoreForTests();
  });

  it('exposes DEFAULT_CHARS_PER_SEC = 260', () => {
    expect(DEFAULT_CHARS_PER_SEC).toBe(260);
  });

  it('reset helper disables the pacer (rate = Infinity) so sync tests work', () => {
    expect(useChatStore.getState().charsPerSec).toBe(Infinity);
  });

  it('with rate = Infinity, all tokens render synchronously before done', async () => {
    server.use(
      http.post('/api/chat/completions', () =>
        sseResponse([
          { event: 'token', data: { content: 'Hel' } },
          { event: 'token', data: { content: 'lo ' } },
          { event: 'token', data: { content: 'world' } },
          { event: 'done', data: { finish_reason: 'stop' } },
        ])
      )
    );

    const { result } = renderHook(() => useChatStore());

    await act(async () => {
      await result.current.sendMessage('Hi', 'chat');
    });

    const assistant = result.current.messages.find(m => m.role === 'assistant');
    expect(assistant?.content).toBe('Hello world');
    expect(result.current.streaming.phase).toBe('idle');
  });

  it(
    'with a finite rate, the assistant bubble is deferred until the pacer drains; final content is full',
    async () => {
      vi.useFakeTimers();
      try {
        server.use(
          http.post('/api/chat/completions', () =>
            sseResponse([
              { event: 'token', data: { content: 'A' } },
              { event: 'token', data: { content: 'B' } },
              { event: 'token', data: { content: 'C' } },
              { event: 'token', data: { content: 'D' } },
              { event: 'done', data: { finish_reason: 'stop' } },
            ])
          )
        );

        // Enable pacing at a deterministic 60 cps (= exactly 1 char per
        // 60 Hz tick), so the per-tick assertions below are exact.
        useChatStore.setState({ charsPerSec: 60 });

        const { result } = renderHook(() => useChatStore());

        // Don't await — sendMessage hangs until the pacer drains. We
        // need to advance fake timers to drain it.
        let sendPromise: Promise<void> | null = null;
        act(() => {
          sendPromise = result.current.sendMessage('Hi', 'chat');
        });

        // Let the SSE consumer run microtasks so all events land in
        // handleEvent and text is buffered. The pacer's first tick
        // also gets scheduled at delay=0 here.
        await act(async () => {
          await vi.advanceTimersByTimeAsync(0);
        });

        // At this point the pacer has fired its first 0-delay tick, so
        // one character is visible (60 cps = 1 char/tick) but not all four.
        expect(
          result.current.streaming.content.length
        ).toBeLessThan(4);
        // And no assistant Message has been pushed yet — `done` is
        // stashed in `pendingDoneEvent` waiting for the queue.
        expect(
          result.current.messages.some(m => m.role === 'assistant')
        ).toBe(false);

        // Advance well past the drain duration (4 chars at 60 cps =
        // ~67ms). 500ms is comfortable.
        await act(async () => {
          await vi.advanceTimersByTimeAsync(500);
        });

        // Drain done. Now sendMessage should resolve.
        await act(async () => {
          await sendPromise;
        });

        const assistant = result.current.messages.find(
          m => m.role === 'assistant'
        );
        expect(assistant?.content).toBe('ABCD');
        expect(result.current.streaming.phase).toBe('idle');
      } finally {
        vi.useRealTimers();
      }
    },
    10000
  );

  it(
    'clarification flushes the queue so stale prose is not typewritten',
    async () => {
      vi.useFakeTimers();
      try {
        server.use(
          http.post('/api/chat/completions', () =>
            sseResponse([
              { event: 'token', data: { content: 'stale prose ' } },
              {
                event: 'clarification',
                data: {
                  tool_call_id: 'tc-1',
                  conversation_id: 'c1',
                  what_i_understood: 'understood',
                  questions: [],
                },
              },
              { event: 'done', data: { finish_reason: 'clarification' } },
            ])
          )
        );

        useChatStore.setState({ charsPerSec: 60 });
        const { result } = renderHook(() => useChatStore());

        let sendPromise: Promise<void> | null = null;
        act(() => {
          sendPromise = result.current.sendMessage('Hi', 'chat');
        });

        // Drain microtasks + any pending pacer ticks. Clarification
        // should have cleared the queue and reset displayedContent.
        await act(async () => {
          await vi.advanceTimersByTimeAsync(500);
        });
        await act(async () => {
          await sendPromise;
        });

        const assistant = result.current.messages.find(
          m => m.role === 'assistant'
        );
        // contentText is wiped on clarification, so the persisted
        // bubble carries no stale prose.
        expect(assistant?.content).toBe('');
      } finally {
        vi.useRealTimers();
      }
    },
    10000
  );
});
