import type { Persona, ConversationSummary, Conversation, ChatRequest, SystemStatus, SSEEvent, MuninProfile, ArtifactSummary, ArtifactFull, Project, TagCatalog, TagPapersResponse, EmbeddingMap } from './types';

const API = '/api';

// ── Personas ─────────────────────────────────────────────────────────────────

export async function fetchPersonas(): Promise<{ personas: Persona[]; default_persona: string }> {
  const res = await fetch(`${API}/personas`);
  if (!res.ok) throw new Error('Failed to fetch personas');
  return res.json();
}

// ── Conversations ────────────────────────────────────────────────────────────

export async function fetchChats(params?: {
  limit?: number;
  offset?: number;
  persona?: string;
  search?: string;
  project_id?: string;
}): Promise<{ conversations: ConversationSummary[]; total: number }> {
  const query = new URLSearchParams();
  if (params?.limit) query.set('limit', String(params.limit));
  if (params?.offset) query.set('offset', String(params.offset));
  if (params?.persona) query.set('persona', params.persona);
  if (params?.search) query.set('search', params.search);
  if (params?.project_id) query.set('project_id', params.project_id);
  const qs = query.toString();
  const res = await fetch(`${API}/chats${qs ? '?' + qs : ''}`);
  if (!res.ok) throw new Error('Failed to fetch chats');
  return res.json();
}

export async function fetchChat(id: string): Promise<Conversation> {
  const res = await fetch(`${API}/chats/${id}`);
  if (!res.ok) throw new Error('Failed to fetch chat');
  return res.json();
}

export async function deleteChat(id: string): Promise<void> {
  const res = await fetch(`${API}/chats/${id}`, { method: 'DELETE' });
  if (!res.ok) throw new Error('Failed to delete chat');
}

