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
  fetchAdminUsers,
  createAdminUser,
  updateAdminUser,
  deleteAdminUser,
  addAdminUserEmail,
  removeAdminUserEmail,
  setAdminUserPrimaryEmail,
  fetchAdminGroups,
  createAdminGroup,
  updateAdminGroup,
  deleteAdminGroup,
} from './api';
import type { AdminUser, AdminGroup } from './api';
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


// ── Admin: Users + Groups (P1 #11) ──────────────────────────────────────────

const AUTH_ADMIN = 'https://auth.muninai.org/admin';

const MOCK_ADMIN_USER: AdminUser = {
  id: 1,
  name: 'Alice',
  role: 'group_leader',
  group: 'elgeti',
  username: 'alice',
  primary_email: 'alice@example.org',
  emails: ['alice@example.org', 'alice@alias.org'],
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

const MOCK_ADMIN_GROUP: AdminGroup = {
  slug: 'elgeti',
  display_name: 'Elgeti Lab',
  member_count: 2,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

describe('fetchAdminUsers', () => {
  it('returns the users array', async () => {
    server.use(http.get(`${AUTH_ADMIN}/users`, () =>
      HttpResponse.json({ users: [MOCK_ADMIN_USER] })));
    const result = await fetchAdminUsers();
    expect(result).toEqual([MOCK_ADMIN_USER]);
  });

  it('throws with server error message', async () => {
    server.use(http.get(`${AUTH_ADMIN}/users`, () =>
      HttpResponse.json({ error: 'forbidden' }, { status: 403 })));
    await expect(fetchAdminUsers()).rejects.toThrow('forbidden');
  });
});

describe('createAdminUser', () => {
  it('POSTs JSON and parses the response', async () => {
    let body: unknown = null;
    server.use(http.post(`${AUTH_ADMIN}/users`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json(MOCK_ADMIN_USER, { status: 201 });
    }));
    const result = await createAdminUser({
      name: 'Alice', email: 'alice@example.org', role: 'group_leader',
    });
    expect(result).toEqual(MOCK_ADMIN_USER);
    expect(body).toEqual({ name: 'Alice', email: 'alice@example.org', role: 'group_leader' });
  });

  it('surfaces duplicate-email 409', async () => {
    server.use(http.post(`${AUTH_ADMIN}/users`, () =>
      HttpResponse.json({ error: 'email already in use' }, { status: 409 })));
    await expect(createAdminUser({
      name: 'X', email: 'x@e.org',
    })).rejects.toThrow('email already in use');
  });
});

describe('updateAdminUser', () => {
  it('PATCHes the right path', async () => {
    let method = '';
    server.use(http.patch(`${AUTH_ADMIN}/users/42`, ({ request }) => {
      method = request.method;
      return HttpResponse.json(MOCK_ADMIN_USER);
    }));
    await updateAdminUser(42, { role: 'admin' });
    expect(method).toBe('PATCH');
  });
});

describe('deleteAdminUser', () => {
  it('DELETEs the user', async () => {
    let saw = false;
    server.use(http.delete(`${AUTH_ADMIN}/users/7`, () => {
      saw = true;
      return new HttpResponse(null, { status: 204 });
    }));
    await deleteAdminUser(7);
    expect(saw).toBe(true);
  });

  it('throws on 409 conflict', async () => {
    server.use(http.delete(`${AUTH_ADMIN}/users/1`, () =>
      HttpResponse.json({ error: 'cannot delete the last admin' }, { status: 409 })));
    await expect(deleteAdminUser(1)).rejects.toThrow('cannot delete the last admin');
  });
});

describe('email aliases', () => {
  it('add encodes the email path parameter', async () => {
    server.use(http.post(`${AUTH_ADMIN}/users/1/emails`, () =>
      HttpResponse.json(MOCK_ADMIN_USER, { status: 201 })));
    const result = await addAdminUserEmail(1, 'a@b.org');
    expect(result.emails).toContain('alice@alias.org');
  });

  it('remove URL-encodes plus-signs and other unsafe chars', async () => {
    let capturedUrl = '';
    server.use(http.delete(`${AUTH_ADMIN}/users/1/emails/:email`, ({ request }) => {
      capturedUrl = request.url;
      return HttpResponse.json(MOCK_ADMIN_USER);
    }));
    await removeAdminUserEmail(1, 'a+b@example.org');
    // URL should contain the encoded '+'.
    expect(capturedUrl).toContain('a%2Bb%40example.org');
  });

  it('set-primary uses PUT', async () => {
    let method = '';
    server.use(http.put(`${AUTH_ADMIN}/users/1/emails/:email/primary`, ({ request }) => {
      method = request.method;
      return HttpResponse.json(MOCK_ADMIN_USER);
    }));
    await setAdminUserPrimaryEmail(1, 'alice@alias.org');
    expect(method).toBe('PUT');
  });
});

describe('fetchAdminGroups', () => {
  it('returns the groups array', async () => {
    server.use(http.get(`${AUTH_ADMIN}/groups`, () =>
      HttpResponse.json({ groups: [MOCK_ADMIN_GROUP] })));
    const result = await fetchAdminGroups();
    expect(result).toEqual([MOCK_ADMIN_GROUP]);
  });
});

describe('group CRUD', () => {
  it('createAdminGroup POSTs JSON', async () => {
    let body: unknown = null;
    server.use(http.post(`${AUTH_ADMIN}/groups`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json(MOCK_ADMIN_GROUP, { status: 201 });
    }));
    await createAdminGroup({ slug: 'elgeti', display_name: 'Elgeti Lab' });
    expect(body).toEqual({ slug: 'elgeti', display_name: 'Elgeti Lab' });
  });

  it('updateAdminGroup encodes slug in path', async () => {
    let path = '';
    server.use(http.patch(`${AUTH_ADMIN}/groups/:slug`, ({ request }) => {
      path = new URL(request.url).pathname;
      return HttpResponse.json(MOCK_ADMIN_GROUP);
    }));
    await updateAdminGroup('slug with space', 'New name');
    expect(path).toContain('slug%20with%20space');
  });

  it('deleteAdminGroup propagates 409 (group has members)', async () => {
    server.use(http.delete(`${AUTH_ADMIN}/groups/elgeti`, () =>
      HttpResponse.json({ error: 'group has 2 member(s); reassign before deleting' }, { status: 409 })));
    await expect(deleteAdminGroup('elgeti')).rejects.toThrow('group has 2 member');
  });
});
