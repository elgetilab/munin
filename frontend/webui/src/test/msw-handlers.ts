import { http, HttpResponse } from 'msw';

export const MOCK_PERSONAS = {
  personas: [
    { id: 'chat', name: 'Meitner - Chat', description: 'General', icon_url: '', tags: [], capabilities: {}, prompt_suggestions: [] },
    { id: 'code', name: 'Turing - Code', description: 'Code', icon_url: '', tags: [], capabilities: {}, prompt_suggestions: [] },
  ],
  default_persona: 'chat',
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
];
