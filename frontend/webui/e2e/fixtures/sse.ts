// Build a Server-Sent-Events body the chat UI's stream reader understands.
// Wire format (matches the backend + the existing useChat tests):
//   event: <name>\n data: <json>\n\n
// The frontend parses each into { type: <name>, data: <json> } and switches on
// type (see stores/chatStore.ts).

export interface SseEvent { event: string; data: unknown; id?: string }

export function buildSSE(events: SseEvent[]): string {
  return events
    .map(e => `${e.id ? `id: ${e.id}\n` : ''}event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`)
    .join('') + 'data: [DONE]\n\n';
}

// --- event constructors -----------------------------------------------------
let _seq = 0;
const nextId = (p: string) => `${p}-${++_seq}`;

export const ev = {
  conversation: (id: string, opts: Partial<{ title: string; is_new: boolean; stream_id: string }> = {}): SseEvent =>
    ({ event: 'conversation', data: { id, title: '', is_new: true, stream_id: 's1', ...opts } }),
  routing: (profile: string): SseEvent =>
    ({ event: 'routing', data: { profile, persona: profile } }),
  token: (content: string): SseEvent => ({ event: 'token', data: { content } }),
  thinking: (content: string): SseEvent => ({ event: 'thinking', data: { content } }),
  toolCall: (name: string, args: Record<string, unknown> = {}, id = nextId('tc')): SseEvent =>
    ({ event: 'tool_call', data: { id, name, arguments: args } }),
  toolResult: (id: string, result: unknown, duration_ms = 120): SseEvent =>
    ({ event: 'tool_result', data: { id, result, duration_ms } }),
  clarification: (payload: unknown): SseEvent => ({ event: 'clarification', data: payload }),
  artifactCreated: (a: {
    id: string; title: string; content_type: string; version?: number;
    language?: string; size_bytes?: number; source?: string; filename?: string;
    external_url?: string;
  }): SseEvent => ({ event: 'artifact_created', data: { version: 1, ...a } }),
  error: (message: string): SseEvent => ({ event: 'error', data: { message } }),
  done: (finish_reason = 'stop'): SseEvent => ({ event: 'done', data: { finish_reason } }),
};

// Convenience: tokenize a string into several token events so the UI renders a
// multi-chunk stream like a real completion.
export function tokens(text: string, chunks = 4): SseEvent[] {
  const size = Math.ceil(text.length / chunks);
  const out: SseEvent[] = [];
  for (let i = 0; i < text.length; i += size) out.push(ev.token(text.slice(i, i + size)));
  return out;
}
