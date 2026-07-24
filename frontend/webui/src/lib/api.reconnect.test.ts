import { http, HttpResponse } from 'msw';
import { server } from '../test/msw-server';
import {
  streamChat,
  resumeChat,
  readActiveStream,
  clearActiveStream,
} from './api';
import type { SSEEvent } from './types';

/**
 * Tests for the SSE reconnect plumbing (P1 #10 + background turns).
 *
 * Covers: id: line parsing → lastEventId tracking; stream_id capture
 * from the conversation event → localStorage write (moved from
 * sessionStorage so the pointer survives a closed tab); localStorage
 * lifecycle (write on stream_id, update on id:, clear on done/error/
 * gone); ephemeral chats skip persistence; resumeChat sends
 * Last-Event-ID; 410 emits the synthetic stream_gone event (reload
 * signal, not an error banner) and clears the pointer.
 *
 * The exponential-backoff reconnect loop itself isn't unit-tested here
 * (it would need fake timers + multi-fixture sequencing); the wiring
 * pieces it depends on are.
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
  // Tests share a process-wide localStorage; reset between cases.
  try { localStorage.clear(); } catch { /* ignore */ }
});

describe('streamChat — id parsing & localStorage', () => {
  it('parses id: lines and persists stream_id + last_event_id on conversation event', async () => {
    server.use(
      http.post('/api/chat/completions', () => sseResponse([
        { id: 'stream-abc-1', event: 'conversation', data: { id: 'conv-1', title: 'T', is_new: true, stream_id: 'stream-abc' } },
        { id: 'stream-abc-2', event: 'token', data: { content: 'hi' } },
        { id: 'stream-abc-3', event: 'done', data: { finish_reason: 'stop' } },
      ])),
    );

    const events: SSEEvent[] = [];
    await streamChat({ persona: 'chat', messages: [], rag: { enabled: false }, stream: true }, e => events.push(e));

    // localStorage is cleared on done — so by the end it's gone, but
    // the conversation event having stream_id was enough to write it.
    // Verify the lifecycle by also inspecting events.
    expect(events.map(e => e.type)).toEqual(['conversation', 'token', 'done']);
    // done clears localStorage.
    expect(readActiveStream()).toBeNull();
  });

  it('writes localStorage between the conversation event and done', async () => {
    // Capture the persisted state mid-stream (on the `token` event).
    // The fixture ends with `done` so streamChat doesn't enter the
    // reconnect loop; the persistence is observed before `done` clears it.
    server.use(
      http.post('/api/chat/completions', () => sseResponse([
        { id: 'sid1-1', event: 'conversation', data: { id: 'conv-1', title: 'T', is_new: true, stream_id: 'sid1' } },
        { id: 'sid1-2', event: 'token', data: { content: 'hi' } },
        { id: 'sid1-3', event: 'done', data: { finish_reason: 'stop' } },
      ])),
    );

    let snapshotAtToken: ReturnType<typeof readActiveStream> = null;
    await streamChat(
      { persona: 'chat', messages: [], rag: { enabled: false }, stream: true },
      e => { if (e.type === 'token') snapshotAtToken = readActiveStream(); },
    );

    expect(snapshotAtToken).toEqual({
      stream_id: 'sid1',
      conversation_id: 'conv-1',
      last_event_id: 'sid1-2',
    });
    // And after `done`, it should be cleared.
    expect(readActiveStream()).toBeNull();
  });

  it('does NOT persist the pointer for ephemeral chats', async () => {
    server.use(
      http.post('/api/chat/completions', () => sseResponse([
        { id: 'eph-1', event: 'conversation', data: { id: 'ephemeral-abc', title: '', is_new: true, ephemeral: true, stream_id: 'eph' } },
        { id: 'eph-2', event: 'token', data: { content: 'hi' } },
        { id: 'eph-3', event: 'done', data: { finish_reason: 'stop' } },
      ])),
    );

    let snapshotAtToken: ReturnType<typeof readActiveStream> = null;
    await streamChat(
      { persona: 'chat', messages: [], rag: { enabled: false }, stream: true, ephemeral: true },
      e => { if (e.type === 'token') snapshotAtToken = readActiveStream(); },
    );

    expect(snapshotAtToken).toBeNull();
  });

  it('clears localStorage on a server-emitted error event', async () => {
    server.use(
      http.post('/api/chat/completions', () => sseResponse([
        { id: 'sid2-1', event: 'conversation', data: { id: 'conv-x', title: '', is_new: true, stream_id: 'sid2' } },
        { id: 'sid2-2', event: 'error', data: { message: 'kaboom' } },
      ])),
    );
    await streamChat({ persona: 'chat', messages: [], rag: { enabled: false }, stream: true }, () => {});
    expect(readActiveStream()).toBeNull();
  });
});

describe('resumeChat', () => {
  it('sends the Last-Event-ID header and dispatches replayed events', async () => {
    let capturedHeader: string | null = null;
    server.use(
      http.get('/api/chat/completions/resume', ({ request }) => {
        capturedHeader = request.headers.get('Last-Event-ID');
        const url = new URL(request.url);
        expect(url.searchParams.get('stream_id')).toBe('mystream');
        return sseResponse([
          { id: 'mystream-5', event: 'token', data: { content: ' more' } },
          { id: 'mystream-6', event: 'done', data: { finish_reason: 'stop' } },
        ]);
      }),
    );

    const events: SSEEvent[] = [];
    await resumeChat('mystream', 'mystream-4', e => events.push(e));

    expect(capturedHeader).toBe('mystream-4');
    expect(events.map(e => e.type)).toEqual(['token', 'done']);
  });

  it('emits stream_gone and clears the pointer on 410 Gone', async () => {
    // Pre-populate localStorage as if mid-stream. Background turns:
    // 410 on a resume means the outcome is already persisted, so the
    // consumer gets the synthetic stream_gone (reload the transcript)
    // rather than an error banner.
    localStorage.setItem(
      'munin.active_stream',
      JSON.stringify({ stream_id: 's-evicted', conversation_id: 'c-1', last_event_id: 's-evicted-7' }),
    );

    server.use(
      http.get('/api/chat/completions/resume', () =>
        HttpResponse.json({ error: { message: 'gone' } }, { status: 410 }),
      ),
    );

    const events: SSEEvent[] = [];
    await resumeChat('s-evicted', 's-evicted-7', e => events.push(e));

    expect(readActiveStream()).toBeNull();
    expect(events.length).toBe(1);
    expect(events[0].type).toBe('stream_gone');
  });

  it('reads the persisted entry from localStorage on demand', () => {
    localStorage.setItem(
      'munin.active_stream',
      JSON.stringify({ stream_id: 'sid', conversation_id: 'conv', last_event_id: 'sid-12' }),
    );
    expect(readActiveStream()).toEqual({
      stream_id: 'sid', conversation_id: 'conv', last_event_id: 'sid-12',
    });
    clearActiveStream();
    expect(readActiveStream()).toBeNull();
  });

  it('readActiveStream rejects malformed JSON', () => {
    localStorage.setItem('munin.active_stream', '{ not valid json');
    expect(readActiveStream()).toBeNull();
  });

  it('readActiveStream rejects entries missing required fields', () => {
    localStorage.setItem('munin.active_stream', JSON.stringify({ stream_id: 'x' })); // no conversation_id
    expect(readActiveStream()).toBeNull();
  });
});
