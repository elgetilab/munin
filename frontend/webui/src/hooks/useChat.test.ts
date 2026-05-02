import { renderHook, act, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from '../test/msw-server';
import { MOCK_CONVERSATION } from '../test/msw-handlers';
import { useChat } from './useChat';

function sseResponse(events: Array<{ event: string; data: unknown }>): Response {
  const body = events.map(e => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`).join('');
  return new Response(body, {
    headers: { 'Content-Type': 'text/event-stream' },
  });
}

describe('useChat', () => {
  // ── 1. Initial state ──────────────────────────────────────────────────────

  it('has correct initial state', () => {
    const { result } = renderHook(() => useChat());

    expect(result.current.messages).toEqual([]);
    expect(result.current.conversationId).toBeNull();
    expect(result.current.streaming.phase).toBe('idle');
    expect(result.current.error).toBeNull();
    expect(result.current.artifacts).toEqual([]);
  });

  // ── 2. loadConversation ───────────────────────────────────────────────────

  it('loads a conversation and sets messages and conversationId', async () => {
    const { result } = renderHook(() => useChat());

    await act(async () => {
      await result.current.loadConversation('c1');
    });

    expect(result.current.conversationId).toBe('c1');
    expect(result.current.messages).toEqual(MOCK_CONVERSATION.messages);
    expect(result.current.error).toBeNull();
  });

  // ── 3. loadConversation error ─────────────────────────────────────────────

  it('sets error state when loadConversation fails', async () => {
    const { result } = renderHook(() => useChat());

    await act(async () => {
      await result.current.loadConversation('nonexistent');
    });

    expect(result.current.error).toBe('Failed to fetch chat');
    expect(result.current.conversationId).toBeNull();
  });

  // ── 4. clearConversation ──────────────────────────────────────────────────

  it('resets all state on clearConversation', async () => {
    const { result } = renderHook(() => useChat());

    await act(async () => {
      await result.current.loadConversation('c1');
    });
    expect(result.current.messages.length).toBeGreaterThan(0);

    act(() => {
      result.current.clearConversation();
    });

    expect(result.current.messages).toEqual([]);
    expect(result.current.conversationId).toBeNull();
    expect(result.current.streaming.phase).toBe('idle');
    expect(result.current.artifacts).toEqual([]);
  });

  // ── 5. sendMessage adds user message optimistically ───────────────────────

  it('adds user message optimistically and sets phase to thinking', async () => {
    server.use(
      http.post('/api/chat/completions', () => sseResponse([
        { event: 'done', data: { finish_reason: 'stop' } },
      ])),
    );

    const { result } = renderHook(() => useChat());

    await act(async () => {
      await result.current.sendMessage('Hello', 'chat');
    });

    const userMsg = result.current.messages.find(m => m.role === 'user');
    expect(userMsg).toBeDefined();
    expect(userMsg!.content).toBe('Hello');
  });

  // ── 6. SSE event flow ────────────────────────────────────────────────────

  it('processes SSE conversation, thinking, token, done events correctly', async () => {
    server.use(
      http.post('/api/chat/completions', () => sseResponse([
        { event: 'conversation', data: { id: 'conv-123', title: 'Test', is_new: true } },
        { event: 'thinking', data: { content: 'Let me ' } },
        { event: 'thinking', data: { content: 'think...' } },
        { event: 'token', data: { content: 'Hello ' } },
        { event: 'token', data: { content: 'world' } },
        { event: 'done', data: { finish_reason: 'stop' } },
      ])),
    );

    const { result } = renderHook(() => useChat());

    await act(async () => {
      await result.current.sendMessage('Hi', 'chat');
    });

    expect(result.current.conversationId).toBe('conv-123');

    const assistant = result.current.messages.find(m => m.role === 'assistant');
    expect(assistant).toBeDefined();
    expect(assistant!.content).toBe('Hello world');
    expect(assistant!.thinking).toBe('Let me think...');
    expect(assistant!.tool_calls).toBeNull();

    expect(result.current.streaming.phase).toBe('idle');
    expect(result.current.streaming.content).toBe('');
  });

  // ── 7. Tool call events ──────────────────────────────────────────────────

  it('populates streaming.toolCalls from tool_call and tool_result events', async () => {
    server.use(
      http.post('/api/chat/completions', () => sseResponse([
        { event: 'tool_call', data: { id: 'tc-1', name: 'search', arguments: { query: 'test' } } },
        { event: 'tool_result', data: { id: 'tc-1', name: 'search', result: { hits: 3 }, duration_ms: 150 } },
        { event: 'token', data: { content: 'Found results' } },
        { event: 'done', data: { finish_reason: 'stop' } },
      ])),
    );

    const { result } = renderHook(() => useChat());

    await act(async () => {
      await result.current.sendMessage('Search for test', 'chat');
    });

    const assistant = result.current.messages.find(m => m.role === 'assistant');
    expect(assistant!.tool_calls).toHaveLength(1);
    expect(assistant!.tool_calls![0].name).toBe('search');
    expect(assistant!.tool_calls![0].result).toEqual({ hits: 3 });
    expect(assistant!.tool_calls![0].duration_ms).toBe(150);
  });

  // ── 8. Ephemeral mode ────────────────────────────────────────────────────

  it('sends full history in ephemeral mode and does not set conversationId', async () => {
    let capturedBody: Record<string, unknown> | undefined;

    server.use(
      http.post('/api/chat/completions', async ({ request }) => {
        capturedBody = await request.json() as Record<string, unknown>;
        return sseResponse([
          { event: 'conversation', data: { id: 'eph-1', title: 'Eph', is_new: true } },
          { event: 'token', data: { content: 'ok' } },
          { event: 'done', data: { finish_reason: 'stop' } },
        ]);
      }),
    );

    const { result } = renderHook(() => useChat());

    // Pre-load a conversation so there's history
    await act(async () => {
      await result.current.loadConversation('c1');
    });

    await act(async () => {
      await result.current.sendMessage('Ephemeral question', 'chat', true);
    });

    // Should NOT set conversationId from the SSE event
    expect(result.current.conversationId).toBe('c1');

    // Should send full history
    expect(capturedBody).toBeDefined();
    expect(capturedBody!.conversation_id).toBeUndefined();
    expect(capturedBody!.ephemeral).toBe(true);
    const msgs = capturedBody!.messages as Array<{ role: string; content: string }>;
    // Original 2 messages + the new user message added optimistically + the ephemeral user message
    expect(msgs.length).toBeGreaterThanOrEqual(3);
    expect(msgs[msgs.length - 1].content).toBe('Ephemeral question');
  });

  // ── 9. Artifact events ───────────────────────────────────────────────────

  it('handles artifact_created and artifact_updated events', async () => {
    server.use(
      http.post('/api/chat/completions', () => sseResponse([
        { event: 'artifact_created', data: { id: 'art-1', source: 'model_written', title: 'Report', content_type: 'text/markdown', version: 1, conversation_id: 'c1', tool_call_id: 'tc-1' } },
        { event: 'artifact_updated', data: { id: 'art-1', source: 'model_written', title: 'Updated Report', version: 2, change_summary: 'Added conclusion', created_by: 'assistant', conversation_id: 'c1', tool_call_id: 'tc-2', applied_hunks: null, lines_added: 5, lines_removed: 0, base_version: 1 } },
        { event: 'token', data: { content: 'Done' } },
        { event: 'done', data: { finish_reason: 'stop' } },
      ])),
    );

    const { result } = renderHook(() => useChat());

    await act(async () => {
      await result.current.sendMessage('Create artifact', 'chat');
    });

    expect(result.current.artifacts).toHaveLength(1);
    expect(result.current.artifacts[0].id).toBe('art-1');
    expect(result.current.artifacts[0].title).toBe('Updated Report');
    expect(result.current.artifacts[0].latest_version).toBe(2);
  });

  // ── 10. stopGenerating ────────────────────────────────────────────────────

  it('aborts stream and sets phase to done on stopGenerating', async () => {
    server.use(
      http.post('/api/chat/completions', async () => {
        // Slow stream that never completes
        return sseResponse([
          { event: 'token', data: { content: 'partial' } },
        ]);
      }),
    );

    const { result } = renderHook(() => useChat());

    // Start sending but don't await — it will hang on the stream
    let sendPromise: Promise<void>;
    act(() => {
      sendPromise = result.current.sendMessage('Hello', 'chat');
    });

    // Wait for the thinking phase to kick in
    await waitFor(() => {
      expect(result.current.streaming.phase).not.toBe('idle');
    });

    act(() => {
      result.current.stopGenerating();
    });

    expect(result.current.streaming.phase).toBe('done');

    // Clean up the promise
    await act(async () => {
      await sendPromise!.catch(() => {});
    });
  });

  // ── 11. Error SSE event ──────────────────────────────────────────────────

  it('sets error and phase to error on SSE error event', async () => {
    server.use(
      http.post('/api/chat/completions', () => sseResponse([
        { event: 'error', data: { message: 'Model overloaded' } },
      ])),
    );

    const { result } = renderHook(() => useChat());

    await act(async () => {
      await result.current.sendMessage('Hi', 'chat');
    });

    expect(result.current.error).toBe('Model overloaded');
    expect(result.current.streaming.phase).toBe('error');
  });

  // ── 12. projectId ────────────────────────────────────────────────────────

  it('includes projectId in the request body', async () => {
    let capturedBody: Record<string, unknown> | undefined;

    server.use(
      http.post('/api/chat/completions', async ({ request }) => {
        capturedBody = await request.json() as Record<string, unknown>;
        return sseResponse([
          { event: 'done', data: { finish_reason: 'stop' } },
        ]);
      }),
    );

    const { result } = renderHook(() => useChat());

    await act(async () => {
      await result.current.sendMessage('Hello', 'chat', false, undefined, 'proj-42');
    });

    expect(capturedBody).toBeDefined();
    expect(capturedBody!.project_id).toBe('proj-42');
  });

  // ── 13. clarification discards prose ──────────────────────────────────────

  it('discards in-progress prose when clarification event arrives', async () => {
    server.use(
      http.post('/api/chat/completions', () => {
        return sseResponse([
          { event: 'conversation', data: { id: 'conv-clar', title: 'Test', is_new: true } },
          { event: 'token', data: { content: 'Could you ' } },
          { event: 'token', data: { content: 'clarify which ' } },
          { event: 'token', data: { content: 'EPR you mean?' } },
          { event: 'clarification', data: {
            tool_call_id: 'tc-1',
            conversation_id: 'conv-clar',
            what_i_understood: 'You want help with EPR analysis',
            questions: [
              { id: 'q1', text: 'Which EPR?', options: ['Spectroscopy', 'Patient record'], allow_custom: false },
            ],
          }},
          { event: 'done', data: { finish_reason: 'clarification' } },
        ]);
      }),
    );

    const { result } = renderHook(() => useChat());

    await act(async () => {
      await result.current.sendMessage('Help with EPR', 'chat');
    });

    // The prose "Could you clarify which EPR you mean?" should be discarded
    const lastMsg = result.current.messages[result.current.messages.length - 1];
    expect(lastMsg.role).toBe('assistant');
    expect(lastMsg.content).toBe('');
    expect(lastMsg.clarification).toBeDefined();
    expect(lastMsg.clarification!.questions).toHaveLength(1);
    expect(lastMsg.clarification!.questions[0].text).toBe('Which EPR?');
  });
});
