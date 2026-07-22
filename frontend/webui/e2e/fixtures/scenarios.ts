// Canned boot data (shapes copied from src/test/msw-handlers.ts so the app
// boots cleanly) plus reusable chat/research scripts.
import { ev, tokens, type SseEvent } from './sse';

export const ME = { email: 'e2e@test.local', name: 'E2E Tester', full_name: 'E2E Tester', nickname: 'e2e', avatar: '' };
export const PERSONAS = { personas: [{ id: 'munin', name: 'Munin', description: 'Munin assistant', icon_url: '', tags: [], capabilities: {}, prompt_suggestions: [] }], default_persona: 'munin' };
export const STATUS = { vllm: { status: 'running', model: 'test-model' }, services: { retrieval: 'ok' }, timestamp: new Date(0).toISOString() };
export const TAGS = { topics: [], groups: [], contributors: [], contributor_count: 0 };
export const ANNOUNCEMENT = { announcement: null };
export const USAGE = { current_month: { tokens_used: 0, tokens_limit: 1000000, tokens_remaining: 1000000, requests: 0, tools_used: {} }, api_keys: [] };
export const EMPTY_CHATS = { conversations: [], total: 0 };

export function emptyConversation(id: string) {
  return { id, title: 'Deep Research', persona: 'munin', created_at: new Date(0).toISOString(), updated_at: new Date(0).toISOString(), summary: null, messages: [] };
}

// A plain assistant reply.
export function simpleReply(text = 'Hello from Munin. This is a **test** reply.'): SseEvent[] {
  return [ev.conversation('conv-e2e'), ev.routing('chat'), ...tokens(text), ev.done()];
}

// A reply that runs a tool then answers (web_search style).
export function toolReply(): SseEvent[] {
  const id = 'tc-web';
  return [
    ev.conversation('conv-e2e'), ev.routing('research'),
    ev.toolCall('web_search', { query: 'protein folding' }, id),
    ev.toolResult(id, { results: [{ title: 'AlphaFold', url: 'https://example.org/af', snippet: 'folding' }] }),
    ...tokens('According to the sources, protein folding is well studied.'),
    ev.done(),
  ];
}

// A reply that produces an artifact (content_type configurable).
export function artifactReply(a: { id: string; title: string; content_type: string; external_url?: string; filename?: string }): SseEvent[] {
  return [
    ev.conversation('conv-e2e'), ev.routing('code'),
    ...tokens(`I created ${a.title}.`),
    ev.artifactCreated(a),
    ev.done(),
  ];
}

// A clarification turn (the model asks before working).
export function clarificationReply(): SseEvent[] {
  return [
    ev.conversation('conv-e2e'),
    ev.clarification({
      what_i_understood: 'You want a pong game.',
      // The card renders `q.text` and string options (see ClarificationCard).
      questions: [{ id: 'q1', text: 'Which style?', options: ['Single player', 'Two player'] }],
    }),
    ev.done('clarify'),
  ];
}

// An error mid-stream.
export function errorReply(): SseEvent[] {
  return [ev.conversation('conv-e2e'), ...tokens('starting'), ev.error('vLLM unavailable'), ev.done('error')];
}

// Deep Research status progression, returned across successive status polls.
export function researchProgression(jobId: string, conversationId: string) {
  const base = { job_id: jobId, question: 'What are CRISPR off-target risks?', conversation_id: conversationId, artifact_id: null as string | null, error: null as string | null };
  return [
    { ...base, status: 'running', progress: [{ t: 0.2, event: 'plan', sub_questions: ['a', 'b', 'c'] }] },
    { ...base, status: 'running', progress: [{ t: 1.1, event: 'sub_question_start', id: 'sq0', sub_question: 'DNA off-target types' }] },
    { ...base, status: 'running', progress: [{ t: 2.4, event: 'synthesising', n_resolved: 2 }] },
    { ...base, status: 'done', artifact_id: 'art-dr', progress: [{ t: 3.0, event: 'done', n_citations: 4 }] },
  ];
}

export const DR_REPORT_ARTIFACT = { id: 'art-dr', title: 'Research: CRISPR off-target risks', content_type: 'text/markdown', latest_version: 1, word_count: 400, byte_size: 2400, source: 'model_written', created_at: new Date(0).toISOString(), updated_at: new Date(0).toISOString() };
