import { http, HttpResponse } from 'msw';

export const MOCK_PERSONAS = {
  personas: [
    { id: 'munin', name: 'Munin', description: 'Munin assistant', icon_url: '', tags: [], capabilities: {}, prompt_suggestions: [] },
  ],
  default_persona: 'munin',
};

export const MOCK_CHATS = {
  conversations: [
    { id: 'c1', title: 'Test Chat 1', persona: 'chat', created_at: new Date().toISOString(), updated_at: new Date().toISOString(), message_count: 3, preview: 'Hello', pinned: true, pinned_at: new Date().toISOString() },
    { id: 'c2', title: 'Test Chat 2', persona: 'code', created_at: new Date(Date.now() - 86400000).toISOString(), updated_at: new Date(Date.now() - 86400000).toISOString(), message_count: 1, preview: 'Code review', pinned: false, pinned_at: null },
    { id: 'c3', title: 'Old Chat', persona: 'chat', created_at: '2025-01-01T00:00:00Z', updated_at: '2025-01-01T00:00:00Z', message_count: 5, preview: 'Ancient', pinned: false, pinned_at: null },
  ],
  total: 3,
};

export const MOCK_CONVERSATION = {
  id: 'c1',
  title: 'Test Chat 1',
  persona: 'chat',
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  summary: null,
  messages: [
    { id: 'm1', role: 'user', content: 'Hello', created_at: new Date().toISOString() },
    { id: 'm2', role: 'assistant', content: 'Hi there!', created_at: new Date().toISOString() },
  ],
};

export const MOCK_PROJECTS = {
  projects: [
    { id: 'p1', user_email: 'test@test.com', name: 'ML Research', description: 'Machine learning stuff', instructions: '', default_persona: null, archived: false, created_at: new Date().toISOString(), updated_at: new Date().toISOString(), conversation_count: 2 },
    { id: 'p2', user_email: 'test@test.com', name: 'Code Review', description: '', instructions: '', default_persona: 'code', archived: false, created_at: new Date().toISOString(), updated_at: new Date().toISOString(), conversation_count: 0 },
  ],
  total: 2,
};

export const MOCK_ARTIFACTS = {
  artifacts: [
    { id: 'a1', title: 'Analysis Report', content_type: 'text/markdown', latest_version: 2, word_count: 500, byte_size: 3000, source: 'model_written' as const, created_at: new Date().toISOString(), updated_at: new Date().toISOString() },
  ],
  total: 1,
};

