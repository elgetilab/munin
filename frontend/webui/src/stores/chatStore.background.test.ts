import { waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from '../test/msw-server';
import {
  useChatStore,
  _resetChatStoreForTests,
  INITIAL_STREAMING,
} from './chatStore';

/**
 * Background-turns store tests.
 *
 * A turn now survives a closed tab: the backend promotes the stream to
 * background, runs it to completion, and persists the answer. These
 * tests cover the frontend half of that contract:
 *
 * 1. loadConversation re-attaches (resume from seq 0) when the server
 *    reports a live `active_stream`, and the replayed events rebuild
 *    the assistant bubble.
 * 2. A completed (`done: true`) or already-attempted stream is NOT
 *    re-attached.
 * 3. A 410 on re-attach (stream_gone) falls back to reloading the
 *    conversation — the persisted answer renders, no error banner.
 * 4. stopGenerating POSTs the explicit cancel endpoint with the
 *    latched stream id (closing the connection alone no longer stops
 *    the turn server-side).
 *
 * NOTE: stream ids are unique per test — the module-scoped
 * _attemptedResumes guard set in chatStore survives across tests.
 */

function sseResponse(events: Array<{ event: string; data: unknown }>): Response {
  const body = events
    .map(e => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`)
    .join('');
  return new Response(body, {
    headers: { 'Content-Type': 'text/event-stream' },
  });
}

function conversationPayload(
  id: string,
  active: { stream_id: string; done: boolean; last_seq: number } | null,
  messages: unknown[] = [
    { id: 'm1', role: 'user', content: 'question?', created_at: 'now' },
  ],
) {
  return {
    id,
    title: 'T',
    persona: 'munin',
    created_at: 'now',
    updated_at: 'now',
    summary: null,
    messages,
    plan: null,
    active_stream: active,
  };
}

beforeEach(() => {
  _resetChatStoreForTests();
  try { localStorage.clear(); } catch { /* ignore */ }
});

describe('loadConversation auto-re-attach', () => {
  it('re-attaches to a live stream and rebuilds the bubble from a full replay', async () => {
    let resumeHeader: string | null = 'unset';
    server.use(
      http.get('/api/chats/conv-bg1', () =>
        HttpResponse.json(conversationPayload('conv-bg1', {
          stream_id: 'sid-bg1', done: false, last_seq: 3,
        })),
      ),
      http.get('/api/chat/completions/resume', ({ request }) => {
        resumeHeader = request.headers.get('Last-Event-ID');
        const url = new URL(request.url);
        expect(url.searchParams.get('stream_id')).toBe('sid-bg1');
        return sseResponse([
          { event: 'conversation', data: { id: 'conv-bg1', title: 'T', is_new: false, stream_id: 'sid-bg1' } },
          { event: 'token', data: { content: 'Hello from ' } },
          { event: 'token', data: { content: 'background' } },
          { event: 'done', data: { finish_reason: 'stop' } },
        ]);
      }),
    );

    await useChatStore.getState().loadConversation('conv-bg1');

    await waitFor(() => {
      const msgs = useChatStore.getState().messages;
      expect(msgs[msgs.length - 1]).toMatchObject({
        role: 'assistant',
        content: 'Hello from background',
      });
    });
    // Replay must start from seq 0 — no Last-Event-ID header.
    expect(resumeHeader).toBeNull();
    expect(useChatStore.getState().streaming.phase).toBe('idle');
  });

  it('does not re-attach when the stream is already done', async () => {
    let resumeCalls = 0;
    server.use(
      http.get('/api/chats/conv-bg2', () =>
        HttpResponse.json(conversationPayload('conv-bg2', {
          stream_id: 'sid-bg2', done: true, last_seq: 9,
        })),
      ),
      http.get('/api/chat/completions/resume', () => {
        resumeCalls += 1;
        return sseResponse([{ event: 'done', data: { finish_reason: 'stop' } }]);
      }),
    );

    await useChatStore.getState().loadConversation('conv-bg2');
    // Give any (incorrect) fire-and-forget resume a tick to land.
    await new Promise(r => setTimeout(r, 10));
    expect(resumeCalls).toBe(0);
  });

  it('re-attaches an already-attempted stream only once', async () => {
    let resumeCalls = 0;
    server.use(
      http.get('/api/chats/conv-bg3', () =>
        HttpResponse.json(conversationPayload('conv-bg3', {
          stream_id: 'sid-bg3', done: false, last_seq: 1,
        })),
      ),
      http.get('/api/chat/completions/resume', () => {
        resumeCalls += 1;
        return sseResponse([
          { event: 'conversation', data: { id: 'conv-bg3', title: 'T', is_new: false, stream_id: 'sid-bg3' } },
          { event: 'done', data: { finish_reason: 'stop' } },
        ]);
      }),
    );

    await useChatStore.getState().loadConversation('conv-bg3');
    await waitFor(() => expect(resumeCalls).toBe(1));
    await useChatStore.getState().loadConversation('conv-bg3');
    await new Promise(r => setTimeout(r, 10));
    expect(resumeCalls).toBe(1);
  });

  it('falls back to a transcript reload when the re-attach gets 410', async () => {
    let chatFetches = 0;
    server.use(
      http.get('/api/chats/conv-bg4', () => {
        chatFetches += 1;
        // First load: server still reports the stream (it truncated /
        // evicted between this response and the resume attempt).
        // Reload after stream_gone: the persisted answer is in the
        // transcript and the stream is gone from the registry.
        if (chatFetches === 1) {
          return HttpResponse.json(conversationPayload('conv-bg4', {
            stream_id: 'sid-bg4', done: false, last_seq: 500,
          }));
        }
        return HttpResponse.json(conversationPayload('conv-bg4', null, [
          { id: 'm1', role: 'user', content: 'question?', created_at: 'now' },
          { id: 'm2', role: 'assistant', content: 'persisted answer', created_at: 'now' },
        ]));
      }),
      http.get('/api/chat/completions/resume', () =>
        HttpResponse.json({ error: { message: 'gone' } }, { status: 410 }),
      ),
    );

    await useChatStore.getState().loadConversation('conv-bg4');

    await waitFor(() => {
      const msgs = useChatStore.getState().messages;
      expect(msgs[msgs.length - 1]).toMatchObject({
        role: 'assistant',
        content: 'persisted answer',
      });
    });
    expect(chatFetches).toBe(2);
    // No error banner — stream_gone is a reload signal, not a failure.
    expect(useChatStore.getState().error).toBeNull();
    expect(useChatStore.getState().streaming.phase).toBe('idle');
  });
});

describe('stopGenerating', () => {
  it('POSTs the explicit cancel endpoint with the latched stream id', async () => {
    let cancelled: string | null = null;
    server.use(
      http.post('/api/chat/completions/:sid/cancel', ({ params }) => {
        cancelled = params.sid as string;
        return new HttpResponse(null, { status: 204 });
      }),
    );

    useChatStore.setState({
      streaming: { ...INITIAL_STREAMING, streamId: 'sid-stop1', phase: 'generating' },
    });
    useChatStore.getState().stopGenerating();

    await waitFor(() => expect(cancelled).toBe('sid-stop1'));
    expect(useChatStore.getState().streaming.phase).toBe('done');
  });

  it('falls back to the localStorage pointer when no stream id is latched', async () => {
    let cancelled: string | null = null;
    server.use(
      http.post('/api/chat/completions/:sid/cancel', ({ params }) => {
        cancelled = params.sid as string;
        return new HttpResponse(null, { status: 204 });
      }),
    );

    localStorage.setItem(
      'munin.active_stream',
      JSON.stringify({ stream_id: 'sid-stop2', conversation_id: 'conv-x' }),
    );
    useChatStore.getState().stopGenerating();

    await waitFor(() => expect(cancelled).toBe('sid-stop2'));
  });
});
