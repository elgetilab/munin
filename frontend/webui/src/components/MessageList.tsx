import { useEffect, useRef } from 'react';
import type { Message, ToolCall, RagContext, Clarification, Delegation, Persona } from '../lib/types';
import { TaskLog } from './TaskLog';
import { FeatherVortex } from './FeatherVortex';
import { Markdown } from './Markdown';
import { ClarificationCard } from './ClarificationCard';
import { MemoryProposalPill } from './MemoryProposalPill';
import { CompactBoundaryDivider } from './CompactBoundaryDivider';

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
    if (last.name === 'llm_summarize') return 'processing';
  }
  return 'thinking';
}

export function MessageList({ messages, streaming, personas, onSendClarification, onDismissMemoryProposal }: MessageListProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages.length, streaming.content, streaming.thinking, streaming.toolCalls.length]);

  const isActive = streaming.phase !== 'idle' && streaming.phase !== 'done' && streaming.phase !== 'error';
  const isIdle = streaming.phase === 'idle' || streaming.phase === 'done';

  return (
    <div className="flex-1 overflow-y-auto px-4 py-6">
      <div className="max-w-2xl mx-auto space-y-6">
        {messages.map(msg => (
          <div key={msg.id}>
            {/* P2 #22: render the boundary divider ABOVE the
                assistant message that triggered compaction so the
                visible order matches the conversation flow. */}
            {msg.compact_boundary ? (
              <CompactBoundaryDivider boundary={msg.compact_boundary} />
            ) : null}
            <MessageBubble message={msg} personas={personas} onSendClarification={onSendClarification} onDismissMemoryProposal={onDismissMemoryProposal} />
          </div>
        ))}

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
              <div className="text-sm text-text-primary leading-relaxed">
                <Markdown content={cleanContent(streaming.content)} />
              </div>
            )}

            {/* Fast spinner below content while generating */}
            {cleanContent(streaming.content) && streaming.phase === 'generating' && (
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
  );
}

function MessageBubble({ message, personas, onSendClarification, onDismissMemoryProposal }: { message: Message; personas?: Persona[]; onSendClarification?: (answer: string) => void; onDismissMemoryProposal?: (proposalId: string) => void }) {
  if (message.role === 'user') {
    return (
      <div className="flex gap-3 justify-end">
        <div className="max-w-[80%] bg-bg-tertiary border border-border rounded-2xl rounded-br-sm px-4 py-3 text-sm text-text-primary leading-relaxed whitespace-pre-wrap">
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

      {/* Persona delegation notes */}
      {message.delegations && message.delegations.length > 0 && (
        <div className="space-y-2">
          {message.delegations.map((d, i) => (
            <DelegationNote key={i} delegation={d} personas={personas} />
          ))}
        </div>
      )}

      {cleanContent(message.content) && (
        <div className="text-sm text-text-primary leading-relaxed">
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