export const MOCK_ARTIFACT_FULL = {
  id: 'a1',
  title: 'Analysis Report',
  content_type: 'text/markdown',
  latest_version: 2,
  word_count: 500,
  byte_size: 3000,
  source: 'model_written' as const,
  content: '# Report\n\nSome analysis here.',
  version: 2,
  change_summary: 'Updated conclusion',
  created_by: 'assistant' as const,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

export const MOCK_STATUS = {
  vllm: { status: 'running', model: 'test-model' },
  services: { retrieval: 'ok' },
  timestamp: new Date().toISOString(),
};

export const MOCK_PROFILE = {
  user_email: 'test@test.com',
  about_me: null,
  response_format: null,
  default_persona: null,
  default_rag_sources: null,
  timezone: null,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

export const MOCK_API_KEYS = [
  { id: 'k1', key_prefix: 'sk-munin-abc', name: 'laptop', created_at: new Date().toISOString(),
    last_used_at: null, revoked: false },
];

// ── Admin: users + groups (auth service, not the chat API) ──────────────────
// Admin requests go to `${AUTH_BASE}/admin`, i.e. a different origin from the
// relative /api handlers above, so these are absolute URLs.

function adminUser(over: Partial<Record<string, unknown>> = {}) {
  const base = {
    id: 1,
    first_name: 'Ada',
    last_name: 'Lovelace',
    name: 'Ada Lovelace',
    role: 'user',
    group: 'ml-research',
    username: 'ada',
    primary_email: 'ada@test.com',
    emails: ['ada@test.com'],
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
  return { ...base, ...over };
}

export const MOCK_ADMIN_USERS = [
  adminUser({}),
  adminUser({ id: 2, first_name: 'Grace', last_name: 'Hopper', name: 'Grace Hopper',
              role: 'group_leader', username: 'grace', primary_email: 'grace@test.com',
              emails: ['grace@test.com'] }),
  adminUser({ id: 3, first_name: 'Alan', last_name: 'Turing', name: 'Alan Turing',
              role: 'admin', group: null, username: 'alan', primary_email: 'alan@test.com',
              emails: ['alan@test.com'] }),
];

export const MOCK_ADMIN_GROUPS = [
  { slug: 'ml-research', display_name: 'ML Research', member_count: 2,
    created_at: new Date().toISOString(), updated_at: new Date().toISOString() },
  { slug: 'bio-lab', display_name: 'Bio Lab', member_count: 0,
    created_at: new Date().toISOString(), updated_at: new Date().toISOString() },
];

const ADMIN = 'https://auth.muninai.org/admin';

export const MOCK_ADMIN_ACTIVITY = {
  online: [{ email: 'ada@test.com', last_seen_at: new Date().toISOString(), source: 'chat' }],
  recent: [{ email: 'grace@test.com', last_seen_at: new Date().toISOString(), source: 'api' }],
  all_users: [
    { email: 'ada@test.com', last_seen_at: new Date().toISOString(), source: 'chat' },
    { email: 'grace@test.com', last_seen_at: new Date().toISOString(), source: 'api' },
  ],
  total: 2,
};

export const MOCK_ADMIN_USAGE = {
  period: '2026-09',
  total_tokens: 1234567,
  total_requests: 890,
  active_users: 2,
  top_users: [
    { email: 'ada@test.com', tokens: 1000000, requests: 700, by_source: { chat: { tokens: 1000000, requests: 700 } } },
    { email: 'grace@test.com', tokens: 234567, requests: 190, by_source: { api: { tokens: 234567, requests: 190 } } },
  ],
  tools_usage: { paper_search: 42, web_search: 17 },
};


export const handlers = [
  http.get('/api/personas', () => HttpResponse.json(MOCK_PERSONAS)),
  http.get('/api/chats', () => HttpResponse.json(MOCK_CHATS)),
  http.get('/api/chats/:id', ({ params }) => {
    if (params.id === 'c1') return HttpResponse.json(MOCK_CONVERSATION);
    return new HttpResponse(null, { status: 404 });
  }),
  http.delete('/api/chats/:id', () => new HttpResponse(null, { status: 204 })),
  http.patch('/api/chats/:id', () => new HttpResponse(null, { status: 200 })),
  http.post('/api/chats/:id/pin', () => new HttpResponse(null, { status: 200 })),
  http.delete('/api/chats/:id/pin', () => new HttpResponse(null, { status: 200 })),
  http.get('/api/status', () => HttpResponse.json(MOCK_STATUS)),
  http.get('/api/keys', () => HttpResponse.json({ keys: MOCK_API_KEYS })),
  http.post('/api/keys', async ({ request }) => {
    const body = await request.json() as Record<string, unknown>;
    return HttpResponse.json({ key: 'sk-munin-secret-value', ...body });
  }),
  http.delete('/api/keys/:id', () => new HttpResponse(null, { status: 204 })),
  http.get('/api/profile', () => HttpResponse.json(MOCK_PROFILE)),
  http.put('/api/profile', () => HttpResponse.json(MOCK_PROFILE)),
  http.get('/api/projects', () => HttpResponse.json(MOCK_PROJECTS)),
  http.get('/api/projects/:id', ({ params }) => {
    const p = MOCK_PROJECTS.projects.find(p => p.id === params.id);
    return p ? HttpResponse.json(p) : new HttpResponse(null, { status: 404 });
  }),
  http.post('/api/projects', async ({ request }) => {
    const body = await request.json() as Record<string, unknown>;
    return HttpResponse.json({ id: 'p-new', ...body, user_email: 'test@test.com', archived: false, created_at: new Date().toISOString(), updated_at: new Date().toISOString(), conversation_count: 0 });
  }),
  http.patch('/api/projects/:id', async ({ request, params }) => {
    const body = await request.json() as Record<string, unknown>;
    const existing = MOCK_PROJECTS.projects.find(p => p.id === params.id);
    return HttpResponse.json({ ...existing, ...body });
  }),
  http.delete('/api/projects/:id', () => new HttpResponse(null, { status: 204 })),
  http.post('/api/projects/:pid/conversations/:cid', () => new HttpResponse(null, { status: 200 })),
  http.delete('/api/projects/:pid/conversations/:cid', () => new HttpResponse(null, { status: 200 })),
  http.get('/api/chats/:cid/artifacts', () => HttpResponse.json(MOCK_ARTIFACTS)),
  http.get('/api/chats/:cid/artifacts/:aid', () => HttpResponse.json(MOCK_ARTIFACT_FULL)),
  http.patch('/api/chats/:cid/artifacts/:aid', async ({ request }) => {
    const body = await request.json() as Record<string, unknown>;
    return HttpResponse.json({ ...MOCK_ARTIFACT_FULL, ...body, version: 3 });
  }),
  http.get('https://auth.muninai.org/auth/me', () => HttpResponse.json({ email: 'test@test.com', name: 'Test User', full_name: 'Test User', nickname: 'tester', avatar: '' })),
  http.get('/api/announcement', () => HttpResponse.json({ announcement: null })),
  http.get('/api/usage/me', () => HttpResponse.json({ current_month: { tokens_used: 0, tokens_limit: 1000000, tokens_remaining: 1000000, requests: 0, tools_used: {} }, api_keys: [] })),

  http.get('/api/usage/admin/activity', () => HttpResponse.json(MOCK_ADMIN_ACTIVITY)),
  http.get('/api/usage/admin', () => HttpResponse.json(MOCK_ADMIN_USAGE)),

  http.get(`${ADMIN}/users`, () => HttpResponse.json({ users: MOCK_ADMIN_USERS })),
  http.post(`${ADMIN}/users`, async ({ request }) => {
    const body = await request.json() as Record<string, unknown>;
    return HttpResponse.json({ user: { ...adminUser({ id: 99 }), ...body } });
  }),
  http.patch(`${ADMIN}/users/:id`, async ({ request, params }) => {
    const body = await request.json() as Record<string, unknown>;
    return HttpResponse.json({ user: { ...adminUser({ id: Number(params.id) }), ...body } });
  }),
  http.delete(`${ADMIN}/users/:id`, () => new HttpResponse(null, { status: 204 })),

  http.get(`${ADMIN}/groups`, () => HttpResponse.json({ groups: MOCK_ADMIN_GROUPS })),
  http.post(`${ADMIN}/groups`, async ({ request }) => {
    const body = await request.json() as Record<string, unknown>;
    return HttpResponse.json({ group: { slug: 'new-group', display_name: 'New Group', member_count: 0,
      created_at: new Date().toISOString(), updated_at: new Date().toISOString(), ...body } });
  }),
  http.patch(`${ADMIN}/groups/:slug`, async ({ request, params }) => {
    const body = await request.json() as Record<string, unknown>;
    return HttpResponse.json({ group: { ...MOCK_ADMIN_GROUPS[0], slug: String(params.slug), ...body } });
  }),
  http.delete(`${ADMIN}/groups/:slug`, () => new HttpResponse(null, { status: 204 })),
  http.get(`${ADMIN}/groups/:slug/members`, () => HttpResponse.json({ members: [MOCK_ADMIN_USERS[0]] })),
  http.post(`${ADMIN}/groups/:slug/members`, () => HttpResponse.json({ members: MOCK_ADMIN_USERS.slice(0, 2) })),
  http.delete(`${ADMIN}/groups/:slug/members/:userId`, () => HttpResponse.json({ members: [] })),
];