export async function renameChat(id: string, title: string): Promise<void> {
  const res = await fetch(`${API}/chats/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  });
  if (!res.ok) throw new Error('Failed to rename chat');
}

export async function pinChat(id: string): Promise<void> {
  const res = await fetch(`${API}/chats/${id}/pin`, { method: 'POST' });
  if (!res.ok) throw new Error('Failed to pin chat');
}

export async function unpinChat(id: string): Promise<void> {
  const res = await fetch(`${API}/chats/${id}/pin`, { method: 'DELETE' });
  if (!res.ok) throw new Error('Failed to unpin chat');
}

// ── User ────────────────────────────────────────────────────────────────────

export interface UserProfile {
  email: string;
  name: string;
  full_name: string;
  nickname: string;
  avatar: string;
}

export async function fetchMe(): Promise<UserProfile> {
  const res = await fetch('https://auth.muninai.org/auth/me', { credentials: 'include' });
  if (!res.ok) throw new Error('Failed to fetch user info');
  return res.json();
}

export async function updateProfile(data: { full_name?: string; nickname?: string; avatar?: string }): Promise<UserProfile> {
  const res = await fetch('https://auth.muninai.org/auth/me', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
    credentials: 'include',
  });
  if (!res.ok) throw new Error('Failed to update profile');
  return res.json();
}

// ── Munin Profile ──────────────────────────────────────────────────────────

export async function fetchMuninProfile(): Promise<MuninProfile> {
  const res = await fetch(`${API}/profile`);
  if (!res.ok) throw new Error('Failed to fetch profile');
  return res.json();
}

export async function updateMuninProfile(data: Partial<Pick<MuninProfile, 'about_me' | 'response_format' | 'default_persona' | 'default_rag_sources' | 'timezone'>>): Promise<MuninProfile> {
  const res = await fetch(`${API}/profile`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error('Failed to update profile');
  return res.json();
}

export async function deleteMuninProfile(): Promise<void> {
  const res = await fetch(`${API}/profile`, { method: 'DELETE' });
  if (!res.ok) throw new Error('Failed to delete profile');
}

// ── API Keys ────────────────────────────────────────────────────────────────

export interface ApiKeyInfo {
  id: string;
  key_prefix: string;
  name: string;
  created_at: string;
  last_used_at: string | null;
  revoked: boolean;
}

export interface CreateKeyResponse {
  id: string;
  key: string;
  key_prefix: string;
  name: string;
  created_at: string;
  warning: string;
}

export interface UsageStats {
  current_month: {
    tokens_used: number;
    tokens_limit: number;
    tokens_remaining: number;
    requests: number;
    tools_used: Record<string, number>;
  };
  api_keys: {
    key_prefix: string;
    name: string;
    tokens_this_month: number;
    last_used: string | null;
  }[];
  is_admin?: boolean;
}

export async function fetchApiKeys(): Promise<{ keys: ApiKeyInfo[] }> {
  const res = await fetch(`${API}/keys`);
  if (!res.ok) throw new Error('Failed to fetch API keys');
  return res.json();
}

export async function createApiKey(name: string): Promise<CreateKeyResponse> {
  const res = await fetch(`${API}/keys`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) throw new Error('Failed to create API key');
  return res.json();
}

export async function revokeApiKey(id: string): Promise<void> {
  const res = await fetch(`${API}/keys/${id}`, { method: 'DELETE' });
  if (!res.ok) throw new Error('Failed to revoke API key');
}

export async function fetchUsageStats(): Promise<UsageStats> {
  const res = await fetch(`${API}/usage/me`);
  if (!res.ok) throw new Error('Failed to fetch usage stats');
  return res.json();
}

// ── Admin ────────────────────────────────────────────────────────────────

export interface AdminActivityUser {
  email: string;
  last_seen_at: string;
  source: string;
}

export interface AdminActivity {
  online: AdminActivityUser[];
  recent: AdminActivityUser[];
  all_users: AdminActivityUser[];
  total: number;
}

export interface AdminUsageUser {
  email: string;
  tokens: number;
  requests: number;
  by_source: Record<string, { tokens: number; requests: number }>;
}

export interface AdminUsage {
  period: string;
  total_tokens: number;
  total_requests: number;
  active_users: number;
  top_users: AdminUsageUser[];
  tools_usage: Record<string, number>;
}

export async function fetchAdminActivity(): Promise<AdminActivity> {
  const res = await fetch(`${API}/usage/admin/activity`);
  if (!res.ok) throw new Error('Failed to fetch admin activity');
  return res.json();
}

export async function fetchAdminUsage(): Promise<AdminUsage> {
  const res = await fetch(`${API}/usage/admin`);
  if (!res.ok) throw new Error('Failed to fetch admin usage');
  return res.json();
}

// ── Announcements ───────────────────────────────────────────────────────────

export interface Announcement {
  message: string;
  level: 'info' | 'warning' | 'error';
  updated_at: string;
}

export async function fetchAnnouncement(): Promise<Announcement | null> {
  const res = await fetch(`${API}/announcement`);
  if (!res.ok) return null;
  const data = await res.json();
  return data.announcement || null;
}

export async function setAnnouncement(message: string, level: string = 'info'): Promise<void> {
  const res = await fetch(`${API}/announcement`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, level }),
  });
  if (!res.ok) throw new Error('Failed to set announcement');
}

export async function clearAnnouncement(): Promise<void> {
  const res = await fetch(`${API}/announcement`, { method: 'DELETE' });
  if (!res.ok) throw new Error('Failed to clear announcement');
}

// ── Documents ───────────────────────────────────────────────────────────────

export interface UploadedDocument {
  document_id: string;
  filename: string;
  chunks: number;
  status: 'embedded' | 'stored';
  upload_time: string;
}

export async function uploadDocument(
  file: File,
  conversationId?: string | null,
  onProgress?: (pct: number) => void,
): Promise<UploadedDocument> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${API}/documents/upload`);

    if (onProgress) {
      xhr.upload.addEventListener('progress', (e) => {
        if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
      });
    }

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText));
      } else {
        const err = JSON.parse(xhr.responseText).error?.message || `Upload failed (${xhr.status})`;
        reject(new Error(err));
      }
    };
    xhr.onerror = () => reject(new Error('Upload failed'));

    const form = new FormData();
    form.append('file', file);
    if (conversationId) form.append('conversation_id', conversationId);
    xhr.send(form);
  });
}

export async function fetchDocuments(conversationId?: string): Promise<{ documents: UploadedDocument[] }> {
  const qs = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : '';
  const res = await fetch(`${API}/documents${qs}`);
  if (!res.ok) throw new Error('Failed to fetch documents');
  return res.json();
}

export async function deleteDocument(id: string): Promise<void> {
  const res = await fetch(`${API}/documents/${id}`, { method: 'DELETE' });
  if (!res.ok) throw new Error('Failed to delete document');
}

// ── Reports ─────────────────────────────────────────────────────────────

export async function reportChat(
  conversationId: string,
  reason?: string,
): Promise<{ reported: boolean; report_id: string }> {
  const res = await fetch(`${API}/chats/${conversationId}/report`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason: reason || undefined }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err?.error?.message || `Report failed (${res.status})`);
  }
  return res.json();
}

