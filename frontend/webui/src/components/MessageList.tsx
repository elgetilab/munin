import { useEffect, useRef, useState } from 'react';
import type { Message, ToolCall, RagContext, Clarification, Delegation, Persona } from '../lib/types';
import { TaskLog } from './TaskLog';
import { FeatherVortex } from './FeatherVortex';
import { Markdown } from './Markdown';
import { ClarificationCard } from './ClarificationCard';
import { MemoryProposalPill } from './MemoryProposalPill';
import { CompactBoundaryDivider } from './CompactBoundaryDivider';
import { PersonaDivider } from './PersonaDivider';
import { PlanCard } from './PlanCard';

interface RetryingState {
  attempt: number;
  maxAttempts: number;
  reason: string;
}

interface StreamingState {
  content: string;
  thinking: string;
  toolCalls: ToolCall[];
  ragContext: RagContext | null;
  clarification: Clarification | null;
  delegations: Delegation[];
  phase: 'idle' | 'thinking' | 'tool_call' | 'generating' | 'done' | 'error';
  retrying: RetryingState | null;
}

interface MessageListProps {
  messages: Message[];
  streaming: StreamingState;
  personas?: Persona[];
  onSendClarification?: (answer: string) => void;
  // P2 #25: invoked when the user accepts or dismisses a memory
  // proposal pill. Should remove the proposal from the message
  // so the pill disappears optimistically.
  onDismissMemoryProposal?: (proposalId: string) => void;
  // P2 #24 Phase 2: id of the conversation currently in view, so
  // the PlanCard can POST to /api/chats/{cid}/plan/* endpoints.
  conversationId?: string | null;
  // Triggered after the user approves a plan via the PlanCard.
  // The parent (App) sends a synthetic "approved, continue" user
  // message so the model resumes. Phase 2 MVP: no optional context
  // input box; the synthetic message body is fixed.
  onPlanApproved?: () => void;
  onPlanRejected?: () => void;
  onPlanEdited?: () => void;
}

function personaName(personas: Persona[] | undefined, id: string): string {
  const p = personas?.find(p => p.id === id);
  if (!p) return id;
  // Persona names often include a dash like "Curie - Research" — use the short label
  return p.name.split('-')[0].trim() || p.name;
}

function DelegationNote({ delegation, personas }: { delegation: Delegation; personas?: Persona[] }) {
  const to = personaName(personas, delegation.to_persona);
  return (
    <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-accent/8 border border-accent/30 text-xs text-text-secondary">
      <span className="text-accent leading-tight">{'↳'}</span>
      <div className="leading-snug">
        <span className="text-text-primary font-medium">Handing off to {to}</span>
        {delegation.reason ? <>: <span className="text-text-secondary">{delegation.reason}</span></> : null}
      </div>
    </div>
  );
}

/** Strip system-injected context blocks (e.g. artifact summaries) from assistant content */
function cleanContent(raw: string): string {
  return raw
    .replace(/=== ACTIVE ARTIFACTS ===[\s\S]*?(?====|$)/g, '')
    .replace(/^\n+/, '')
    .trim();
}

export function detectPhase(streaming: StreamingState): string {
  if (streaming.toolCalls.length > 0) {
    const last = streaming.toolCalls[streaming.toolCalls.length - 1];
    if (last.name === 'invoke_agent') return 'deep_research';
    if (['paper_search', 'semantic_scholar_search', 'paper_lookup', 'get_citations', 'get_references', 'get_author_papers', 'check_papers_availability'].includes(last.name)) {
      return 'paper_search';
    }
    if (['web_search', 'web_fetch'].includes(last.name)) return 'web_search';
    if (['run_python', 'sandbox_reset', 'compile_latex'].includes(last.name)) return 'code';
    if (['create_artifact', 'update_artifact'].includes(last.name)) return 'code';
    if (last.name === 'llm_summarize') return 'processing';
  }
  return 'thinking';
}

// Distance (in CSS pixels) from the scroll-container bottom within which
// the floating "jump to bottom" button stays hidden. The user is close
// enough that they don't need the affordance.
const JUMP_BUTTON_HIDE_THRESHOLD_PX = 96;

