/**
 * Chat store (P2 #26 Migration commit 1).
 *
 * Module-scoped Zustand store that owns every reactive slice the
 * chat domain needs: messages, streaming state, conversation
 * identity, errors, and artifact summaries. Replaces the seven
 * useState calls + two useRef cells previously held in
 * `hooks/useChat.ts`.
 *
 * The store EXPORT is `useChatStore`. Consumers (App.tsx, the
 * useChat.test.ts + useChat.error.test.ts suites) subscribe to
 * individual slices via the selector form
 * `useChatStore(s => s.X)`. Per-field selectors mean a token-
 * stream update (state.streaming.content += chunk) only re-renders
 * components subscribed to that exact slice, not every component
 * that destructured the whole tuple.
 *
 * The transitional `useChat()` wrapper that existed through
 * commits 1-3 of this migration is gone. Mount-time lifecycle
 * effects (the P1 #10 SSE resume) live in `hooks/useChatLifecycle.ts`
 * because Zustand stores can't host React effects.
 *
 * Why module-scoped over per-instance: components in distant
 * subtrees (PlanCard inside MessageBubble; ChatInput at the
 * footer; ArtifactPanel in a side drawer) all need to coordinate
 * on the same chat state. With React's per-instance useState
 * that required prop-drilling through every intermediate
 * component. A shared store is the explicit solution.
 *
 * Test isolation: tests that mutate the store must call
 * `_resetChatStoreForTests()` in their setup (one line per file
 * — see useChat.test.ts beforeEach). Without it, state from
 * test N would leak into test N+1.
 */

import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';

import {
  fetchChat,
  resumeChat,
  streamChat,
} from '../lib/api';
import type {
  AgentState,
  ArtifactCreatedEvent,
  ArtifactSummary,
  ArtifactUpdatedEvent,
  Clarification,
  CompactBoundary,
  Conversation,
  MemoryProposal,
  Message,
  MessageContent,
  Plan,
  RagContext,
  SSEEvent,
  TagChip,
  ToolCall,
} from '../lib/types';


// ── Streaming state shape ──────────────────────────────────────────

interface RetryingState {
  attempt: number;
  maxAttempts: number;
  reason: string;
}

interface ReconnectingState {
  attempt: number;
  maxAttempts: number;
}

export interface StreamingState {
  content: string;
  thinking: string;
  toolCalls: ToolCall[];
  ragContext: RagContext | null;
  clarification: Clarification | null;
  // Profile the per-turn router selected for the in-flight stream
  // ("chat" / "code" / "research"). Set on the `routing` SSE event,
  // reset to null when a new turn starts. Drives the routed-profile
  // chip on the streaming assistant bubble.
  routedProfile: string | null;
  phase: 'idle' | 'thinking' | 'tool_call' | 'generating' | 'done' | 'error';
  // Set when the backend emits a `retrying` SSE because a vLLM call
  // hit a transient error (5xx / 429 / pre-first-byte drop). Cleared
  // as soon as any other event arrives (the call succeeded) or the
  // stream finishes.
  retrying: RetryingState | null;
  // Set by the SSE consumer when the connection drops and a
  // Last-Event-ID resume is being attempted (P1 #10). Cleared once
  // any event lands on the resumed connection.
  reconnecting: ReconnectingState | null;
}

export const INITIAL_STREAMING: StreamingState = {
  content: '',
  thinking: '',
  toolCalls: [],
  ragContext: null,
  clarification: null,
  routedProfile: null,
  phase: 'idle',
  retrying: null,
  reconnecting: null,
};


