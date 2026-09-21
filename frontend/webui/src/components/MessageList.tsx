import { useEffect, useRef, useState } from 'react';
import type { Message, ToolCall, RagContext, Clarification } from '../lib/types';
import { TaskLog } from './TaskLog';
import { FeatherVortex } from './FeatherVortex';
import { Markdown } from './Markdown';
import { ClarificationCard } from './ClarificationCard';
import { MemoryProposalPill } from './MemoryProposalPill';
import { CompactBoundaryDivider } from './CompactBoundaryDivider';
import { PlanCard } from './PlanCard';
import { detectPhase } from '../lib/streamPhase';

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
  routedProfile?: string | null;
  phase: 'idle' | 'thinking' | 'tool_call' | 'generating' | 'done' | 'error';
  retrying: RetryingState | null;
}

interface MessageListProps {
  messages: Message[];
  streaming: StreamingState;
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
  // Invoked when the user clicks Continue on a turn that stopped at its
  // tool-use budget. The parent (App) sends a synthetic "please continue"
  // user message so the model picks the task back up (rpt_20260702).
  onContinue?: () => void;
  // The Deep Research timeline for the active conversation, if any. Rendered
  // as the last item of the transcript so it scrolls WITH the messages
  // instead of sitting pinned above the composer. App owns the DR store and
  // passes the ready-to-render node (or null).
  researchTimeline?: React.ReactNode;
}

// Small muted pill showing the internal profile the per-turn router
// selected for an assistant message ("code" / "research"). The plain
// "chat" profile is the default and gets no chip, keeping the common
// case visually quiet.
function RoutedProfileChip({ profile }: { profile?: string | null }) {
  if (!profile) return null;
  const normalized = profile.toLowerCase();
  if (normalized === 'chat' || normalized === 'munin') return null;
  const label = normalized.charAt(0).toUpperCase() + normalized.slice(1);
  return (
    <div className="select-none">
      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] uppercase tracking-wider text-text-secondary bg-bg-tertiary border border-border">
        {label}
      </span>
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


// Distance (in CSS pixels) from the scroll-container bottom within which
// the floating "jump to bottom" button stays hidden. The user is close
// enough that they don't need the affordance.
const JUMP_BUTTON_HIDE_THRESHOLD_PX = 96;

export function MessageList({ messages, streaming, onSendClarification, onDismissMemoryProposal, conversationId, onPlanApproved, onPlanRejected, onPlanEdited, onContinue, researchTimeline }: MessageListProps) {
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

  return (
    <div className="flex-1 relative min-h-0 flex flex-col">
    <div ref={containerRef} onScroll={handleScroll} className="flex-1 overflow-y-auto px-4 py-6">
      <div className="max-w-2xl mx-auto space-y-6">
        {messages.map((msg, i) => {
          const isLast = i === messages.length - 1;
          // Only surface Continue on the final bubble, and only while
          // nothing is streaming — otherwise a mid-history paused turn or
          // an in-flight continuation would show a stale button.
          const streamingActive =
            streaming.phase === 'thinking' ||
            streaming.phase === 'tool_call' ||
            streaming.phase === 'generating';
          return (
          <div key={msg.id}>
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
            <MessageBubble message={msg} onSendClarification={onSendClarification} onDismissMemoryProposal={onDismissMemoryProposal} onContinue={isLast && !streamingActive ? onContinue : undefined} />
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

            {/* Routed-profile chip for the in-progress turn. */}
            <RoutedProfileChip profile={streaming.routedProfile} />

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

        {/* Deep Research timeline — last item of the transcript so it scrolls
            with the conversation instead of overlaying it. */}
        {researchTimeline}

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

function MessageBubble({ message, onSendClarification, onDismissMemoryProposal, onContinue }: { message: Message; onSendClarification?: (answer: string) => void; onDismissMemoryProposal?: (proposalId: string) => void; onContinue?: () => void }) {
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

      {/* Routed-profile chip: the internal profile the per-turn router
          selected for this assistant turn (stored on message.persona).
          Hidden for the default "chat" profile. */}
      <RoutedProfileChip profile={message.persona} />

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

      {/* Budget-cap Continue affordance. Shown when the turn stopped
          because it exhausted its (auto-extended) tool-use budget, so the
          user can resume the task with one click instead of typing
          "keep going" (rpt_20260702). */}
      {message.paused_at_budget && onContinue && (
        <div className="mt-2 flex items-center gap-2 text-sm text-text-secondary">
          <span>Munin paused at its tool-use budget.</span>
          <button
            type="button"
            onClick={onContinue}
            className="inline-flex items-center px-3 py-1 rounded-full text-sm font-medium text-text-primary bg-bg-tertiary border border-border hover:bg-bg-secondary transition-colors"
          >
            Continue
          </button>
        </div>
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