// ── Artifacts ───────────────────────────────────────────────────────────────

export async function fetchArtifacts(conversationId: string): Promise<{ artifacts: ArtifactSummary[]; total: number }> {
  const res = await fetch(`${API}/chats/${conversationId}/artifacts`);
  if (!res.ok) throw new Error('Failed to fetch artifacts');
  return res.json();
}

export async function fetchArtifact(conversationId: string, artifactId: string, version?: number): Promise<ArtifactFull> {
  const qs = version ? `?version=${version}` : '';
  const res = await fetch(`${API}/chats/${conversationId}/artifacts/${artifactId}${qs}`);
  if (!res.ok) throw new Error('Failed to fetch artifact');
  return res.json();
}

export async function updateArtifact(conversationId: string, artifactId: string, content: string, changeSummary: string): Promise<ArtifactFull> {
  const res = await fetch(`${API}/chats/${conversationId}/artifacts/${artifactId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content, change_summary: changeSummary }),
  });
  if (!res.ok) throw new Error('Failed to update artifact');
  return res.json();
}

// ── Projects ────────────────────────────────────────────────────────────────

export async function fetchProjects(archived?: boolean): Promise<{ projects: Project[]; total: number }> {
  const qs = archived ? '?archived=true' : '';
  const res = await fetch(`${API}/projects${qs}`);
  if (!res.ok) throw new Error('Failed to fetch projects');
  return res.json();
}

export async function fetchProject(id: string): Promise<Project> {
  const res = await fetch(`${API}/projects/${id}`);
  if (!res.ok) throw new Error('Failed to fetch project');
  return res.json();
}

export async function createProject(data: { name: string; description?: string; instructions?: string; default_persona?: string }): Promise<Project> {
  const res = await fetch(`${API}/projects`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error('Failed to create project');
  return res.json();
}

export async function updateProject(id: string, data: Partial<Pick<Project, 'name' | 'description' | 'instructions' | 'default_persona' | 'archived'>>): Promise<Project> {
  const res = await fetch(`${API}/projects/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error('Failed to update project');
  return res.json();
}

export async function deleteProject(id: string): Promise<void> {
  const res = await fetch(`${API}/projects/${id}`, { method: 'DELETE' });
  if (!res.ok) throw new Error('Failed to delete project');
}

export async function fileConversation(projectId: string, conversationId: string): Promise<void> {
  const res = await fetch(`${API}/projects/${projectId}/conversations/${conversationId}`, { method: 'POST' });
  if (!res.ok) throw new Error('Failed to file conversation');
}

export async function unfileConversation(projectId: string, conversationId: string): Promise<void> {
  const res = await fetch(`${API}/projects/${projectId}/conversations/${conversationId}`, { method: 'DELETE' });
  if (!res.ok) throw new Error('Failed to unfile conversation');
}

// ── Knowledge / Tags ────────────────────────────────────────────────────────

let tagCatalogCache: { data: TagCatalog; fetchedAt: number } | null = null;
const TAG_CACHE_TTL = 120_000; // 2 minutes

export async function fetchTags(): Promise<TagCatalog> {
  if (tagCatalogCache && Date.now() - tagCatalogCache.fetchedAt < TAG_CACHE_TTL) {
    return tagCatalogCache.data;
  }
  const res = await fetch(`${API}/tags`);
  if (!res.ok) throw new Error('Failed to fetch tags');
  const data = await res.json();
  tagCatalogCache = { data, fetchedAt: Date.now() };
  return data;
}

export async function fetchTagPapers(
  kind: string,
  slug: string,
  params?: { offset?: number; limit?: number; sort?: string },
): Promise<TagPapersResponse> {
  const query = new URLSearchParams();
  if (params?.offset) query.set('offset', String(params.offset));
  if (params?.limit) query.set('limit', String(params.limit));
  if (params?.sort) query.set('sort', params.sort);
  const qs = query.toString();
  const res = await fetch(`${API}/tags/${kind}/${slug}/papers${qs ? '?' + qs : ''}`);
  if (!res.ok) throw new Error('Failed to fetch papers');
  return res.json();
}

export async function fetchEmbeddingMap(): Promise<EmbeddingMap> {
  const res = await fetch(`${API}/embedding_map`);
  if (!res.ok) throw new Error('Failed to fetch embedding map');
  return res.json();
}

export async function fetchPaperEnriched(doi: string): Promise<Record<string, unknown>> {
  const res = await fetch(`/paper/${encodeURIComponent(doi)}/enriched`);
  if (!res.ok) throw new Error('Failed to fetch paper details');
  return res.json();
}

// ── Status ───────────────────────────────────────────────────────────────────

export async function fetchStatus(): Promise<SystemStatus> {
  const res = await fetch(`${API}/status`);
  if (!res.ok) throw new Error('Failed to fetch status');
  return res.json();
}

// ── Streaming Chat ───────────────────────────────────────────────────────────

// Backoff schedule for SSE reconnects (P1 #10). Each entry is the delay
// before the corresponding attempt. After the last entry, give up and
// surface an error.
const RECONNECT_BACKOFF_MS = [1000, 2000, 4000, 8000, 16000, 30000];

// sessionStorage key for the active SSE stream (P1 #10 Phase 3, survives
// browser refresh, dies with the tab). Holds {stream_id, conversation_id,
// last_event_id}. Ephemeral chats are deliberately not persisted: a
// refresh of an ephemeral chat has nothing to restore from chat_store so
// the resume target would be meaningless.
const ACTIVE_STREAM_KEY = 'munin.active_stream';

export interface ActiveStreamPersist {
  stream_id: string;
  conversation_id: string;
  last_event_id?: string;
}

export function readActiveStream(): ActiveStreamPersist | null {
  try {
    const raw = sessionStorage.getItem(ACTIVE_STREAM_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed && parsed.stream_id && parsed.conversation_id) return parsed;
  } catch { /* fall through */ }
  return null;
}

function writeActiveStream(value: ActiveStreamPersist): void {
  try { sessionStorage.setItem(ACTIVE_STREAM_KEY, JSON.stringify(value)); } catch { /* ignore */ }
}

export function clearActiveStream(): void {
  try { sessionStorage.removeItem(ACTIVE_STREAM_KEY); } catch { /* ignore */ }
}

/**
 * Consume a single SSE response body, dispatching events via onEvent.
 * Updates `state.lastEventId` and `state.streamId` from `id:` lines and
 * from the first `conversation` event respectively.
 *
 * Returns:
 *  - "done"  the server emitted a `done` SSE event (stream completed).
 *  - "drop"  the reader ended without a `done` event (connection lost).
 *  - "error" the server returned a body-level error event; treat as terminal.
 */
async function _consumeSSE(
  res: Response,
  state: { streamId?: string; lastEventId?: string; sawDone: boolean },
  onEvent: (event: SSEEvent) => void,
): Promise<'done' | 'drop' | 'error'> {
  const reader = res.body?.getReader();
  if (!reader) return 'drop';

  const decoder = new TextDecoder();
  let buffer = '';
  let currentEvent = '';
  let currentId = '';

  const flush = (line: string) => {
    if (line.startsWith('event: ')) {
      currentEvent = line.slice(7).trim();
    } else if (line.startsWith('id: ')) {
      currentId = line.slice(4).trim();
    } else if (line.startsWith('data: ') && currentEvent) {
      try {
        const data = JSON.parse(line.slice(6));
        // P1 #10: capture stream_id off the first conversation event;
        // capture lastEventId off every event with an id: line. Persist
        // to sessionStorage so a browser refresh can resume.
        if (currentEvent === 'conversation' && data?.stream_id && !state.streamId) {
          state.streamId = data.stream_id;
          // Skip persistence for ephemeral chats (nothing to restore on F5).
          if (!data.ephemeral && data?.id) {
            writeActiveStream({
              stream_id: data.stream_id,
              conversation_id: data.id,
              last_event_id: state.lastEventId,
            });
          }
        }
        if (currentId) {
          state.lastEventId = currentId;
          // Update the persisted last_event_id (best-effort; if the
          // chat was ephemeral, no entry exists and this is a no-op).
          const persisted = readActiveStream();
          if (persisted && persisted.stream_id === state.streamId) {
            writeActiveStream({ ...persisted, last_event_id: currentId });
          }
        }
        if (currentEvent === 'done') {
          state.sawDone = true;
          clearActiveStream();
        }
        if (currentEvent === 'error') {
          // A server-emitted error is terminal — don't try to resume
          // through a logical failure. Flip sawDone so the consumer
          // returns 'done' (not 'drop') and the caller's reconnect
          // loop exits.
          state.sawDone = true;
          clearActiveStream();
        }
        onEvent({ type: currentEvent, data } as SSEEvent);
      } catch {
        // skip malformed JSON
      }
      currentEvent = '';
      currentId = '';
    } else if (line.trim() === '') {
      currentEvent = '';
      currentId = '';
    }
  };

  while (true) {
    let chunk: ReadableStreamReadResult<Uint8Array>;
    try {
      chunk = await reader.read();
    } catch {
      return 'drop';
    }
    if (chunk.done) {
      return state.sawDone ? 'done' : 'drop';
    }
    buffer += decoder.decode(chunk.value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';
    for (const line of lines) flush(line);
  }
}

async function _attemptResume(
  state: { streamId?: string; lastEventId?: string; sawDone: boolean },
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<'done' | 'drop' | 'gone' | 'error'> {
  if (!state.streamId) return 'error';
  const headers: HeadersInit = { 'Accept': 'text/event-stream' };
  if (state.lastEventId) headers['Last-Event-ID'] = state.lastEventId;
  const url = `${API}/chat/completions/resume?stream_id=${encodeURIComponent(state.streamId)}`;
  let res: Response;
  try {
    res = await fetch(url, { method: 'GET', headers, signal });
  } catch {
    return 'drop';
  }
  if (res.status === 410) return 'gone';
  if (!res.ok) return 'error';
  return _consumeSSE(res, state, onEvent);
}

function _sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const t = setTimeout(resolve, ms);
    if (signal) {
      const onAbort = () => { clearTimeout(t); reject(new DOMException('Aborted', 'AbortError')); };
      if (signal.aborted) onAbort();
      else signal.addEventListener('abort', onAbort, { once: true });
    }
  });
}