/**
 * Target rate (characters per second) at which the final assistant
 * answer is revealed in the UI.
 *
 * The local vLLM emits at 100+ tokens/sec, and it often packs several
 * tokens into one stream chunk, so flushing whole chunks dumps the
 * reply in a burst that's hard to read. The pacer in `sendMessage`
 * buffers incoming text and reveals it at a steady CHARACTER rate
 * (independent of vLLM's chunk sizes) so the user sees an even
 * typewriter while the network stream continues at full speed (the GPU
 * slot is released as soon as vLLM finishes, regardless of what the
 * user has seen). When the model generates slower than this rate the
 * pacer never throttles — it is a ceiling, not a forced speed.
 *
 * ~260 cps (~65 tok/s) reads as slightly-but-noticeably faster than
 * Claude. Set to `Infinity` in tests via `_resetChatStoreForTests` so
 * synchronous assertions on `streaming.content` see the full text
 * immediately. Only affects the 'token' event (final user-facing
 * content); 'thinking', tool events, and nested-agent streams are
 * unaffected because they have their own event types and are never
 * rate-limited.
 */
export const DEFAULT_CHARS_PER_SEC = 260;

// Repaint cadence of the pacer. Fixed at 60 Hz for smoothness; each
// tick reveals DEFAULT_CHARS_PER_SEC / PACER_REPAINT_HZ characters
// (with a fractional carry so the exact rate is hit).
const PACER_REPAINT_HZ = 60;


// ── Store shape ────────────────────────────────────────────────────

interface ChatState {
  // Reactive slices
  messages: Message[];
  conversationId: string | null;
  conversationPersona: string | null;
  streaming: StreamingState;
  error: string | null;
  artifacts: ArtifactSummary[];
  lastArtifactEvent: ArtifactCreatedEvent | ArtifactUpdatedEvent | null;
  /**
   * Per-store throttle target (characters/sec) for revealing the
   * final answer. See `DEFAULT_CHARS_PER_SEC`. Tests set this to
   * `Infinity` to bypass the pacer and keep synchronous assertions
   * working.
   */
  charsPerSec: number;

  // Actions
  setArtifacts: (next: ArtifactSummary[]) => void;
  loadConversation: (id: string) => Promise<Conversation | null>;
  clearConversation: () => void;
  sendMessage: (
    content: string,
    persona: string,
    ephemeral?: boolean,
    multimodalContent?: MessageContent,
    projectId?: string,
    tags?: TagChip[],
    // P1 #10 Phase 3: when set, skip the optimistic user message +
    // POST and resume an existing stream via Last-Event-ID. The
    // content/persona/etc args are ignored on the resume path; the
    // entire event handler downstream is identical.
    resumeOpts?: { streamId: string; lastEventId?: string },
  ) => Promise<void>;
  stopGenerating: () => void;
  dismissMemoryProposal: (proposalId: string) => void;
}


// Module-level abort handle. Not part of reactive state (it's a
// mutable resource, not data), but tied to the store's lifetime
// (which equals the app's lifetime since the store is module-
// scoped). Each new sendMessage replaces it; stopGenerating aborts
// via this handle.
let _abortController: AbortController | null = null;


/**
 * Deep-clone the turn-scoped toolCalls accumulator before handing
 * it to immer.
 *
 * Why: immer auto-freezes every value that lands in store state.
 * The handleEvent function holds a LOCAL `toolCalls: ToolCall[]`
 * accumulator and mutates it in place (`tc.result = ...` on
 * tool_result, `activeAgent.thinking += content` on agent_thinking).
 * If we passed the live references into immer via
 * `state.streaming.toolCalls = toolCalls`, immer would freeze our
 * accumulator and subsequent in-place mutations would silently
 * fail. The fix is to snapshot the array AND its nested `agent`
 * sub-tree before each set() so immer's copy is independent of
 * the live accumulator.
 *
 * Identified via useChat.test.ts "populates streaming.toolCalls
 * from tool_call and tool_result events" — the test sequence was
 * tool_call → tool_result → token → done, and `tool_result` set
 * tc.result on a frozen reference so the final assistant message
 * had `tool_calls[0].result === undefined`.
 */
function _snapshotToolCalls(tcs: ToolCall[]): ToolCall[] {
  return tcs.map(t => ({
    ...t,
    agent: t.agent
      ? {
          ...t.agent,
          toolCalls: t.agent.toolCalls.map(a => ({ ...a })),
        }
      : t.agent,
  }));
}


// ── Store implementation ───────────────────────────────────────────