export function MessageList({ messages, streaming, personas, onSendClarification, onDismissMemoryProposal, conversationId, onPlanApproved, onPlanRejected, onPlanEdited }: MessageListProps) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  // Drives the floating "jump to bottom" button. True when the user
  // has scrolled up far enough from the bottom that the button is
  // worth showing.
  const [showJumpButton, setShowJumpButton] = useState(false);

  // Bug 2026-06-02: the original implementation auto-scrolled on every
  // streaming-token update via scrollIntoView({ behavior: 'smooth' }).
  // The smooth-scroll animation runs ~300ms and is NOT interruptible
  // by user wheel events in most browsers, so a fresh token arriving
  // every ~16ms (60 tok/s) means the animation never finishes; the
  // user can't escape the bottom even with a "near-bottom" gate. The
  // current behaviour drops streaming-token auto-scroll entirely:
  //
  //   1. Conversation switch (conversationId change) -> jump to bottom
  //      instantly so the user lands at the latest message.
  //   2. New user message added -> smooth-scroll so the user sees
  //      their own bubble land at the bottom after pressing Enter.
  //   3. Streaming tokens, thinking, tool calls -> NO scroll. The
  //      user reads at their own pace.
  //   4. Floating jump-to-bottom button when they want to catch up.

  // (1) Conversation switch.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [conversationId]);

  // (2) New user message. We track the previous count with a ref so
  // we only fire on the increment-with-user-tail edge.
  const prevLengthRef = useRef(messages.length);
  useEffect(() => {
    const prev = prevLengthRef.current;
    prevLengthRef.current = messages.length;
    if (messages.length <= prev) return;
    const last = messages[messages.length - 1];
    if (last?.role === 'user') {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages]);

  // (4) Update jump-button visibility on scroll. Coalesced naturally
  // by the browser; no rAF/throttle needed at typical scroll
  // cadences.
  const handleScroll = () => {
    const el = containerRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    const farFromBottom = distanceFromBottom > JUMP_BUTTON_HIDE_THRESHOLD_PX;
    setShowJumpButton(prev => (prev === farFromBottom ? prev : farFromBottom));
  };

  const scrollToBottom = () => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  const isActive = streaming.phase !== 'idle' && streaming.phase !== 'done' && streaming.phase !== 'error';
  const isIdle = streaming.phase === 'idle' || streaming.phase === 'done';

  // Effective authoring persona per message, carrying the last known
  // persona forward across legacy NULL rows so a divider is drawn only
  // on a real change (and never for an all-legacy transcript).
  const effectivePersonas: (string | undefined)[] = [];
  {
    let last: string | undefined = undefined;
    for (const m of messages) {
      if (m.persona) last = m.persona;
      effectivePersonas.push(last);
    }
  }

  return (
    <div className="flex-1 relative min-h-0 flex flex-col">
    <div ref={containerRef} onScroll={handleScroll} className="flex-1 overflow-y-auto px-4 py-6">
      <div className="max-w-2xl mx-auto space-y-6">
        {messages.map((msg, idx) => {
          // Persona-switch divider: drawn above the first message whose
          // effective persona differs from the previous one. Covers both
          // delegate_to_persona and manual switches, and survives reload.
          const showPersonaDivider =
            idx > 0 &&
            !!effectivePersonas[idx] &&
            effectivePersonas[idx] !== effectivePersonas[idx - 1];
          const delegationReason = showPersonaDivider
            ? msg.delegations?.find(d => d.to_persona === effectivePersonas[idx])?.reason
            : undefined;
          return (
          <div key={msg.id}>
            {showPersonaDivider ? (
              <PersonaDivider
                personaId={effectivePersonas[idx]!}
                personas={personas}
                reason={delegationReason}
              />
            ) : null}
            {/* P2 #22: render the boundary divider ABOVE the
                assistant message that triggered compaction so the
                visible order matches the conversation flow. */}
            {msg.compact_boundary ? (
              <CompactBoundaryDivider boundary={msg.compact_boundary} />
            ) : null}
            {/* P2 #24 Phase 1: plan card above the assistant
                bubble that last invoked set_plan / update_plan_item
                this turn. Read-only in Phase 1; Phase 2 adds the
                Approve / Edit / Reject controls. */}
            {msg.plan_snapshot ? (
              <PlanCard
                plan={msg.plan_snapshot}
                conversationId={conversationId}
                onAfterApprove={onPlanApproved}
                onAfterReject={onPlanRejected}
                onAfterEdit={onPlanEdited}
              />
            ) : null}
            <MessageBubble message={msg} onSendClarification={onSendClarification} onDismissMemoryProposal={onDismissMemoryProposal} />
          </div>
          );
        })}

        {/* Streaming state */}
        {isActive && (
          <div className="space-y-3">
            {/* Transient retry indicator when vLLM is hiccuping */}
            {streaming.retrying && (
              <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-amber-50 border border-amber-300 text-xs text-amber-800">
                <span className="inline-block w-2 h-2 rounded-full bg-amber-500 animate-pulse" />
                <span>
                  Reconnecting to vLLM
                  {` (attempt ${streaming.retrying.attempt}/${streaming.retrying.maxAttempts}`}
                  {streaming.retrying.reason ? `, ${streaming.retrying.reason}` : ''}
                  )…
                </span>
              </div>
            )}

            {/* Task log for current stream */}
            {(streaming.thinking || streaming.toolCalls.length > 0 || streaming.ragContext) && (
              <TaskLog
                thinking={streaming.thinking || null}
                toolCalls={streaming.toolCalls.length > 0 ? streaming.toolCalls : null}
                ragContext={streaming.ragContext}
                isStreaming
              />
            )}

            {/* Persona delegation notes for the current stream */}
            {streaming.delegations.length > 0 && (
              <div className="space-y-2">
                {streaming.delegations.map((d, i) => (
                  <DelegationNote key={i} delegation={d} personas={personas} />
                ))}
              </div>
            )}

            {/* Loading vortex — show when no content yet OR during tool calls */}
            {(!streaming.content.trim() || streaming.phase === 'tool_call') && (
              <div className="flex justify-center py-8">
                <FeatherVortex size="inline" phase={detectPhase(streaming)} />
              </div>
            )}

            {/* Streaming content */}
            {cleanContent(streaming.content) && (
              <div className="text-base text-text-primary leading-relaxed">
                <Markdown content={cleanContent(streaming.content)} />
              </div>
            )}

            {/* Working spinner below already-streamed content. Shown for
                ANY active non-tool_call phase (not just 'generating'), so
                the user still sees activity while the model is quietly
                producing a large tool argument — e.g. writing an artifact
                body, where no token/phase events arrive and the phase
                stays 'thinking' (chat d28ef78e). When phase is 'tool_call'
                the big vortex above already covers it. */}
            {cleanContent(streaming.content) && streaming.phase !== 'tool_call' && (
              <div className="pt-1">
                <FeatherVortex size="generating" />
              </div>
            )}

            {/* Streaming clarification card */}
            {streaming.clarification && onSendClarification && (
              <ClarificationCard
                clarification={streaming.clarification}
                onSubmit={onSendClarification}
              />
            )}
          </div>
        )}

        {/* Idle indicator — small vortex after last message */}
        {isIdle && messages.length > 0 && (
          <div className="pt-1">
            <FeatherVortex size="idle" />
          </div>
        )}

        <div ref={bottomRef} />
      </div>
    </div>
      {/* Floating jump-to-bottom button. Appears once the user has
          scrolled more than JUMP_BUTTON_HIDE_THRESHOLD_PX above the
          bottom. Click to smooth-scroll back. Positioned absolute
          inside the relative wrapper so it floats over the scroll
          area without participating in flow. */}
      {showJumpButton && (
        <button
          onClick={scrollToBottom}
          className="absolute bottom-6 right-6 w-10 h-10 rounded-full bg-bg-tertiary border border-border shadow-lg flex items-center justify-center text-text-secondary hover:text-accent hover:border-accent transition-colors cursor-pointer"
          title="Scroll to bottom"
          aria-label="Scroll to bottom"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </button>
      )}
    </div>
  );
}