export async function streamChat(
  request: ChatRequest,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${API}/chat/completions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
    signal,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: { message: 'Request failed' } }));
    onEvent({ type: 'error', data: { message: err.error?.message || `HTTP ${res.status}` } });
    return;
  }

  const state = { sawDone: false } as { streamId?: string; lastEventId?: string; sawDone: boolean };
  let outcome = await _consumeSSE(res, state, onEvent);

  // Reconnect loop (P1 #10). A clean `done` ends the loop. Anything
  // else with a known stream_id retries with backoff until the server
  // says 410 (stream gone) or we exhaust the schedule.
  let attempt = 0;
  while (outcome !== 'done' && outcome !== 'error' && state.streamId) {
    if (attempt >= RECONNECT_BACKOFF_MS.length) {
      clearActiveStream();
      onEvent({
        type: 'error',
        data: { message: 'Lost connection and could not resume after several attempts.' },
      });
      return;
    }
    const delay = RECONNECT_BACKOFF_MS[attempt++];
    onEvent({
      type: 'reconnecting',
      data: { attempt, max_attempts: RECONNECT_BACKOFF_MS.length, delay_s: delay / 1000 },
    });
    try {
      await _sleep(delay, signal);
    } catch {
      return;
    }
    outcome = await _attemptResume(state, onEvent, signal);
    if (outcome === 'gone') {
      clearActiveStream();
      onEvent({
        type: 'error',
        data: { message: 'Stream is no longer available on the server.' },
      });
      return;
    }
  }
}

