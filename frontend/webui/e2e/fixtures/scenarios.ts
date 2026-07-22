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

export function emptyConversation(id: string): { id: string; title: string; persona: string; created_at: string; updated_at: string; summary: null; messages: Array<{ id: string; role: string; content: string; created_at: string }> } {
  return { id, title: 'Deep Research', persona: 'munin', created_at: new Date(0).toISOString(), updated_at: new Date(0).toISOString(), summary: null, messages: [] };
}

// A conversation row for the sidebar list.
export function conversationSummary(id: string, title: string) {
  return { id, title, persona: 'chat', created_at: new Date(0).toISOString(), updated_at: new Date(0).toISOString(), message_count: 1, preview: title, pinned: false, pinned_at: null };
}

export function artifactSummary(id: string, title: string, content_type = 'text/markdown') {
  return { id, title, content_type, latest_version: 1, word_count: 10, byte_size: 100, source: 'model_written', created_at: new Date(0).toISOString(), updated_at: new Date(0).toISOString() };
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

// Deep Research event-log snapshots, returned across successive status polls.
// Each snapshot is a full job with the CUMULATIVE render-ready event log (plan,
// tool cards, notes, artifact), matching the backend research_store shape.
export function researchSnapshots(jobId: string, conversationId: string, question = 'What are CRISPR off-target risks?') {
  const base = { job_id: jobId, conversation_id: conversationId, question, error: null as string | null };
  const log: Array<Record<string, unknown>> = [];
  const step = (status: string, artifact_id: string | null, ...evs: Array<Record<string, unknown>>) => {
    log.push(...evs);
    return { ...base, status, artifact_id, events: log.map((e) => ({ ...e })) };
  };
  return [
    step('running', null, { t: 0.2, type: 'plan', items: [{ id: 'sq0', text: 'Sub-question one', status: 'open' }, { id: 'sq1', text: 'Sub-question two', status: 'open' }] }),
    step('running', null,
      { t: 0.5, type: 'plan_update', id: 'sq0', status: 'in_progress' },
      { t: 0.6, type: 'tool_call', id: 'tc1', name: 'search', arguments: { query: 'sub-question one' } },
      { t: 2.0, type: 'tool_result', id: 'tc1', summary: '8 candidates, 3 kept to read' }),
    step('running', null,
      { t: 2.1, type: 'tool_call', id: 'tc2', name: 'source', arguments: { doi: '10.1/x', title: 'A key paper' } },
      { t: 5.0, type: 'tool_result', id: 'tc2', summary: 'A key paper - resolved (full_text)', outcome: 'resolved', read_depth: 'full_text' },
      { t: 5.1, type: 'note', claim: 'Lipid composition influences binding', ref: { title: 'A key paper', doi: '10.1/x' } },
      { t: 5.2, type: 'plan_update', id: 'sq0', status: 'resolved' }),
    step('done', 'art-dr',
      { t: 6.0, type: 'synthesising', n_resolved: 1 },
      { t: 6.5, type: 'artifact', artifact_id: 'art-dr', title: 'Research: ' + question.slice(0, 40), n_resolved: 1, n_citations: 1 },
      { t: 6.6, type: 'done', artifact_id: 'art-dr' }),
  ];
}

export const DR_REPORT_ARTIFACT = { id: 'art-dr', title: 'Research: CRISPR off-target risks', content_type: 'text/markdown', latest_version: 1, word_count: 400, byte_size: 2400, source: 'model_written', created_at: new Date(0).toISOString(), updated_at: new Date(0).toISOString() };
