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
  research: { steps: ReturnType<typeof S.researchProgression>; poll: number; reportArtifact: Record<string, unknown> | null };
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
    research: { steps: [], poll: 0, reportArtifact: null },
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
    if (/^\/api\/chats\/[^/]+\/artifacts$/.test(p)) return json(route, { artifacts: state.artifacts, total: state.artifacts.length });
    if (/^\/api\/chats\/[^/]+\/artifacts\/[^/]+$/.test(p)) {
      const id = p.split('/').pop();
      const a = state.artifacts.find((x) => x.id === id) || S.DR_REPORT_ARTIFACT;
      return json(route, { ...a, content: '# Report\n\nMocked artifact body.', version: 1, change_summary: '', created_by: 'assistant' });
    }
    if (/^\/api\/chats\/[^/]+$/.test(p)) return m === 'GET' ? json(route, S.emptyConversation(p.split('/').pop() || 'conv-e2e')) : json(route, {});

    // chat completions (SSE)
    if (p === '/api/chat/completions') return sse(route, state.chatScript);

    // deep research
    if (p === '/api/research/start') {
      state.research = { steps: S.researchProgression('dr-e2e', 'conv-dr'), poll: 0, reportArtifact: S.DR_REPORT_ARTIFACT };
      return json(route, { job_id: 'dr-e2e', conversation_id: 'conv-dr', created_conversation: true, status: 'queued' });
    }
    if (p.startsWith('/api/research/status/')) {
      const { steps } = state.research;
      const step = steps[Math.min(state.research.poll, steps.length - 1)];
      state.research.poll += 1;
      if (step?.status === 'done' && state.research.reportArtifact && !state.artifacts.find((a) => a.id === 'art-dr')) {
        state.artifacts.unshift(state.research.reportArtifact);
      }
      return json(route, step ?? { status: 'error', error: 'no steps' });
    }

    state.unmocked.push(`${m} ${p}`);
    return json(route, {});
  });

  return state;
}
