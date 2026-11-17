/**
 * Regression tests: a self-correcting turn shows ONE result and ONE answer.
 *
 * Chat 13550817 (2026-11-13): the model showed PDB 6JFQ for AT1R, saw it was a
 * bacterial enzyme, looked up 4YAY and showed that. The chat displayed both
 * viewers, and one text holding the wrong claim, the retraction and the
 * answer (docs/agent-track/TURN-RESULTS-PLAN.md). The backend now marks the
 * replaced result (`superseded_by`, SSE `artifact_superseded`) and keeps the
 * text written before tool calls as `step_text`. These tests pin the client:
 * the replaced result never renders inline, the narration lives in the work
 * log, and the live bubble matches what a reload shows.
 * Backend side: backend/retrieval/tests/test_turn_results.py.
 */
import { act, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { http } from 'msw';
import { server } from './test/msw-server';
import { useChatStore, _resetChatStoreForTests } from './stores/chatStore';
import { InlineArtifacts } from './components/InlineArtifacts';
import { TaskLog } from './components/TaskLog';
import {
  artifactRefsFromToolCalls,
  supersededRefsFromToolCalls,
  WIDGET_CONTENT_TYPE,
} from './lib/artifactDisplay';
import type { ToolCall } from './lib/types';

vi.mock('3dmol/build/3Dmol-min.js?raw', () => ({ default: 'library' }));
vi.mock('./widgets/proteinRuntime.js?raw', () => ({ default: 'runtime' }));

function sseResponse(events: Array<{ event: string; data: unknown }>): Response {
  const body = events.map(e => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`).join('');
  return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } });
}

const STEP1 = "I'll show you the AT1R structure. The first crystal structure was solved in 2017 (PDB ID: 6JFQ).";
const STEP2 = 'That PDB ID was wrong: it is a different protein. Let me find the correct one.';
const ANSWER = 'The correct PDB entry is 4YAY, the first crystal structure of the human AT1R.';

const viewer = (id: string, pdb: string) => ({
  id, title: `${pdb}: structure`, content_type: WIDGET_CONTENT_TYPE, version: 1,
});

/** The AT1R turn as the server streams it. */
function at1rStream(narrationInLog: boolean) {
  return [
    { event: 'token', data: { content: STEP1 } },
    { event: 'tool_call', data: { id: 'c1', name: 'show_protein', arguments: { pdb_id: '6JFQ' } } },
    { event: 'tool_result', data: { id: 'c1', name: 'show_protein', result: viewer('art_6jfq', '6JFQ') } },
    { event: 'token', data: { content: STEP2 } },
    { event: 'tool_call', data: { id: 'c2', name: 'search', arguments: { query: 'AT1R PDB' } } },
    { event: 'tool_result', data: { id: 'c2', name: 'search', result: { results: [] } } },
    { event: 'tool_call', data: { id: 'c3', name: 'show_protein', arguments: { pdb_id: '4YAY', replaces: ['art_6jfq'] } } },
    { event: 'tool_result', data: { id: 'c3', name: 'show_protein', result: viewer('art_4yay', '4YAY') } },
    { event: 'artifact_superseded', data: { artifact_id: 'art_6jfq', superseded_by: 'art_4yay', tool_call_id: 'c1' } },
    { event: 'token', data: { content: ANSWER } },
    { event: 'done', data: { finish_reason: 'stop', terminal_reason: 'done', narration_in_log: narrationInLog } },
  ];
}

/** The same turn as it is stored (what a reload renders). */
const SAVED_AT1R: ToolCall[] = [
  { id: 'c1', name: 'show_protein', arguments: { pdb_id: '6JFQ' }, result: viewer('art_6jfq', '6JFQ'),
    step_text: STEP1, superseded_by: { art_6jfq: 'art_4yay' } },
  { id: 'c2', name: 'search', arguments: { query: 'AT1R PDB' }, result: { results: [] }, step_text: STEP2 },
  { id: 'c3', name: 'show_protein', arguments: { pdb_id: '4YAY', replaces: ['art_6jfq'] }, result: viewer('art_4yay', '4YAY') },
];

describe('self-correcting turn: one result, one answer', () => {
  beforeEach(() => _resetChatStoreForTests());

  it('the store keeps only the answer as the message and moves narration into the log', async () => {
    server.use(http.post('/api/chat/completions', () => sseResponse(at1rStream(true))));
    const { result } = renderHook(() => useChatStore());
    await act(async () => { await result.current.sendMessage('Can you show me At1R?', 'code'); });
    const msg = result.current.messages.find(m => m.role === 'assistant')!;
    expect(msg.content).toBe(ANSWER);
    expect(msg.content).not.toContain('6JFQ');
    expect(msg.tool_calls![0].step_text).toBe(STEP1);
    expect(msg.tool_calls![1].step_text).toBe(STEP2);
    expect(msg.tool_calls![2].step_text).toBeUndefined();
    expect(msg.tool_calls![0].superseded_by).toEqual({ art_6jfq: 'art_4yay' });
    // and therefore exactly one inline result: the corrected viewer
    expect(artifactRefsFromToolCalls(msg.tool_calls).map(r => r.id)).toEqual(['art_4yay']);
  });

  it('the live bubble drops the narration as soon as a tool call starts', async () => {
    const seen: string[] = [];
    const unsub = useChatStore.subscribe(s => {
      if (s.streaming.phase === 'tool_call') seen.push(s.streaming.content);
    });
    server.use(http.post('/api/chat/completions', () => sseResponse(at1rStream(true))));
    const { result } = renderHook(() => useChatStore());
    await act(async () => { await result.current.sendMessage('Can you show me At1R?', 'code'); });
    unsub();
    expect(seen.length).toBeGreaterThan(0);
    expect(seen.every(c => !c.includes('6JFQ'))).toBe(true);
  });

  it('when the server keeps all text in the message (thin answer, older server), so does the bubble', async () => {
    server.use(http.post('/api/chat/completions', () => sseResponse(at1rStream(false))));
    const { result } = renderHook(() => useChatStore());
    await act(async () => { await result.current.sendMessage('Can you show me At1R?', 'code'); });
    const msg = result.current.messages.find(m => m.role === 'assistant')!;
    expect(msg.content).toBe(STEP1 + STEP2 + ANSWER);
    expect(msg.tool_calls!.every(t => t.step_text === undefined)).toBe(true);
  });

  it('an interrupted turn keeps everything it streamed in the bubble', async () => {
    server.use(http.post('/api/chat/completions', () => sseResponse([
      ...at1rStream(true).slice(0, 4),
      { event: 'error', data: { message: 'boom' } },
    ])));
    const { result } = renderHook(() => useChatStore());
    await act(async () => { await result.current.sendMessage('Can you show me At1R?', 'code'); });
    const msg = result.current.messages.find(m => m.role === 'assistant')!;
    expect(msg.content).toContain(STEP1);
    expect(msg.content).toContain(STEP2);
  });
});

describe('replaced results never render inline', () => {
  it('artifactRefsFromToolCalls drops superseded results and supersededRefsFromToolCalls lists them', () => {
    expect(artifactRefsFromToolCalls(SAVED_AT1R).map(r => r.id)).toEqual(['art_4yay']);
    expect(supersededRefsFromToolCalls(SAVED_AT1R).map(r => r.id)).toEqual(['art_6jfq']);
  });

  it('InlineArtifacts shows one viewer for the AT1R turn', () => {
    render(<InlineArtifacts toolCalls={SAVED_AT1R} conversationId={null} />);
    expect(screen.getByTestId('inline-artifacts').children).toHaveLength(1);
  });

  it('a script rewritten under the same title shows once', () => {
    const card = (id: string, title: string) => ({ id, title, content_type: 'text/x-python', version: 1 });
    const calls: ToolCall[] = [
      { id: 'a', name: 'create_artifact', arguments: { title: 'unpacker.py' }, result: card('art_1', 'unpacker.py'),
        superseded_by: { art_1: 'art_2' } },
      { id: 'b', name: 'create_artifact', arguments: { title: 'unpacker.py' }, result: card('art_2', 'unpacker.py') },
    ];
    render(<InlineArtifacts toolCalls={calls} />);
    expect(screen.getAllByTestId('artifact-card')).toHaveLength(1);
  });

  it('old messages without superseded marks render every result, as before', () => {
    const calls: ToolCall[] = SAVED_AT1R.map(({ superseded_by: _s, ...rest }) => rest);
    expect(artifactRefsFromToolCalls(calls).map(r => r.id)).toEqual(['art_6jfq', 'art_4yay']);
  });
});

describe('the work log carries the narration and the earlier attempt', () => {
  it('shows each step text and marks the replaced viewer as an earlier attempt', () => {
    render(<TaskLog toolCalls={SAVED_AT1R} />);
    fireEvent.click(screen.getByTestId('activity-line'));
    const steps = screen.getAllByTestId('step-text').map(e => e.textContent);
    expect(steps).toEqual([STEP1, STEP2]);
    const notes = screen.getAllByTestId('superseded-note');
    expect(notes).toHaveLength(1);
    expect(notes[0].textContent).toContain('Earlier attempt (replaced): 6JFQ: structure');
  });

  it('is open while streaming, so narration that left the answer stays visible', () => {
    render(<TaskLog toolCalls={SAVED_AT1R.slice(0, 1)} isStreaming />);
    expect(screen.getByTestId('step-text').textContent).toBe(STEP1);
  });
});
