import { act } from '@testing-library/react';
import { http } from 'msw';
import { server } from '../test/msw-server';
import { useChatStore, _resetChatStoreForTests } from './chatStore';
import type { Message } from '../lib/types';

// Retry and Edit: sendMessage(..., replaceFrom) drops the replaced message and
// everything after it, sends replace_from, and `done` swaps the stand-in ids
// for the saved ones so the next Retry or Edit can name them.

function sseResponse(events: Array<{ event: string; data: unknown }>): Response {
  const body = events.map(e => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`).join('');
  return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } });
}

const msg = (id: string, role: Message['role'], content: string): Message => ({ id, role, content, created_at: '' });

describe('chatStore resend', () => {
  let sent: Record<string, unknown> | null = null;

  beforeEach(() => {
    _resetChatStoreForTests();
    sent = null;
    server.use(
      http.post('/api/chat/completions', async ({ request }) => {
        sent = await request.json() as Record<string, unknown>;
        return sseResponse([
          { event: 'token', data: { content: 'new answer' } },
          { event: 'done', data: { finish_reason: 'stop', message_ids: { user: 'saved-u', assistant: 'saved-a' } } },
        ]);
      }),
    );
  });

  it('takes the replaced message and everything after it away, and tells the server', async () => {
    useChatStore.setState({
      conversationId: 'c1',
      messages: [msg('u0', 'user', 'first'), msg('a0', 'assistant', 'one'), msg('u1', 'user', 'second'), msg('a1', 'assistant', 'two')],
    });
    await act(async () => { await useChatStore.getState().sendMessage('second, edited', 'munin', false, undefined, undefined, undefined, undefined, 'u1'); });

    expect(sent?.replace_from).toBe('u1');
    expect(sent?.conversation_id).toBe('c1');
    const m = useChatStore.getState().messages;
    expect(m.map(x => [x.role, x.content])).toEqual([['user', 'first'], ['assistant', 'one'], ['user', 'second, edited'], ['assistant', 'new answer']]);
  });

  it('gives the sent message and the reply the ids the server saved them under', async () => {
    useChatStore.setState({ conversationId: 'c1', messages: [] });
    await act(async () => { await useChatStore.getState().sendMessage('hello', 'munin'); });

    expect(sent?.replace_from).toBeUndefined();
    expect(useChatStore.getState().messages.map(x => x.id)).toEqual(['saved-u', 'saved-a']);
  });

  it('keeps stand-in ids when the server sends none (ephemeral chats, older servers)', async () => {
    server.use(http.post('/api/chat/completions', () => sseResponse([
      { event: 'token', data: { content: 'x' } },
      { event: 'done', data: { finish_reason: 'stop' } },
    ])));
    useChatStore.setState({ conversationId: 'c1', messages: [] });
    await act(async () => { await useChatStore.getState().sendMessage('hello', 'munin'); });

    const [u, a] = useChatStore.getState().messages;
    expect(u.id.startsWith('temp-')).toBe(true);
    expect(a.id.startsWith('msg-')).toBe(true);
  });
});
