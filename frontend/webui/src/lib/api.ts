import type { Persona, ConversationSummary, Conversation, ChatRequest, SystemStatus, SSEEvent, MuninProfile, ArtifactSummary, ArtifactFull, Project, TagCatalog, TagPapersResponse, EmbeddingMap } from './types';
import { AUTH_BASE } from './urls';

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
  const res = await fetch(`${AUTH_BASE}/auth/me`, { credentials: 'include' });
  if (!res.ok) throw new Error('Failed to fetch user info');
  return res.json();
}

export async function updateProfile(data: { full_name?: string; nickname?: string; avatar?: string }): Promise<UserProfile> {
  const res = await fetch(`${AUTH_BASE}/auth/me`, {
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

// ── Admin: Users + Groups (P1 #11) ──────────────────────────────────────────
//
// CRUD against the auth service. All endpoints require an admin session
// cookie. Failure surfaces a server-supplied error string when present so
// the UI can show "email already in use" instead of a generic "Failed".

const AUTH_ADMIN = `${AUTH_BASE}/admin`;

export type AdminRole = 'user' | 'group_leader' | 'admin';

export interface AdminUser {
  id: number;
  first_name: string;
  last_name: string;
  // `name` is a back-compat alias the auth service computes as
  // f"{first_name} {last_name}".strip(). Kept on the type so legacy
  // consumers (retrieval's X-Munin-Name header, /auth/me) don't need
  // to change all at once.
  name: string;
  role: AdminRole;
  group: string | null;
  username: string | null;
  primary_email: string;
  emails: string[];
  created_at: string;
  updated_at: string;
}

export interface AdminGroup {
  slug: string;
  display_name: string;
  member_count: number;
  created_at: string;
  updated_at: string;
}

async function adminRequest(path: string, init: RequestInit = {}): Promise<Response> {
  const res = await fetch(`${AUTH_ADMIN}${path}`, {
    credentials: 'include',
    ...init,
    headers: {
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      ...(init.headers || {}),
    },
  });
  return res;
}

async function adminJSON<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await adminRequest(path, init);
  if (!res.ok) {
    let detail = '';
    try {
      const body = await res.json();
      detail = body?.error || '';
    } catch {
      // not JSON
    }
    throw new Error(detail || `Request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}

export async function fetchAdminUsers(): Promise<AdminUser[]> {
  const data = await adminJSON<{ users: AdminUser[] }>('/users');
  return data.users;
}

export async function createAdminUser(input: {
  first_name: string;
  last_name?: string;
  email: string;
  role?: AdminRole;
  group?: string | null;
  username?: string | null;
}): Promise<AdminUser> {
  return adminJSON<AdminUser>('/users', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export async function updateAdminUser(id: number, patch: {
  first_name?: string;
  last_name?: string;
  role?: AdminRole;
  group?: string | null;
  username?: string | null;
}): Promise<AdminUser> {
  return adminJSON<AdminUser>(`/users/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  });
}

export async function deleteAdminUser(id: number): Promise<void> {
  const res = await adminRequest(`/users/${id}`, { method: 'DELETE' });
  if (!res.ok) {
    let detail = '';
    try { detail = (await res.json())?.error || ''; } catch { /* */ }
    throw new Error(detail || `Delete failed (${res.status})`);
  }
}

export async function addAdminUserEmail(id: number, email: string): Promise<AdminUser> {
  return adminJSON<AdminUser>(`/users/${id}/emails`, {
    method: 'POST',
    body: JSON.stringify({ email }),
  });
}

export async function removeAdminUserEmail(id: number, email: string): Promise<AdminUser> {
  return adminJSON<AdminUser>(`/users/${id}/emails/${encodeURIComponent(email)}`, {
    method: 'DELETE',
  });
}

export async function setAdminUserPrimaryEmail(id: number, email: string): Promise<AdminUser> {
  return adminJSON<AdminUser>(`/users/${id}/emails/${encodeURIComponent(email)}/primary`, {
    method: 'PUT',
  });
}

export async function fetchAdminGroups(): Promise<AdminGroup[]> {
  const data = await adminJSON<{ groups: AdminGroup[] }>('/groups');
  return data.groups;
}

export async function createAdminGroup(input: {
  slug: string;
  display_name: string;
}): Promise<AdminGroup> {
  return adminJSON<AdminGroup>('/groups', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export async function updateAdminGroup(slug: string, display_name: string): Promise<AdminGroup> {
  return adminJSON<AdminGroup>(`/groups/${encodeURIComponent(slug)}`, {
    method: 'PATCH',
    body: JSON.stringify({ display_name }),
  });
}

export async function deleteAdminGroup(slug: string): Promise<void> {
  const res = await adminRequest(`/groups/${encodeURIComponent(slug)}`, { method: 'DELETE' });
  if (!res.ok) {
    let detail = '';
    try { detail = (await res.json())?.error || ''; } catch { /* */ }
    throw new Error(detail || `Delete failed (${res.status})`);
  }
}

// ── Group membership ───────────────────────────────────────────────────────
// A user can belong to several groups. These manage the membership join
// table; the user's primary group (AdminUser.group) is maintained server-side.

export async function fetchGroupMembers(slug: string): Promise<AdminUser[]> {
  const data = await adminJSON<{ members: AdminUser[] }>(`/groups/${encodeURIComponent(slug)}/members`);
  return data.members;
}

export async function addGroupMember(slug: string, userId: number): Promise<AdminUser[]> {
  const data = await adminJSON<{ members: AdminUser[] }>(`/groups/${encodeURIComponent(slug)}/members`, {
    method: 'POST',
    body: JSON.stringify({ user_id: userId }),
  });
  return data.members;
}

export async function removeGroupMember(slug: string, userId: number): Promise<AdminUser[]> {
  const data = await adminJSON<{ members: AdminUser[] }>(
    `/groups/${encodeURIComponent(slug)}/members/${userId}`,
    { method: 'DELETE' },
  );
  return data.members;
}

// ── Admin metrics (Prometheus proxy) ───────────────────────────────────────

/**
 * Prometheus range-query response, narrowed to the fields the
 * dashboard actually reads. The proxy on retrieval forwards the
 * upstream body verbatim; nothing else is added.
 */
export interface PromSeries {
  metric: Record<string, string>;
  values: Array<[number, string]>; // [unix_seconds, value_as_string]
}

export interface PromRangeResponse {
  status: 'success' | 'error';
  data: {
    resultType: 'matrix';
    result: PromSeries[];
  };
  errorType?: string;
  error?: string;
}

export interface PromInstantResponse {
  status: 'success' | 'error';
  data: {
    resultType: 'vector' | 'scalar' | 'string';
    result: Array<{ metric: Record<string, string>; value: [number, string] }>;
  };
  errorType?: string;
  error?: string;
}

/** Throws a generic Error with the upstream message on non-2xx. */
async function metricsRequest(path: string, body: object): Promise<unknown> {
  const res = await fetch(`${API}/admin/metrics/${path}`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = '';
    try {
      const err = await res.json();
      detail = err?.error?.message || err?.error || '';
    } catch {
      /* not JSON */
    }
    throw new Error(detail || `Metrics request failed (${res.status})`);
  }
  return res.json();
}

export async function metricsQueryRange(
  query: string,
  start: Date,
  end: Date,
  stepSeconds: number,
): Promise<PromRangeResponse> {
  return (await metricsRequest('query_range', {
    query,
    start: start.toISOString(),
    end: end.toISOString(),
    step: `${stepSeconds}s`,
  })) as PromRangeResponse;
}

export async function metricsQuery(
  query: string,
  at?: Date,
): Promise<PromInstantResponse> {
  return (await metricsRequest('query', {
    query,
    ...(at ? { time: at.toISOString() } : {}),
  })) as PromInstantResponse;
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
  // Present only when a TEXT upload stored without embedding, saying why:
  // 'no_text_layer' (a scanned/printed PDF; OCR is running in the background
  // and the document becomes searchable shortly), 'no_text_extracted' (parser
  // read nothing and no recovery applies), 'no_chunks', or 'index_unavailable'
  // (operational). Images store with no reason, since that is the correct
  // outcome for them. See BACKEND-API.md 4.9.
  reason?: 'no_text_layer' | 'no_text_extracted' | 'no_chunks' | 'index_unavailable';
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
        try {
          resolve(JSON.parse(xhr.responseText));
        } catch {
          reject(new Error('Upload succeeded but the server response was malformed'));
        }
      } else {
        // The backend returns { error: { message } }, but a gateway/proxy
        // error (502/504) can send a non-JSON body. Guard the parse so the
        // real status still surfaces instead of a thrown SyntaxError.
        let msg = `Upload failed (${xhr.status})`;
        try {
          msg = JSON.parse(xhr.responseText).error?.message || msg;
        } catch {
          /* non-JSON body - keep the status-based message */
        }
        reject(new Error(msg));
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

// ── Deep Research ─────────────────────────────────────────────────────────────
// Long-running detached backend agent. `start` returns immediately with a
// job_id; `status` is polled (durable across disconnect). The finished report is
// delivered as a markdown artifact in the conversation. Auth (X-Munin-Email) is
// injected by the gateway, same as every other /api call.

export interface ResearchEventDTO { t: number; type: string; [k: string]: unknown }

export interface ResearchStatus {
  job_id: string;
  conversation_id?: string;
  question: string;
  status: 'queued' | 'running' | 'done' | 'error' | 'cancelled';
  events?: ResearchEventDTO[];
  artifact_id?: string | null;
  error?: string | null;
}

export async function startDeepResearch(
  conversationId: string | null,
  question: string,
): Promise<{ job_id: string; conversation_id: string; created_conversation: boolean; status: string }> {
  const res = await fetch(`${API}/research/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    // Omit conversation_id for a brand-new chat; the backend creates one and
    // returns it so we can switch to it.
    body: JSON.stringify(conversationId ? { conversation_id: conversationId, question } : { question }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err?.error?.message || `Failed to start research (${res.status})`);
  }
  return res.json();
}

export async function getResearchStatus(jobId: string): Promise<ResearchStatus> {
  const res = await fetch(`${API}/research/status/${encodeURIComponent(jobId)}`);
  if (!res.ok) throw new Error(`Failed to fetch research status (${res.status})`);
  return res.json();
}

// The conversation's most recent DR job (+ its event log), or {job: null}. Used
// on chat open to re-load and render the inline research view.
export async function fetchResearchForConversation(conversationId: string): Promise<{ job: ResearchStatus | null }> {
  const res = await fetch(`${API}/research/for-conversation/${encodeURIComponent(conversationId)}`);
  if (!res.ok) return { job: null };
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

// localStorage key for the active SSE stream (P1 #10 Phase 3; moved
// from sessionStorage for background turns so the pointer survives a
// closed tab, not just a refresh). Holds {stream_id, conversation_id,
// last_event_id}. It is only a navigation hint — the server's
// `active_stream` on GET /api/chats/{id} is the source of truth, so a
// stale entry costs one conversation load, nothing more. Ephemeral
// chats are deliberately not persisted: nothing exists in chat_store
// to restore, so the resume target would be meaningless.
const ACTIVE_STREAM_KEY = 'munin.active_stream';

export interface ActiveStreamPersist {
  stream_id: string;
  conversation_id: string;
  last_event_id?: string;
}

export function readActiveStream(): ActiveStreamPersist | null {
  try {
    const raw = localStorage.getItem(ACTIVE_STREAM_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed && parsed.stream_id && parsed.conversation_id) return parsed;
  } catch { /* fall through */ }
  return null;
}

function writeActiveStream(value: ActiveStreamPersist): void {
  try { localStorage.setItem(ACTIVE_STREAM_KEY, JSON.stringify(value)); } catch { /* ignore */ }
}

export function clearActiveStream(): void {
  try { localStorage.removeItem(ACTIVE_STREAM_KEY); } catch { /* ignore */ }
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
  // Union widened so the loop body can reassign from _attemptResume
  // (which adds 'gone' to the possible outcomes). Without the
  // annotation TS narrows to _consumeSSE's return type and the
  // 'gone' / 'error' branches below become unreachable from its POV.
  let outcome: 'done' | 'drop' | 'error' | 'gone' =
    await _consumeSSE(res, state, onEvent);

  // Reconnect loop (P1 #10). A clean `done` ends the loop. Anything
  // else with a known stream_id retries with backoff until the server
  // says 410 (stream gone) or we exhaust the schedule.
  let attempt = 0;
  // The 'error' branch returns inside the loop body, so by the
  // time we re-check the condition outcome can only be 'done' or
  // 'drop'. TS sees this and rejects the redundant `!== 'error'`
  // guard that used to live here.
  while (outcome !== 'done' && state.streamId) {
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
    if (outcome === 'error') {
      // The resume GET came back as a non-2xx, non-410 status (e.g.
      // 500 from a wedged retrieval, 502 through the tunnel). Without
      // this branch the while-loop's `outcome !== 'error'` guard
      // would exit the function silently and the user would see the
      // partial bubble freeze with no banner. Surface a terminal
      // error so the hook can flip into its interrupted-message path.
      clearActiveStream();
      onEvent({
        type: 'error',
        data: { message: 'Stream resume failed; please retry.' },
      });
      return;
    }
  }
}

// Resume an existing stream after a page reload or a conversation
// (re)open. Returns the same resolution states as streamChat. Unlike
// streamChat's mid-stream reconnect loop (where 'gone' is an error —
// the user is watching a bubble that just died), a 410 here emits the
// synthetic `stream_gone` event: the turn's outcome is already
// persisted server-side, so the right reaction is to reload the
// transcript, not to show a banner.
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
  // Same widening as streamChat: subsequent reassignments narrow
  // to the loop body's reachable cases otherwise.
  let outcome: 'done' | 'drop' | 'error' | 'gone' =
    await _attemptResume(state, onEvent, signal);
  if (outcome === 'gone') {
    clearActiveStream();
    onEvent({ type: 'stream_gone', data: {} });
    return;
  }
  if (outcome === 'error') {
    // First-shot resume on mount hit a non-2xx, non-410 status.
    // Mirror the streamChat fix so the user sees a banner instead of
    // a frozen partial bubble.
    clearActiveStream();
    onEvent({
      type: 'error',
      data: { message: 'Stream resume failed; please retry.' },
    });
    return;
  }
  // Same backoff schedule as streamChat — a refresh that lands while
  // the server is mid-shutdown can still recover.
  let attempt = 0;
  // The 'error' branch returns inside the loop body, so by the
  // time we re-check the condition outcome can only be 'done' or
  // 'drop'. TS sees this and rejects the redundant `!== 'error'`
  // guard that used to live here.
  while (outcome !== 'done' && state.streamId) {
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
      onEvent({ type: 'stream_gone', data: {} });
      return;
    }
    if (outcome === 'error') {
      clearActiveStream();
      onEvent({
        type: 'error',
        data: { message: 'Stream resume failed; please retry.' },
      });
      return;
    }
  }
}

// Explicitly cancel an in-flight chat completion (background turns).
// Stop must signal the server: closing the SSE connection alone no
// longer cancels anything — the turn would just keep running in the
// background. Best-effort: a failed cancel only means the turn runs
// to completion, which is safe.
export async function cancelChat(streamId: string): Promise<void> {
  try {
    await fetch(
      `${API}/chat/completions/${encodeURIComponent(streamId)}/cancel`,
      { method: 'POST' },
    );
  } catch { /* best-effort */ }
}

// ── Memory proposals (P2 #25) ────────────────────────────────────────────────

export async function acceptMemoryProposal(proposalId: string): Promise<void> {
  const res = await fetch(
    `${API}/memories/proposed/${encodeURIComponent(proposalId)}/accept`,
    { method: 'POST' },
  );
  if (!res.ok) throw new Error('Failed to accept memory proposal');
}

export async function rejectMemoryProposal(proposalId: string): Promise<void> {
  const res = await fetch(
    `${API}/memories/proposed/${encodeURIComponent(proposalId)}/reject`,
    { method: 'POST' },
  );
  if (!res.ok) throw new Error('Failed to reject memory proposal');
}

// ── Plan-mode approval (P2 #24 Phase 2) ──────────────────────────────────────

export async function approvePlan(
  conversationId: string,
  mode: 'each' | 'auto' = 'each',
): Promise<void> {
  const res = await fetch(
    `${API}/chats/${encodeURIComponent(conversationId)}/plan/approve`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode }),
    },
  );
  if (!res.ok) throw new Error(`Failed to approve plan (${res.status})`);
}

export async function rejectPlan(conversationId: string): Promise<void> {
  const res = await fetch(
    `${API}/chats/${encodeURIComponent(conversationId)}/plan/reject`,
    { method: 'POST' },
  );
  if (!res.ok) throw new Error(`Failed to reject plan (${res.status})`);
}

export async function editPlan(
  conversationId: string,
  items: Array<{ id?: string; title: string; status?: string; notes?: string | null }>,
  mode: 'each' | 'auto' = 'each',
): Promise<void> {
  const res = await fetch(
    `${API}/chats/${encodeURIComponent(conversationId)}/plan`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items, mode }),
    },
  );
  if (!res.ok) throw new Error(`Failed to edit plan (${res.status})`);
}
