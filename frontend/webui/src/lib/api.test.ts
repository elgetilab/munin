import { http, HttpResponse } from 'msw';
import { server } from '../test/msw-server';
import {
  MOCK_PERSONAS,
  MOCK_CHATS,
  MOCK_CONVERSATION,
  MOCK_PROJECTS,
  MOCK_ARTIFACTS,
  MOCK_ARTIFACT_FULL,
} from '../test/msw-handlers';
import {
  fetchPersonas,
  fetchChats,
  fetchChat,
  deleteChat,
  renameChat,
  pinChat,
  unpinChat,
  fetchProjects,
  createProject,
  fileConversation,
  unfileConversation,
  fetchArtifacts,
  fetchArtifact,
  updateArtifact,
  streamChat,
} from './api';
import type { SSEEvent, ChatRequest } from './types';

// ── Personas ────────────────────────────────────────────────────────────────

describe('fetchPersonas', () => {
  it('returns parsed persona data', async () => {
    const result = await fetchPersonas();
    expect(result).toEqual(MOCK_PERSONAS);
  });
});

// ── Conversations ───────────────────────────────────────────────────────────

describe('fetchChats', () => {
  it('hits /api/chats with no params', async () => {
    const result = await fetchChats();
    expect(result).toEqual(MOCK_CHATS);
  });

  it('builds correct query string from params', async () => {
    let capturedUrl = '';
    server.use(
      http.get('/api/chats', ({ request }) => {
        capturedUrl = request.url;
        return HttpResponse.json(MOCK_CHATS);
      }),
    );

    await fetchChats({ search: 'hello', limit: 10, project_id: 'p1' });
    const url = new URL(capturedUrl);
    expect(url.searchParams.get('search')).toBe('hello');
    expect(url.searchParams.get('limit')).toBe('10');
    expect(url.searchParams.get('project_id')).toBe('p1');
  });

  it('throws on non-OK response', async () => {
    server.use(http.get('/api/chats', () => new HttpResponse(null, { status: 500 })));
    await expect(fetchChats()).rejects.toThrow('Failed to fetch chats');
  });
});

describe('fetchChat', () => {
  it('returns conversation for valid id', async () => {
    const result = await fetchChat('c1');
    expect(result).toEqual(MOCK_CONVERSATION);
  });
});

describe('deleteChat', () => {
  it('sends DELETE request', async () => {
    let method = '';
    server.use(
      http.delete('/api/chats/:id', ({ request }) => {
        method = request.method;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    await deleteChat('c1');
    expect(method).toBe('DELETE');
  });
});

describe('renameChat', () => {
  it('sends PATCH with JSON body', async () => {
    let body: Record<string, unknown> = {};
    server.use(
      http.patch('/api/chats/:id', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return new HttpResponse(null, { status: 200 });
      }),
    );
    await renameChat('c1', 'New Title');
    expect(body).toEqual({ title: 'New Title' });
  });
});

describe('pinChat / unpinChat', () => {
  it('pinChat sends POST to /api/chats/:id/pin', async () => {
    let method = '';
    let path = '';
    server.use(
      http.post('/api/chats/:id/pin', ({ request }) => {
        method = request.method;
        path = new URL(request.url).pathname;
        return new HttpResponse(null, { status: 200 });
      }),
    );
    await pinChat('c1');
    expect(method).toBe('POST');
    expect(path).toBe('/api/chats/c1/pin');
  });

  it('unpinChat sends DELETE to /api/chats/:id/pin', async () => {
    let method = '';
    let path = '';
    server.use(
      http.delete('/api/chats/:id/pin', ({ request }) => {
        method = request.method;
        path = new URL(request.url).pathname;
        return new HttpResponse(null, { status: 200 });
      }),
    );
    await unpinChat('c1');
    expect(method).toBe('DELETE');
    expect(path).toBe('/api/chats/c1/pin');
  });
});

// ── Projects ────────────────────────────────────────────────────────────────

describe('fetchProjects', () => {
  it('returns projects', async () => {
    const result = await fetchProjects();
    expect(result).toEqual(MOCK_PROJECTS);
  });
});

describe('createProject', () => {
  it('sends POST with body', async () => {
    let body: Record<string, unknown> = {};
    server.use(
      http.post('/api/projects', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: 'p-new', ...body });
      }),
    );
    const result = await createProject({ name: 'New Project', description: 'Desc' });
    expect(body).toEqual({ name: 'New Project', description: 'Desc' });
    expect(result.id).toBe('p-new');
  });
});

describe('fileConversation / unfileConversation', () => {
  it('fileConversation sends POST to nested route', async () => {
    let method = '';
    let path = '';
    server.use(
      http.post('/api/projects/:pid/conversations/:cid', ({ request }) => {
        method = request.method;
        path = new URL(request.url).pathname;
        return new HttpResponse(null, { status: 200 });
      }),
    );
    await fileConversation('p1', 'c1');
    expect(method).toBe('POST');
    expect(path).toBe('/api/projects/p1/conversations/c1');
  });

  it('unfileConversation sends DELETE to nested route', async () => {
    let method = '';
    let path = '';
    server.use(
      http.delete('/api/projects/:pid/conversations/:cid', ({ request }) => {
        method = request.method;
        path = new URL(request.url).pathname;
        return new HttpResponse(null, { status: 200 });
      }),
    );
    await unfileConversation('p1', 'c1');
    expect(method).toBe('DELETE');
    expect(path).toBe('/api/projects/p1/conversations/c1');
  });
});