export const useChatStore = create<ChatState>()(
  immer((set, get) => ({
    messages: [],
    conversationId: null,
    conversationPersona: null,
    streaming: INITIAL_STREAMING,
    error: null,
    artifacts: [],
    lastArtifactEvent: null,
    charsPerSec: DEFAULT_CHARS_PER_SEC,

    setArtifacts: (next) =>
      set(state => {
        state.artifacts = next;
      }),

    loadConversation: async (id: string) => {
      set(state => {
        state.error = null;
      });
      try {
        const chat = await fetchChat(id);
        // P2 #24 Phase 1: backend embeds the current plan under
        // `chat.plan` (null if no plan). Attach the snapshot to
        // the most recent assistant message whose tool_calls
        // touched the plan, so the inline PlanCard re-renders
        // above the right bubble. Fallback to the last assistant
        // message if no tool_calls match (defensive: shouldn't
        // happen if a plan row exists, but keeps the UX robust).
        const messagesIn = chat.messages as Message[];
        const planRow = (chat as { plan?: Plan | null }).plan ?? null;
        if (planRow) {
          const PLAN_TOOL_NAMES = new Set(['set_plan', 'update_plan_item']);
          let attachedAt = -1;
          for (let i = messagesIn.length - 1; i >= 0; i--) {
            const m = messagesIn[i];
            if (m.role !== 'assistant' || !m.tool_calls) continue;
            if (m.tool_calls.some(tc => PLAN_TOOL_NAMES.has(tc.name))) {
              messagesIn[i] = { ...m, plan_snapshot: planRow };
              attachedAt = i;
              break;
            }
          }
          if (attachedAt === -1) {
            for (let i = messagesIn.length - 1; i >= 0; i--) {
              if (messagesIn[i].role === 'assistant') {
                messagesIn[i] = { ...messagesIn[i], plan_snapshot: planRow };
                break;
              }
            }
          }
        }
        set(state => {
          state.messages = messagesIn;
          state.conversationId = chat.id;
          state.conversationPersona = chat.persona || null;
        });
        return chat;
      } catch (e) {
        set(state => {
          state.error = e instanceof Error ? e.message : 'Failed to load conversation';
        });
        return null;
      }
    },

    clearConversation: () =>
      set(state => {
        state.messages = [];
        state.conversationId = null;
        state.conversationPersona = null;
        state.streaming = INITIAL_STREAMING;
        state.error = null;
        state.artifacts = [];
        state.lastArtifactEvent = null;
      }),

    sendMessage: async (
      content,
      persona,
      ephemeral,
      multimodalContent,
      projectId,
      tags,
      resumeOpts,
    ) => {
      set(state => {
        state.error = null;
      });

      if (!resumeOpts) {
        const userMessage: Message = {
          id: `temp-${Date.now()}`,
          role: 'user',
          content,
          // Persona this turn is addressed to ("munin"). The per-turn
          // router decides the actual profile, which lands on the
          // assistant message via the `routing` SSE event.
          persona,
          created_at: new Date().toISOString(),
        };
        set(state => {
          state.messages.push(userMessage);
        });
      }
      set(state => {
        state.streaming = { ...INITIAL_STREAMING, phase: 'thinking' };
      });

      const abort = new AbortController();
      _abortController = abort;

      // Turn-scoped accumulators. These are local to this call so
      // a re-entrant sendMessage (impossible in practice but
      // defensive) wouldn't cross-contaminate.
      const toolCalls: ToolCall[] = [];
      // Per-turn router decision. Latched on the `routing` SSE event and
      // attached to the assistant message as its `persona` (routed
      // profile) so the chip renders identically live and after reload.
      let routedProfile: string | null = null;
      // P2 #25: memory proposals fire from a `stop` hook AFTER the
      // `done` event in most paths, so we collect them in a turn-
      // scoped accumulator and attach to the most recent assistant
      // message when each one lands.
      const memoryProposals: MemoryProposal[] = [];
      // P2 #22: compact_boundary fires once per turn (or not at
      // all) when the backend used a summary. Latched on the
      // assistant bubble so a transcript reload still shows the
      // divider.
      let compactBoundary: CompactBoundary | null = null;
      // P2 #24 Phase 1: plan_updated fires zero or more times per
      // turn (once per set_plan or update_plan_item call). We
      // latch the LATEST plan and attach it to the assistant
      // message that closes the turn — that's the bubble the
      // PlanCard renders above.
      let planSnapshot: Plan | null = null;
      let thinkingText = '';
      let contentText = '';
      let ragCtx: RagContext | null = null;
      let activeAgent: AgentState | null = null;
      let clarification: Clarification | null = null;

      // Answer pacer (DEFAULT_CHARS_PER_SEC). Buffers 'token' text and
      // reveals it at a steady CHARACTER rate so the typewriter is even
      // regardless of how many tokens vLLM packs per stream chunk. The
      // network read and `contentText` accumulator are NOT paced — vLLM
      // finishes at full speed and the GPU slot is released as soon as
      // `done` arrives, even if the user is still watching characters
      // land. When the pacer is still draining at the time `done`
      // arrives, the assistant Message push is deferred until the
      // buffer is empty so the bubble doesn't snap to the full text
      // mid-typewriter.
      //
      // `sendMessage` awaits `pacerDrained` before returning so the
      // post-drain processDoneInline (or an explicit stop) is the last
      // action — without this the finally block would clearTimeout
      // before the deferred 'done' got a chance to run.
      const charsPerSec = get().charsPerSec;
      const pacerEnabled = Number.isFinite(charsPerSec) && charsPerSec > 0;
      const pacerTickMs = pacerEnabled ? 1000 / PACER_REPAINT_HZ : 0;
      // Characters revealed per repaint tick; fractional, with a carry.
      const charsPerTick = pacerEnabled ? charsPerSec / PACER_REPAINT_HZ : 0;
      // Unrevealed buffered text + fractional-character carry.
      let pendingText = '';
      let charCarry = 0;
      let displayedContent = '';
      let pacerTimer: ReturnType<typeof setTimeout> | null = null;
      let nextEmitAt = 0;
      let pendingDoneEvent: SSEEvent | null = null;
      let pacerStopped = false;
      let pacerDrainedResolve: (() => void) | null = null;
      const pacerDrained: Promise<void> = pacerEnabled
        ? new Promise(resolve => {
            pacerDrainedResolve = resolve;
          })
        : Promise.resolve();
      const resolvePacerDrained = (): void => {
        if (pacerDrainedResolve !== null) {
          const r = pacerDrainedResolve;
          pacerDrainedResolve = null;
          r();
        }
      };

      const stopPacer = (): void => {
        pacerStopped = true;
        if (pacerTimer !== null) {
          clearTimeout(pacerTimer);
          pacerTimer = null;
        }
        resolvePacerDrained();
      };

      const processDoneInline = (event: SSEEvent): void => {
        if (event.type !== 'done') return;
        const assistantMessage: Message = {
          id: `msg-${Date.now()}`,
          role: 'assistant',
          content: contentText,
          thinking: thinkingText || null,
          tool_calls:
            toolCalls.length > 0 ? _snapshotToolCalls(toolCalls) : null,
          rag_context: ragCtx,
          clarification: clarification,
          memory_proposals:
            memoryProposals.length > 0 ? [...memoryProposals] : null,
          compact_boundary: compactBoundary,
          plan_snapshot: planSnapshot,
          // Routed profile for this turn ("chat" / "code" / "research"),
          // as decided by the per-turn router. Stored on `persona` so the
          // routed-profile chip renders identically live and after reload.
          // Falls back to the persona the turn was sent under.
          persona: routedProfile ?? persona,
          // Budget-cap turns finish with terminal_reason "max_turns" even
          // though finish_reason is "stop"; flag the bubble so the UI can
          // offer a Continue button instead of leaving the user to guess
          // whether the model stopped on purpose (rpt_20260702).
          paused_at_budget: event.data.terminal_reason === 'max_turns',
          created_at: new Date().toISOString(),
        };
        set(state => {
          state.messages.push(assistantMessage);
          state.streaming = INITIAL_STREAMING;
        });
      };

      const drainPacer = (): void => {
        pacerTimer = null;
        if (pacerStopped) {
          resolvePacerDrained();
          return;
        }
        if (pendingText.length > 0) {
          charCarry += charsPerTick;
          const n = Math.min(Math.floor(charCarry), pendingText.length);
          if (n > 0) {
            displayedContent += pendingText.slice(0, n);
            pendingText = pendingText.slice(n);
            charCarry -= n;
            set(state => {
              state.streaming.content = displayedContent;
              state.streaming.phase = 'generating';
            });
          }
        }
        if (pendingText.length > 0) {
          schedulePacer();
          return;
        }
        // Buffer drained: drop the fractional carry so a later pause
        // can't bank a burst of characters when text resumes.
        charCarry = 0;
        if (pendingDoneEvent !== null) {
          const evt = pendingDoneEvent;
          pendingDoneEvent = null;
          processDoneInline(evt);
          resolvePacerDrained();
          return;
        }
        // Queue empty, no done pending. The pacer naturally pauses
        // and the 'token' case will re-schedule when more arrive.
        // We do NOT resolve pacerDrained here — sendMessage's finally
        // is the gatekeeper and resolves it via stopPacer if nothing
        // more arrives.
      };

      const schedulePacer = (): void => {
        if (pacerTimer !== null || pacerStopped) return;
        const now =
          typeof performance !== 'undefined' ? performance.now() : Date.now();
        const delay = Math.max(0, nextEmitAt - now);
        nextEmitAt = now + delay + pacerTickMs;
        pacerTimer = setTimeout(drainPacer, delay);
      };

      const handleEvent = (event: SSEEvent) => {
        // Any event other than `retrying` itself means the backend
        // has resumed forward progress — clear the indicator.
        if (event.type !== 'retrying') {
          set(state => {
            if (state.streaming.retrying) state.streaming.retrying = null;
          });
        }
        // Same pattern for the SSE-level reconnect indicator
        // (P1 #10): any other event means a fresh connection is
        // delivering data.
        if (event.type !== 'reconnecting') {
          set(state => {
            if (state.streaming.reconnecting) state.streaming.reconnecting = null;
          });
        }
        switch (event.type) {
          case 'conversation':
            // May fire twice: first with null title, second with
            // generated title. Skip for ephemeral chats — don't
            // track the synthetic ID.
            if (!ephemeral) {
              set(state => {
                if (!state.conversationId) state.conversationId = event.data.id;
              });
            }
            break;
          case 'rag_context':
            ragCtx = event.data;
            set(state => {
              state.streaming.ragContext = event.data;
            });
            break;
          case 'thinking':
            thinkingText += event.data.content;
            set(state => {
              state.streaming.thinking = thinkingText;
              state.streaming.phase = 'thinking';
            });
            break;
          case 'token':
            contentText += event.data.content;
            if (pacerEnabled) {
              pendingText += event.data.content;
              schedulePacer();
            } else {
              displayedContent = contentText;
              set(state => {
                state.streaming.content = contentText;
                state.streaming.phase = 'generating';
              });
            }
            break;
          case 'tool_call':
            toolCalls.push({
              id: event.data.id,
              name: event.data.name,
              arguments: event.data.arguments,
            });
            set(state => {
              state.streaming.toolCalls = _snapshotToolCalls(toolCalls);
              state.streaming.phase = 'tool_call';
            });
            break;
          case 'tool_result': {
            const tc = toolCalls.find(t => t.id === event.data.id);
            if (tc) {
              tc.result = event.data.result;
              tc.duration_ms = event.data.duration_ms;
            }
            set(state => {
              state.streaming.toolCalls = _snapshotToolCalls(toolCalls);
            });
            break;
          }
          case 'clarification':
            // Discard any in-progress prose — the backend retried
            // with forced tool_choice after detecting a prose
            // clarification, so the streamed tokens are stale.
            // Same logic applies to anything sitting in the pacer
            // queue: drop it, since it's stale prose the user
            // shouldn't see.
            contentText = '';
            displayedContent = '';
            pendingText = '';
            charCarry = 0;
            clarification = event.data;
            set(state => {
              state.streaming.content = '';
              state.streaming.clarification = event.data;
            });
            break;
          case 'artifact_created':
            set(state => {
              const exists = state.artifacts.find(a => a.id === event.data.id);
              if (exists) return;
              state.artifacts.unshift({
                id: event.data.id,
                title: event.data.title,
                content_type: event.data.content_type,
                language: event.data.language,
                latest_version: event.data.version,
                word_count: 0,
                byte_size: event.data.size_bytes || 0,
                source: event.data.source,
                filename: event.data.filename,
                size_bytes: event.data.size_bytes,
                external_url: event.data.external_url,
                created_at: new Date().toISOString(),
                updated_at: new Date().toISOString(),
              });
              state.lastArtifactEvent = event.data;
            });
            break;
          case 'artifact_updated':
            set(state => {
              for (const a of state.artifacts) {
                if (a.id === event.data.id) {
                  a.title = event.data.title;
                  a.latest_version = event.data.version;
                  a.updated_at = new Date().toISOString();
                }
              }
              state.lastArtifactEvent = event.data;
            });
            break;
          case 'agent_start': {
            activeAgent = {
              name: event.data.agent,
              query: event.data.query,
              thinking: '',
              toolCalls: [],
            };
            // Attach to the last invoke_agent tool call.
            const parentTc = [...toolCalls].reverse().find(t => t.name === 'invoke_agent');
            if (parentTc) parentTc.agent = activeAgent;
            set(state => {
              state.streaming.toolCalls = _snapshotToolCalls(toolCalls);
            });
            break;
          }
          case 'agent_thinking':
            if (activeAgent) {
              activeAgent.thinking += event.data.content;
              set(state => {
                state.streaming.toolCalls = _snapshotToolCalls(toolCalls);
              });
            }
            break;
          case 'agent_tool_call':
            if (activeAgent) {
              activeAgent.toolCalls.push({
                id: event.data.id,
                name: event.data.name,
                arguments: event.data.arguments,
              });
              set(state => {
                state.streaming.toolCalls = _snapshotToolCalls(toolCalls);
              });
            }
            break;
          case 'agent_tool_result':
            if (activeAgent) {
              const atc = activeAgent.toolCalls.find(t => t.id === event.data.id);
              if (atc) {
                atc.result = event.data.result;
                atc.duration_ms = event.data.duration_ms;
              }
              set(state => {
                state.streaming.toolCalls = _snapshotToolCalls(toolCalls);
              });
            }
            break;
          case 'agent_done':
            if (activeAgent) {
              activeAgent.stoppedReason = event.data.stopped_reason;
              activeAgent.durationSeconds = event.data.duration_seconds;
              activeAgent = null;
              set(state => {
                state.streaming.toolCalls = _snapshotToolCalls(toolCalls);
              });
            }
            break;
          case 'routing':
            // Per-turn router picked an internal profile
            // ("chat" / "code" / "research") for this turn. Latch it
            // so the assistant message carries it as its routed
            // profile, and expose it on the streaming state to drive
            // the in-progress routed-profile chip.
            routedProfile = event.data.profile;
            set(state => {
              state.streaming.routedProfile = event.data.profile;
            });
            break;
          case 'retrying':
            // A vLLM call hit a transient error and is about to
            // retry. Show a "reconnecting" indicator until any
            // other event arrives.
            set(state => {
              state.streaming.retrying = {
                attempt: event.data.attempt,
                maxAttempts: event.data.max_attempts,
                reason: event.data.reason,
              };
            });
            break;
          case 'reconnecting':
            // SSE connection to the server dropped; the client is
            // resuming via Last-Event-ID. The in-flight turn
            // keeps running server-side during the grace window
            // (P1 #10).
            set(state => {
              state.streaming.reconnecting = {
                attempt: event.data.attempt,
                maxAttempts: event.data.max_attempts,
              };
            });
            break;
          case 'plan_updated':
            // P2 #24 Phase 1: backend confirmed a set_plan or
            // update_plan_item call landed. Latch the freshest
            // plan; the closing `done` / `error` branch attaches
            // the snapshot to the assistant message.
            planSnapshot = event.data;
            // ensure render — immer turns this into a no-op if
            // nothing else changed, which is fine: planSnapshot
            // is a turn-scoped variable, the visible store state
            // hasn't changed yet.
            set(state => {
              state.streaming = { ...state.streaming };
            });
            break;
          case 'compact_boundary':
            // P2 #22: backend used (or just generated) a summary
            // for the earlier conversation. The divider is
            // rendered above the assistant message that this turn
            // produces, so we latch the boundary into a turn-
            // scoped variable and attach it on `done`.
            compactBoundary = event.data;
            set(state => {
              state.streaming = { ...state.streaming };
            });
            break;
          case 'memory_proposed': {
            // P2 #25: stop-hook produced an auto-extracted memory
            // candidate. The hook fires AFTER 'done' in the
            // typical path, so the assistant message is already
            // persisted — we attach the proposal retroactively to
            // the last assistant message. If 'done' hasn't
            // happened yet, we keep the proposal in the turn-
            // scoped buffer and the 'done' / 'error' branch
            // attaches it on initial creation.
            memoryProposals.push(event.data);
            set(state => {
              for (let i = state.messages.length - 1; i >= 0; i--) {
                if (state.messages[i].role === 'assistant') {
                  const m = state.messages[i];
                  m.memory_proposals = [
                    ...(m.memory_proposals || []),
                    event.data,
                  ];
                  return;
                }
              }
            });
            break;
          }
          case 'error': {
            // Stop the pacer up front so its queued ticks can't write
            // into `streaming` after we replace it with the
            // interrupted bubble below.
            stopPacer();
            // Save-always parity with the backend (chat 3951063c,
            // 2026-05-08): append a Message carrying whatever
            // partial state we accumulated, with the same
            // `_(stream interrupted: <reason>)_` marker the
            // backend's apply_stream_error_marker produces.
            // Without this, the user would see the streamed
            // prose, hit the error, and watch the entire bubble
            // vanish (MessageList gates the streaming view on
            // phase !== 'error'). The error banner still shows
            // for top-level visibility; the bubble preserves the
            // partial content + marker inline.
            const reason = (event.data.message || '').trim() || 'vLLM stream error';
            const marker = `_(stream interrupted: ${reason})_`;
            const interruptedContent = contentText
              ? `${contentText.replace(/\s+$/, '')}\n\n${marker}`
              : marker;
            const interruptedMessage: Message = {
              id: `msg-${Date.now()}`,
              role: 'assistant',
              content: interruptedContent,
              thinking: thinkingText || null,
              tool_calls: toolCalls.length > 0 ? _snapshotToolCalls(toolCalls) : null,
              rag_context: ragCtx,
              clarification: clarification,
              compact_boundary: compactBoundary,
              plan_snapshot: planSnapshot,
              persona: routedProfile ?? persona,
              interrupted: true,
              created_at: new Date().toISOString(),
            };
            set(state => {
              state.messages.push(interruptedMessage);
              state.error = event.data.message;
              state.streaming = INITIAL_STREAMING;
            });
            break;
          }
          case 'done': {
            // If the pacer still has tokens queued, defer the bubble
            // push until the queue drains so the user sees the
            // typewriter complete instead of the bubble snapping to
            // the full text mid-animation. `drainPacer` will call
            // `processDoneInline` when the queue empties.
            if (pacerEnabled && (pendingText.length > 0 || pacerTimer !== null)) {
              pendingDoneEvent = event;
            } else {
              processDoneInline(event);
            }
            break;
          }
        }
      };

      // Build request body. Ephemeral: send full history
      // (stateless). Persistent: only last message.
      // multimodalContent is used for the last user message when
      // images are attached.
      const lastContent: MessageContent = multimodalContent || content;
      let chatMessages: { role: string; content: MessageContent }[];
      if (ephemeral) {
        const prevMessages = get().messages;
        chatMessages = [
          ...prevMessages.map(m => ({ role: m.role, content: m.content })),
          { role: 'user', content: lastContent },
        ];
      } else {
        chatMessages = [{ role: 'user', content: lastContent }];
      }

      try {
        if (resumeOpts) {
          await resumeChat(
            resumeOpts.streamId,
            resumeOpts.lastEventId,
            handleEvent,
            abort.signal,
          );
        } else {
          await streamChat(
            {
              persona,
              conversation_id: ephemeral ? undefined : get().conversationId,
              project_id: projectId,
              messages: chatMessages,
              rag: { enabled: true },
              tags: tags && tags.length > 0 ? tags : undefined,
              ephemeral: ephemeral || undefined,
              stream: true,
            },
            handleEvent,
            abort.signal,
          );
        }
      } catch (e) {
        if ((e as Error).name !== 'AbortError') {
          // Network-level failure (DNS, connection drop, non-
          // streaming 5xx). Preserve whatever partial state we
          // accumulated as an interrupted Message so the user
          // does not lose the prose they already saw, mirroring
          // the SSE 'error' branch above.
          const reason = e instanceof Error ? e.message : 'Stream failed';
          const marker = `_(stream interrupted: ${reason})_`;
          const interruptedContent = contentText
            ? `${contentText.replace(/\s+$/, '')}\n\n${marker}`
            : marker;
          const interruptedMessage: Message = {
            id: `msg-${Date.now()}`,
            role: 'assistant',
            content: interruptedContent,
            thinking: thinkingText || null,
            tool_calls: toolCalls.length > 0 ? toolCalls : null,
            rag_context: ragCtx,
            clarification: clarification,
            persona: routedProfile ?? persona,
            interrupted: true,
            created_at: new Date().toISOString(),
          };
          set(state => {
            state.messages.push(interruptedMessage);
            state.error = reason;
            state.streaming = INITIAL_STREAMING;
          });
        }
      } finally {
        // If the SSE stream completed cleanly but the pacer still has
        // queued tokens + a pendingDoneEvent, wait for drainPacer to
        // process them and push the assistant Message. If the stream
        // aborted or errored, the corresponding handler already
        // stopped the pacer (resolving pacerDrained immediately).
        // Either way, await is safe.
        if (pacerEnabled && (pendingText.length > 0 || pendingDoneEvent !== null)) {
          await pacerDrained;
        }
        stopPacer();
        _abortController = null;
      }
    },

    stopGenerating: () => {
      _abortController?.abort();
      set(state => {
        state.streaming.phase = 'done';
      });
    },

    dismissMemoryProposal: (proposalId: string) =>
      set(state => {
        // Remove the proposal from whichever assistant message
        // carries it. Optimistic — no rollback on REST failure;
        // the proposal will simply re-appear on next reload if
        // the server side rejected it. Matches the design-doc
        // choice for the pill behaviour.
        for (const m of state.messages) {
          if (!m.memory_proposals) continue;
          const next = m.memory_proposals.filter(p => p.id !== proposalId);
          if (next.length !== m.memory_proposals.length) {
            m.memory_proposals = next.length > 0 ? next : null;
          }
        }
      }),
  })),
);


// ── Test helpers ───────────────────────────────────────────────────

/**
 * Reset every reactive slice back to the initial state. Call from
 * `beforeEach` in any test file that mutates the store, so state
 * from test N doesn't leak into test N+1. The store is module-
 * scoped so without this, two consecutive tests share state.
 *
 * Actions are NOT reassigned — they're stable references defined
 * at store creation. Only the state slices get reset.
 */
export function _resetChatStoreForTests(): void {
  useChatStore.setState({
    messages: [],
    conversationId: null,
    conversationPersona: null,
    streaming: INITIAL_STREAMING,
    error: null,
    artifacts: [],
    lastArtifactEvent: null,
    // Tests assert on streaming.content synchronously after dispatching
    // 'token' events, so disable the pacer by default. Individual tests
    // that exercise the pacer can override to 60 (or any finite rate).
    charsPerSec: Infinity,
  });
  _abortController?.abort();
  _abortController = null;
}
