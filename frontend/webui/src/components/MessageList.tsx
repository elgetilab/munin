import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { Message, ToolCall, RagContext, Clarification } from '../lib/types';
import { TaskLog } from './TaskLog';
import { FeatherVortex } from './FeatherVortex';
import { Markdown } from './Markdown';
import { ClarificationCard } from './ClarificationCard';
import { MemoryProposalPill } from './MemoryProposalPill';
import { CompactBoundaryDivider } from './CompactBoundaryDivider';
import { PlanCard } from './PlanCard';
import { InlineArtifacts } from './InlineArtifacts';
import { detectPhase } from '../lib/streamPhase';
import { rewordInterruptedMarker } from '../lib/modelError';
import { BTN_PRIMARY } from './ui/cardButton';
import { CHAT_COLUMN } from './ui/chatColumn';
import { ReplyActions } from './ReplyActions';
import { UserMessage } from './UserMessage';
import { ModeLabel } from './ModeChip';
import { canResend } from '../lib/resend';

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
  // Whether this chat can be reported (saved, not temporary): shows Report
  // under each reply.
  canReport?: boolean;
  // Retry and Edit: send `content` again in place of the saved user message
  // `messageId`, dropping it and everything after it. Absent where that
  // can't be done (temporary chats).
  onResend?: (messageId: string, content: string) => void;
}


/** Strip system-injected context blocks (e.g. artifact summaries) from assistant content */
function cleanContent(raw: string): string {
  return rewordInterruptedMarker(raw
    .replace(/=== ACTIVE ARTIFACTS ===[\s\S]*?(?====|$)/g, '')
    .replace(/^\n+/, '')
    .trim());
}


// Distance (in CSS pixels) from the scroll-container bottom within which
// the floating "jump to bottom" button stays hidden. The user is close
// enough that they don't need the affordance.
const JUMP_BUTTON_HIDE_THRESHOLD_PX = 96;

// Where a just-sent message sits below the top of the scroll area: the
// container's own top padding (py-6), so it lines up with the first message.
const SENT_MESSAGE_TOP_GAP_PX = 24;