// ── Artifacts ───────────────────────────────────────────────────────────────

describe('fetchArtifacts', () => {
  it('returns artifacts list', async () => {
    const result = await fetchArtifacts('c1');
    expect(result).toEqual(MOCK_ARTIFACTS);
  });
});

describe('fetchArtifact', () => {
  it('returns artifact without version param', async () => {
    const result = await fetchArtifact('c1', 'a1');
    expect(result).toEqual(MOCK_ARTIFACT_FULL);
  });

  it('passes version as query param', async () => {
    let capturedUrl = '';
    server.use(
      http.get('/api/chats/:cid/artifacts/:aid', ({ request }) => {
        capturedUrl = request.url;
        return HttpResponse.json(MOCK_ARTIFACT_FULL);
      }),
    );
    await fetchArtifact('c1', 'a1', 1);
    expect(new URL(capturedUrl).searchParams.get('version')).toBe('1');
  });
});

describe('updateArtifact', () => {
  it('sends PATCH with content and change_summary', async () => {
    let body: Record<string, unknown> = {};
    server.use(
      http.patch('/api/chats/:cid/artifacts/:aid', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...MOCK_ARTIFACT_FULL, ...body, version: 3 });
      }),
    );
    const result = await updateArtifact('c1', 'a1', 'new content', 'fixed typo');
    expect(body).toEqual({ content: 'new content', change_summary: 'fixed typo' });
    expect(result.version).toBe(3);
  });
});

// ── streamChat ──────────────────────────────────────────────────────────────

function sseStream(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
}

const BASE_REQUEST: ChatRequest = {
  persona: 'chat',
  messages: [{ role: 'user', content: 'Hello' }],
  stream: true,
};

describe('streamChat', () => {
  it('parses token events and calls onEvent', async () => {
    server.use(
      http.post('/api/chat/completions', () => {
        return new HttpResponse(
          sseStream([
            'event: token\ndata: {"content":"Hello"}\n\n',
            'event: token\ndata: {"content":" world"}\n\n',
            'event: done\ndata: {"finish_reason":"stop"}\n\n',
          ]),
          { headers: { 'Content-Type': 'text/event-stream' } },
        );
      }),
    );

    const events: SSEEvent[] = [];
    await streamChat(BASE_REQUEST, (e) => events.push(e));

    expect(events).toHaveLength(3);
    expect(events[0]).toEqual({ type: 'token', data: { content: 'Hello' } });
    expect(events[1]).toEqual({ type: 'token', data: { content: ' world' } });
    expect(events[2]).toEqual({ type: 'done', data: { finish_reason: 'stop' } });
  });

  it('handles conversation, thinking, tool_call, tool_result, and done events', async () => {
    server.use(
      http.post('/api/chat/completions', () => {
        return new HttpResponse(
          sseStream([
            'event: conversation\ndata: {"id":"c1","title":"Test","is_new":true}\n\n',
            'event: thinking\ndata: {"content":"Hmm..."}\n\n',
            'event: tool_call\ndata: {"id":"t1","name":"search","arguments":{"q":"test"}}\n\n',
            'event: tool_result\ndata: {"id":"t1","name":"search","result":"found","duration_ms":50}\n\n',
            'event: done\ndata: {"finish_reason":"stop"}\n\n',
          ]),
          { headers: { 'Content-Type': 'text/event-stream' } },
        );
      }),
    );

    const events: SSEEvent[] = [];
    await streamChat(BASE_REQUEST, (e) => events.push(e));

    expect(events.map((e) => e.type)).toEqual([
      'conversation',
      'thinking',
      'tool_call',
      'tool_result',
      'done',
    ]);
    expect(events[0].data).toEqual({ id: 'c1', title: 'Test', is_new: true });
    expect(events[2].data).toEqual({ id: 't1', name: 'search', arguments: { q: 'test' } });
  });

  it('calls onEvent with error type on non-OK HTTP response', async () => {
    server.use(
      http.post('/api/chat/completions', () => {
        return HttpResponse.json(
          { error: { message: 'Rate limited' } },
          { status: 429 },
        );
      }),
    );

    const events: SSEEvent[] = [];
    await streamChat(BASE_REQUEST, (e) => events.push(e));

    expect(events).toHaveLength(1);
    expect(events[0]).toEqual({ type: 'error', data: { message: 'Rate limited' } });
  });

  it('skips malformed JSON lines', async () => {
    server.use(
      http.post('/api/chat/completions', () => {
        return new HttpResponse(
          sseStream([
            'event: token\ndata: {broken json}\n\n',
            'event: token\ndata: {"content":"ok"}\n\n',
            'event: done\ndata: {"finish_reason":"stop"}\n\n',
          ]),
          { headers: { 'Content-Type': 'text/event-stream' } },
        );
      }),
    );

    const events: SSEEvent[] = [];
    await streamChat(BASE_REQUEST, (e) => events.push(e));

    expect(events).toHaveLength(2);
    expect(events[0]).toEqual({ type: 'token', data: { content: 'ok' } });
    expect(events[1]).toEqual({ type: 'done', data: { finish_reason: 'stop' } });
  });
});