// Resume an existing stream after a page reload. Returns the same
// resolution states as streamChat. Used by useChat on mount when
// sessionStorage carries an active stream.
export async function resumeChat(
  streamId: string,
  lastEventId: string | undefined,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const state = {
    streamId,
    lastEventId,
    sawDone: false,
  } as { streamId?: string; lastEventId?: string; sawDone: boolean };
  let outcome = await _attemptResume(state, onEvent, signal);
  if (outcome === 'gone') {
    clearActiveStream();
    onEvent({
      type: 'error',
      data: { message: 'Stream is no longer available on the server.' },
    });
    return;
  }
  // Same backoff schedule as streamChat — a refresh that lands while
  // the server is mid-shutdown can still recover.
  let attempt = 0;
  while (outcome !== 'done' && outcome !== 'error' && state.streamId) {
    if (attempt >= RECONNECT_BACKOFF_MS.length) {
      clearActiveStream();
      onEvent({
        type: 'error',
        data: { message: 'Lost connection and could not resume after several attempts.' },
      });
      return;
    }
    const delay = RECONNECT_BACKOFF_MS[attempt++];
    onEvent({
      type: 'reconnecting',
      data: { attempt, max_attempts: RECONNECT_BACKOFF_MS.length, delay_s: delay / 1000 },
    });
    try { await _sleep(delay, signal); } catch { return; }
    outcome = await _attemptResume(state, onEvent, signal);
    if (outcome === 'gone') {
      clearActiveStream();
      onEvent({
        type: 'error',
        data: { message: 'Stream is no longer available on the server.' },
      });
      return;
    }
  }
}