export function MessageList({ messages, streaming, onSendClarification, onDismissMemoryProposal, conversationId, onPlanApproved, onPlanRejected, onPlanEdited, onContinue, researchTimeline, canReport = false, onResend }: MessageListProps) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const columnRef = useRef<HTMLDivElement>(null);
  // Room below the transcript so a just-sent message can sit at the top while
  // the reply streams into the space under it. Sized through the DOM, not
  // state, so a growing reply never re-renders the list.
  const spacerRef = useRef<HTMLDivElement>(null);
  // The user message pinned to the top since it was sent; null when the view
  // is a loaded conversation, which opens at the bottom as before.
  const anchorIdRef = useRef<string | null>(null);
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
  //   2. New user message added -> one smooth scroll that puts it at the
  //      top of the view, with a spacer below so it can get there; the
  //      reply then streams into the visible space under it (2026-11,
  //      as Claude does). The spacer shrinks as the reply grows, so the
  //      page never moves on its own.
  //   3. Streaming tokens, thinking, tool calls -> NO scroll. The
  //      user reads at their own pace.
  //   4. Floating jump-to-bottom button when they want to catch up.

  // Size the spacer so the pinned message can reach the top: the view's
  // height less what already sits from that message down.
  const sizeSpacer = useCallback(() => {
    const el = containerRef.current, column = columnRef.current, spacer = spacerRef.current;
    if (!el || !column || !spacer) return;
    const id = anchorIdRef.current;
    const anchor = id ? column.querySelector<HTMLElement>(`[data-message-id="${CSS.escape(id)}"]`) : null;
    if (!anchor) { spacer.style.height = '0px'; return; }
    const below = column.getBoundingClientRect().bottom - anchor.getBoundingClientRect().top;
    const padBottom = parseFloat(getComputedStyle(el).paddingBottom) || 0;
    const room = Math.max(0, el.clientHeight - SENT_MESSAGE_TOP_GAP_PX - below - padBottom);
    spacer.style.height = `${Math.round(room)}px`;
  }, []);

  // (1) Conversation switch. A new chat gets its id only after its first
  // message is sent, so keep that message pinned when the id first arrives.
  const prevConversationRef = useRef<string | null | undefined>(undefined);
  const mountedRef = useRef(false);
  useEffect(() => {
    const prev = prevConversationRef.current;
    const firstRun = !mountedRef.current;
    prevConversationRef.current = conversationId;
    mountedRef.current = true;
    if (anchorIdRef.current && (firstRun || prev == null)) return;
    anchorIdRef.current = null;
    sizeSpacer();
    const el = containerRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [conversationId, sizeSpacer]);

  // (2) New user message. We track the previous count with a ref so
  // we only fire on the increment-with-user-tail edge. The list mounts
  // with a new chat's first message already in it, so that one counts too.
  const prevLengthRef = useRef(messages.length === 1 && messages[0].role === 'user' ? 0 : messages.length);
  useLayoutEffect(() => {
    const prev = prevLengthRef.current;
    prevLengthRef.current = messages.length;
    if (messages.length <= prev) return;
    const last = messages[messages.length - 1];
    if (last?.role !== 'user') return;
    anchorIdRef.current = last.id;
    sizeSpacer();
    const el = containerRef.current;
    const anchor = columnRef.current?.querySelector<HTMLElement>(`[data-message-id="${CSS.escape(last.id)}"]`);
    if (!el || !anchor) return;
    const top = el.scrollTop + anchor.getBoundingClientRect().top - el.getBoundingClientRect().top - SENT_MESSAGE_TOP_GAP_PX;
    const reduce = typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    el.scrollTo?.({ top, behavior: reduce ? 'auto' : 'smooth' });
  }, [messages, sizeSpacer]);

  // (4) Update jump-button visibility on scroll. Coalesced naturally
  // by the browser; no rAF/throttle needed at typical scroll
  // cadences.
  const updateJumpButton = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    // Measured to the end of the transcript, not of the spacer under it.
    const spacer = spacerRef.current?.offsetHeight ?? 0;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight - spacer;
    const farFromBottom = distanceFromBottom > JUMP_BUTTON_HIDE_THRESHOLD_PX;
    setShowJumpButton(prev => (prev === farFromBottom ? prev : farFromBottom));
  }, []);

  // Growing content fires no scroll event, so without this an answer streaming
  // past the bottom would show no button until the user scrolled by hand.
  useEffect(() => {
    const column = columnRef.current;
    if (!column || typeof ResizeObserver === 'undefined') return;
    // The spacer shrinks as the reply grows, and follows the view's height.
    const observer = new ResizeObserver(() => { sizeSpacer(); updateJumpButton(); });
    observer.observe(column);
    if (containerRef.current) observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, [updateJumpButton, sizeSpacer]);

  const scrollToBottom = () => {
    // 'end': the spacer sits below this marker, so 'start' would lift the
    // last lines out of view.
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  };

  const isActive = streaming.phase !== 'idle' && streaming.phase !== 'done' && streaming.phase !== 'error';

  // Each reply's position among the replies, for its actions and reports.
  const replyNumbers = new Map<string, number>();
  for (const m of messages) if (m.role === 'assistant') replyNumbers.set(m.id, replyNumbers.size + 1);
  const latestReplyId = [...messages].reverse().find(m => m.role === 'assistant')?.id;
  // Retry and Edit only while nothing streams, and only for messages whose
  // text can be sent again as it was (see lib/resend).
  const resend = !isActive ? onResend : undefined;
  const lastUserIndex = messages.map(m => m.role).lastIndexOf('user');
  // Retry on the latest reply sends the user message before it again.
  const latestReplyIndex = latestReplyId ? messages.findIndex(m => m.id === latestReplyId) : -1;
  const askedBefore = [...messages.slice(0, Math.max(0, latestReplyIndex))].reverse().find(m => m.role === 'user');
  const onRetryLatest = resend && askedBefore && canResend(askedBefore)
    ? () => resend(askedBefore.id, askedBefore.content)
    : undefined;

  return (
    <div className="flex-1 relative min-h-0 flex flex-col">
    <div ref={containerRef} onScroll={updateJumpButton} className="flex-1 overflow-y-auto px-4 py-6">
      <div ref={columnRef} className={`${CHAT_COLUMN} space-y-6`}>
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
          <div key={msg.id} data-message-id={msg.id}>
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
            <MessageBubble message={msg} conversationId={conversationId} onSendClarification={onSendClarification} onDismissMemoryProposal={onDismissMemoryProposal} onContinue={isLast && !streamingActive ? onContinue : undefined}
              actions={msg.role === 'assistant' ? { replyNumber: replyNumbers.get(msg.id) ?? 0, latest: msg.id === latestReplyId && !isActive, canReport, onRetry: msg.id === latestReplyId ? onRetryLatest : undefined } : undefined}
              onEdit={msg.role === 'user' && resend && canResend(msg) ? (text: string) => resend(msg.id, text) : undefined}
              dropsLaterTurns={msg.role === 'user' && i < lastUserIndex} />
          </div>
          );
        })}

        {/* Streaming state */}
        {isActive && (
          <div className="space-y-3">
            {/* Transient retry indicator when vLLM is hiccuping */}
            {streaming.retrying && (
              <div
                title={streaming.retrying.reason || undefined}
                className="flex items-center gap-2 px-3 py-2 rounded-lg bg-warning/10 border border-warning text-xs text-warning"
              >
                <span className="inline-block w-2 h-2 rounded-full bg-warning animate-pulse" />
                <span>
                  Munin is having trouble reaching the model. Retrying
                  {` (${streaming.retrying.attempt} of ${streaming.retrying.maxAttempts})`}…
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

            {/* The mode the router picked for the turn in progress, as quiet
                text; a finished reply shows it in its action row. */}
            <ModeLabel profile={streaming.routedProfile} />

            {/* Loading vortex — show when no content yet OR during tool calls */}
            {(!streaming.content.trim() || streaming.phase === 'tool_call') && (
              <div className="flex justify-start py-2">
                <FeatherVortex size="inline" phase={detectPhase(streaming)} />
              </div>
            )}

            {/* Streaming content */}
            {cleanContent(streaming.content) && (
              <div className="text-base text-text-primary leading-relaxed">
                <Markdown content={cleanContent(streaming.content)} />
              </div>
            )}

            <InlineArtifacts toolCalls={streaming.toolCalls} content={streaming.content} conversationId={conversationId} />

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

        {/* Deep Research timeline — last item of the transcript so it scrolls
            with the conversation instead of overlaying it. */}
        {researchTimeline}

        <div ref={bottomRef} />
      </div>
      <div ref={spacerRef} aria-hidden="true" data-testid="sent-message-spacer" />
    </div>
      {/* Floating jump-to-bottom button. Appears once the user has
          scrolled more than JUMP_BUTTON_HIDE_THRESHOLD_PX above the
          bottom. Click to smooth-scroll back. Positioned absolute
          inside the relative wrapper so it floats over the scroll
          area without participating in flow. */}
      {showJumpButton && (
        <button
          onClick={scrollToBottom}
          className="absolute bottom-6 right-6 w-10 h-10 rounded-full bg-bg-tertiary border border-border shadow-lg flex items-center justify-center text-text-secondary hover:text-text-primary hover:border-accent transition-colors cursor-pointer"
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

function MessageBubble({ message, conversationId, onSendClarification, onDismissMemoryProposal, onContinue, actions, onEdit, dropsLaterTurns = false }: { message: Message; conversationId?: string | null; onSendClarification?: (answer: string) => void; onDismissMemoryProposal?: (proposalId: string) => void; onContinue?: () => void; actions?: { replyNumber: number; latest: boolean; canReport: boolean; onRetry?: () => void }; onEdit?: (text: string) => void; dropsLaterTurns?: boolean }) {
  if (message.role === 'user') {
    return <UserMessage content={message.content} onEdit={onEdit} dropsLaterTurns={dropsLaterTurns} />;
  }

  return (
    <div className="space-y-2 group/reply">
      {/* Task log for completed messages */}
      {(message.thinking || message.tool_calls || message.rag_context) && (
        <TaskLog
          thinking={message.thinking}
          toolCalls={message.tool_calls}
          ragContext={message.rag_context}
        />
      )}

      {cleanContent(message.content) && (
        <div className="text-base text-text-primary leading-relaxed">
          <Markdown content={cleanContent(message.content)} />
        </div>
      )}

      <InlineArtifacts toolCalls={message.tool_calls} content={message.content} conversationId={conversationId} />

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
          <span>Munin paused after using its allowance of tools for this answer.</span>
          <button
            type="button"
            onClick={onContinue}
            className={BTN_PRIMARY}
          >
            Continue
          </button>
        </div>
      )}

      {/* Copy and Report, under a reply that has text. */}
      {actions && cleanContent(message.content) && (
        <ReplyActions content={cleanContent(message.content)} replyNumber={actions.replyNumber} latest={actions.latest} canReport={actions.canReport} onRetry={actions.onRetry} mode={message.persona} />
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