function MessageBubble({ message, onSendClarification, onDismissMemoryProposal }: { message: Message; onSendClarification?: (answer: string) => void; onDismissMemoryProposal?: (proposalId: string) => void }) {
  if (message.role === 'user') {
    return (
      <div className="flex gap-3 justify-end">
        <div className="max-w-[80%] bg-bg-tertiary border border-border rounded-2xl rounded-br-sm px-4 py-3 text-base text-text-primary leading-relaxed whitespace-pre-wrap">
          {message.content}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {/* Task log for completed messages */}
      {(message.thinking || message.tool_calls || message.rag_context) && (
        <TaskLog
          thinking={message.thinking}
          toolCalls={message.tool_calls}
          ragContext={message.rag_context}
        />
      )}

      {/* Persona handoff is shown as a PersonaDivider above the bubble
          (driven by the per-message persona field, reload-safe). The
          inline DelegationNote is kept only for the live stream. */}

      {cleanContent(message.content) && (
        <div className="text-base text-text-primary leading-relaxed">
          <Markdown content={cleanContent(message.content)} />
        </div>
      )}

      {/* Clarification card on completed message */}
      {message.clarification && onSendClarification && (
        <ClarificationCard
          clarification={message.clarification}
          onSubmit={onSendClarification}
        />
      )}

      {/* P2 #25: auto-extracted memory candidates. Rendered below the
          bubble; user can accept or dismiss each one. */}
      {message.memory_proposals && message.memory_proposals.length > 0 && onDismissMemoryProposal && (
        <div className="mt-1">
          {message.memory_proposals.map(p => (
            <MemoryProposalPill
              key={p.id}
              proposal={p}
              onDismiss={onDismissMemoryProposal}
            />
          ))}
        </div>
      )}
    </div>
  );
}
