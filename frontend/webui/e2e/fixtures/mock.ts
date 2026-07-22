import type { Page, Route } from '@playwright/test';
import { buildSSE, type SseEvent } from './sse';
import * as S from './scenarios';

// Mutable state the test drives + the route handler reads. A test sets
// `state.chatScript` (what the next chat POST streams) and inspects
// `state.artifacts` / `state.unmocked`.
export interface MockState {
  chatScript: SseEvent[];
  conversations: Record<string, unknown>[];
  artifacts: Record<string, unknown>[];
  // Per-conversation artifacts. When set for a conversation id, the list +
  // single-artifact endpoints scope to it and 404 on a cross-conversation fetch
  // (reproduces the "artifact not found on switch" bug).
  perConversationArtifacts: Record<string, Record<string, unknown>[]>;
  research: { snapshots: ReturnType<typeof S.researchSnapshots>; poll: number; reportArtifact: Record<string, unknown> | null; question: string };
  unmocked: string[];
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
}
function sse(route: Route, events: SseEvent[]) {
  return route.fulfill({ status: 200, contentType: 'text/event-stream', body: buildSSE(events) });
}

/**
 * Intercept every /api + /auth + /shared request with one pathname-keyed
 * dispatcher (avoids glob-precedence and query-string pitfalls). Anything not
 * matched is logged to `state.unmocked` and returned as `{}` so no real network
 * escapes and boot never hangs.
 */
export async function installMocks(page: Page, init: Partial<MockState> = {}): Promise<MockState> {
  const state: MockState = {
    chatScript: init.chatScript ?? S.simpleReply(),
    conversations: init.conversations ?? [],
    artifacts: init.artifacts ?? [],
    perConversationArtifacts: init.perConversationArtifacts ?? {},
    research: { snapshots: [], poll: 0, reportArtifact: null, question: '' },
    unmocked: [],
  };

  // The app refetches the artifact LIST whenever the conversation id changes,
  // which would overwrite anything an `artifact_created` SSE just added. So the
  // mocked list must include artifacts the chat script creates - derive them.
  for (const e of state.chatScript) {
    if (e.event !== 'artifact_created') continue;
    const d = e.data as Record<string, unknown>;
    if (state.artifacts.find((a) => a.id === d.id)) continue;
    state.artifacts.push({
      id: d.id, title: d.title, content_type: d.content_type,
      latest_version: (d.version as number) || 1, word_count: 0,
      byte_size: (d.size_bytes as number) || 0, source: d.source || 'model_written',
      filename: d.filename, external_url: d.external_url,
      created_at: new Date(0).toISOString(), updated_at: new Date(0).toISOString(),
    });
  }

  await page.route(/\/(api|auth|shared)\//, (route) => {
    const req = route.request();
    const m = req.method();
    const p = new URL(req.url()).pathname;

    if (p.startsWith('/shared/')) return route.fulfill({ status: 200, contentType: 'application/javascript', body: '' });
    if (p.endsWith('/auth/me')) return json(route, S.ME);

    // static boot GETs
    if (p === '/api/status') return json(route, S.STATUS);
    if (p === '/api/personas') return json(route, S.PERSONAS);
    if (p === '/api/tags') return json(route, S.TAGS);
    if (p === '/api/announcement') return json(route, S.ANNOUNCEMENT);
    if (p === '/api/usage/me') return json(route, S.USAGE);
    if (p === '/api/projects') return json(route, { projects: [], total: 0 });

    // conversations
    if (p === '/api/chats') return m === 'GET'
      ? json(route, { conversations: state.conversations, total: state.conversations.length })
      : json(route, {});
    const artifactsListMatch = p.match(/^\/api\/chats\/([^/]+)\/artifacts$/);
    if (artifactsListMatch) {
      const cid = artifactsListMatch[1];
      const list = state.perConversationArtifacts[cid] ?? state.artifacts;
      return json(route, { artifacts: list, total: list.length });
    }
    const artifactOneMatch = p.match(/^\/api\/chats\/([^/]+)\/artifacts\/([^/]+)$/);
    if (artifactOneMatch) {
      const [, cid, id] = artifactOneMatch;
      const scoped = state.perConversationArtifacts[cid];
      // Cross-conversation fetch (the bug): the artifact isn't in this
      // conversation -> 404, exactly like the backend.
      if (scoped && !scoped.find((x) => x.id === id)) {
        return json(route, { error: { message: 'artifact not found' } }, 404);
      }
      const a = (scoped ?? state.artifacts).find((x) => x.id === id) || S.DR_REPORT_ARTIFACT;
      return json(route, { ...a, content: '# Report\n\nMocked artifact body.', version: 1, change_summary: '', created_by: 'assistant' });
    }
    if (/^\/api\/chats\/[^/]+$/.test(p) && m === 'GET') {
      const cid = p.split('/').pop() || 'conv-e2e';
      // The DR conversation carries the research question as a user message (the
      // backend persists it), so a fresh DR chat is not empty.
      if (cid === 'conv-dr' && state.research.question) {
        const c = S.emptyConversation(cid);
        c.messages = [{ id: 'm-dr', role: 'user', content: state.research.question, created_at: new Date(0).toISOString() }];
        return json(route, c);
      }
      return json(route, S.emptyConversation(cid));
    }
    if (/^\/api\/chats\/[^/]+$/.test(p)) return json(route, {});

    // chat completions (SSE)
    if (p === '/api/chat/completions') return sse(route, state.chatScript);

    // deep research
    if (p === '/api/research/start') {
      const posted = (req.postDataJSON?.() ?? {}) as { question?: string };
      const q = posted.question ?? '';
      state.research = { snapshots: S.researchSnapshots('dr-e2e', 'conv-dr', q), poll: 0, reportArtifact: S.DR_REPORT_ARTIFACT, question: q };
      return json(route, { job_id: 'dr-e2e', conversation_id: 'conv-dr', created_conversation: true, status: 'queued' });
    }
    if (p.startsWith('/api/research/status/')) {
      const { snapshots } = state.research;
      const snap = snapshots[Math.min(state.research.poll, snapshots.length - 1)];
      state.research.poll += 1;
      if (snap?.status === 'done' && state.research.reportArtifact && !state.artifacts.find((a) => a.id === 'art-dr')) {
        state.artifacts.unshift(state.research.reportArtifact);
      }
      return json(route, snap ?? { status: 'error', error: 'no snapshots' });
    }
    if (p.startsWith('/api/research/for-conversation/')) {
      const { snapshots } = state.research;
      const snap = snapshots.length ? snapshots[Math.min(state.research.poll, snapshots.length - 1)] : null;
      return json(route, { job: snap });
    }

    state.unmocked.push(`${m} ${p}`);
    return json(route, {});
  });

  return state;
}
