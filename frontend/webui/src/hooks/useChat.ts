import { useState, useCallback, useRef, useEffect } from 'react';
import { streamChat, resumeChat, fetchChat } from '../lib/api';
import type { Message, MessageContent, SSEEvent, ToolCall, RagContext, AgentState, Clarification, ArtifactSummary, ArtifactCreatedEvent, ArtifactUpdatedEvent, TagChip, Delegation } from '../lib/types';

interface RetryingState {
  attempt: number;
  maxAttempts: number;
  reason: string;
}

interface ReconnectingState {
  attempt: number;
  maxAttempts: number;
}

interface StreamingState {
  content: string;
  thinking: string;
  toolCalls: ToolCall[];
  ragContext: RagContext | null;
  clarification: Clarification | null;
  delegations: Delegation[];
  phase: 'idle' | 'thinking' | 'tool_call' | 'generating' | 'done' | 'error';
  // Set when the backend emits a `retrying` SSE because a vLLM call hit a
  // transient error (5xx / 429 / pre-first-byte drop). Cleared as soon as
  // any other event arrives (the call succeeded) or the stream finishes.
  retrying: RetryingState | null;
  // Set by the SSE consumer when the connection drops and a Last-Event-ID
  // resume is being attempted (P1 #10). Cleared once any event lands on
  // the resumed connection.
  reconnecting: ReconnectingState | null;
}

const INITIAL_STREAMING: StreamingState = {
  content: '',
  thinking: '',
  toolCalls: [],
  ragContext: null,
  clarification: null,
  delegations: [],
  phase: 'idle',
  retrying: null,
  reconnecting: null,
};

import { readActiveStream } from '../lib/api';

export function useChat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversationPersona, setConversationPersona] = useState<string | null>(null);
  const [streaming, setStreaming] = useState<StreamingState>(INITIAL_STREAMING);
  const [error, setError] = useState<string | null>(null);
  const [artifacts, setArtifacts] = useState<ArtifactSummary[]>([]);
  const [lastArtifactEvent, setLastArtifactEvent] = useState<ArtifactCreatedEvent | ArtifactUpdatedEvent | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const messagesRef = useRef<Message[]>([]);
  messagesRef.current = messages;

  const loadConversation = useCallback(async (id: string) => {
    setError(null);
    try {
      const chat = await fetchChat(id);
      setMessages(chat.messages);
      setConversationId(chat.id);
      setConversationPersona(chat.persona || null);
      return chat;
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load conversation');
      return null;
    }
  }, []);

  const clearConversation = useCallback(() => {
    setMessages([]);
    setConversationId(null);
    setConversationPersona(null);
    setStreaming(INITIAL_STREAMING);
    setError(null);
    setArtifacts([]);
    setLastArtifactEvent(null);
  }, []);

  const sendMessage = useCallback(async (
    content: string,
    persona: string,
    ephemeral?: boolean,
    multimodalContent?: MessageContent,
    projectId?: string,
    tags?: TagChip[],
    // P1 #10 Phase 3: when set, skip the optimistic user message + POST
    // and resume an existing stream via Last-Event-ID instead. The
    // content/persona/etc args are ignored on the resume path; the
    // entire event handler downstream is identical.
    resumeOpts?: { streamId: string; lastEventId?: string },
  ) => {
    setError(null);

    if (!resumeOpts) {
      const userMessage: Message = {
        id: `temp-${Date.now()}`,
        role: 'user',
        content,
        created_at: new Date().toISOString(),
      };
      setMessages(prev => [...prev, userMessage]);
    }
    setStreaming({ ...INITIAL_STREAMING, phase: 'thinking' });

    const abort = new AbortController();
    abortRef.current = abort;

    const toolCalls: ToolCall[] = [];
    const delegations: Delegation[] = [];
    let thinkingText = '';
    let contentText = '';
    let ragCtx: RagContext | null = null;
    let activeAgent: AgentState | null = null;
    let clarification: Clarification | null = null;

    const handleEvent = (event: SSEEvent) => {
      // Any event other than `retrying` itself means the backend has
      // resumed forward progress — clear the "reconnecting" indicator.
      if (event.type !== 'retrying') {
        setStreaming(s => (s.retrying ? { ...s, retrying: null } : s));
      }
      // Same pattern for the SSE-level reconnect indicator (P1 #10):
      // any other event means a fresh connection is delivering data.
      if (event.type !== 'reconnecting') {
        setStreaming(s => (s.reconnecting ? { ...s, reconnecting: null } : s));
      }
      switch (event.type) {
        case 'conversation':
          // May fire twice: first with null title, second with generated title
          // Skip for ephemeral chats — don't track the synthetic ID
          if (!ephemeral) {
            setConversationId(prev => prev || event.data.id);
          }
          break;
        case 'rag_context':
          ragCtx = event.data;
          setStreaming(s => ({ ...s, ragContext: event.data }));
          break;
        case 'thinking':
          thinkingText += event.data.content;
          setStreaming(s => ({ ...s, thinking: thinkingText, phase: 'thinking' }));
          break;
        case 'token':
          contentText += event.data.content;
          setStreaming(s => ({ ...s, content: contentText, phase: 'generating' }));
          break;
        case 'tool_call':
          toolCalls.push({
            id: event.data.id,
            name: event.data.name,
            arguments: event.data.arguments,
          });
          setStreaming(s => ({ ...s, toolCalls: [...toolCalls], phase: 'tool_call' }));
          break;
        case 'tool_result': {
          const tc = toolCalls.find(t => t.id === event.data.id);
          if (tc) {
            tc.result = event.data.result;
            tc.duration_ms = event.data.duration_ms;
          }
          setStreaming(s => ({ ...s, toolCalls: [...toolCalls] }));
          break;
        }
        case 'clarification':
          // Discard any in-progress prose — the backend retried with
          // forced tool_choice after detecting a prose clarification,
          // so the streamed tokens are stale draft text.
          contentText = '';
          clarification = event.data;
          setStreaming(s => ({ ...s, content: '', clarification: event.data }));
          break;
        case 'artifact_created':
          setArtifacts(prev => {
            const exists = prev.find(a => a.id === event.data.id);
            if (exists) return prev;
            return [{
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
            }, ...prev];
          });
          setLastArtifactEvent(event.data);
          break;
        case 'artifact_updated':
          setArtifacts(prev => prev.map(a =>
            a.id === event.data.id
              ? { ...a, title: event.data.title, latest_version: event.data.version, updated_at: new Date().toISOString() }
              : a
          ));
          setLastArtifactEvent(event.data);
          break;
        case 'agent_start': {
          activeAgent = {
            name: event.data.agent,
            query: event.data.query,
            thinking: '',
            toolCalls: [],
          };
          // Attach to the last invoke_agent tool call
          const parentTc = [...toolCalls].reverse().find(t => t.name === 'invoke_agent');
          if (parentTc) parentTc.agent = activeAgent;
          setStreaming(s => ({ ...s, toolCalls: [...toolCalls] }));
          break;
        }
        case 'agent_thinking':
          if (activeAgent) {
            activeAgent.thinking += event.data.content;
            setStreaming(s => ({ ...s, toolCalls: [...toolCalls] }));
          }
          break;
        case 'agent_tool_call':
          if (activeAgent) {
            activeAgent.toolCalls.push({
              id: event.data.id,
              name: event.data.name,
              arguments: event.data.arguments,
            });
            setStreaming(s => ({ ...s, toolCalls: [...toolCalls] }));
          }
          break;
        case 'agent_tool_result': {
          if (activeAgent) {
            const atc = activeAgent.toolCalls.find(t => t.id === event.data.id);
            if (atc) {
              atc.result = event.data.result;
              atc.duration_ms = event.data.duration_ms;
            }
            setStreaming(s => ({ ...s, toolCalls: [...toolCalls] }));
          }
          break;
        }
        case 'agent_done':
          if (activeAgent) {
            activeAgent.stoppedReason = event.data.stopped_reason;
            activeAgent.durationSeconds = event.data.duration_seconds;
            activeAgent = null;
            setStreaming(s => ({ ...s, toolCalls: [...toolCalls] }));
          }
          break;
        case 'delegated':
          // Persona handoff. Tokens streamed before this event came from the
          // source persona; tokens after this come from the delegated one. We
          // keep them concatenated and let the UI render the handoff marker
          // between TaskLog and content.
          delegations.push(event.data);
          setStreaming(s => ({ ...s, delegations: [...delegations], phase: 'thinking' }));
          break;
        case 'persona_changed':
          // Backend persisted the new persona for this conversation. Sync local state.
          setConversationPersona(event.data.persona);
          break;
        case 'retrying':
          // A vLLM call hit a transient error and is about to retry. Show a
          // "reconnecting" indicator until any other event arrives.
          setStreaming(s => ({
            ...s,
            retrying: {
              attempt: event.data.attempt,
              maxAttempts: event.data.max_attempts,
              reason: event.data.reason,
            },
          }));
          break;
        case 'reconnecting':
          // SSE connection to the server dropped; the client is
          // resuming via Last-Event-ID. The in-flight turn keeps
          // running server-side during the grace window (P1 #10).
          setStreaming(s => ({
            ...s,
            reconnecting: {
              attempt: event.data.attempt,
              maxAttempts: event.data.max_attempts,
            },
          }));
          break;
        case 'error': {
          // Save-always parity with the backend (chat 3951063c,
          // 2026-05-08): append a Message carrying whatever partial
          // state we accumulated, with the same `_(stream
          // interrupted: <reason>)_` marker the backend's
          // apply_stream_error_marker produces. Without this, the
          // user would see the streamed prose, hit the error, and
          // watch the entire bubble vanish (MessageList gates the
          // streaming view on phase !== 'error'). The error banner
          // still shows for top-level visibility; the bubble
          // preserves the partial content + marker inline.
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
            tool_calls: toolCalls.length > 0 ? toolCalls : null,
            rag_context: ragCtx,
            clarification: clarification,
            delegations: delegations.length > 0 ? [...delegations] : null,
            interrupted: true,
            created_at: new Date().toISOString(),
          };
          setMessages(prev => [...prev, interruptedMessage]);
          setError(event.data.message);
          setStreaming(INITIAL_STREAMING);
          break;
        }
        case 'done': {
          const assistantMessage: Message = {
            id: `msg-${Date.now()}`,
            role: 'assistant',
            content: contentText,
            thinking: thinkingText || null,
            tool_calls: toolCalls.length > 0 ? toolCalls : null,
            rag_context: ragCtx,
            clarification: clarification,
            delegations: delegations.length > 0 ? [...delegations] : null,
            created_at: new Date().toISOString(),
          };
          setMessages(prev => [...prev, assistantMessage]);
          setStreaming(INITIAL_STREAMING);
          break;
        }
      }
    };

    // Build request body
    // Ephemeral: send full history (stateless). Persistent: only last message.
    // multimodalContent is used for the last user message when images are attached.
    const lastContent: MessageContent = multimodalContent || content;
    let chatMessages: { role: string; content: MessageContent }[];
    if (ephemeral) {
      const prevMessages = messagesRef.current;
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
            conversation_id: ephemeral ? undefined : conversationId,
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
        // Network-level failure (DNS, connection drop, non-streaming
        // 5xx). Preserve whatever partial state we accumulated as an
        // interrupted Message so the user does not lose the prose
        // they already saw, mirroring the SSE 'error' branch above.
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
          delegations: delegations.length > 0 ? [...delegations] : null,
          interrupted: true,
          created_at: new Date().toISOString(),
        };
        setMessages(prev => [...prev, interruptedMessage]);
        setError(reason);
        setStreaming(INITIAL_STREAMING);
      }
    } finally {
      abortRef.current = null;
    }
  }, [conversationId]);

  // P1 #10 Phase 3 — on mount, check sessionStorage for an active SSE
  // stream and resume it. This is the cross-browser-refresh case:
  // refreshing the chat tab mid-stream re-attaches to the in-flight
  // turn (provided the server hasn't yet evicted it past the grace
  // window). Runs once per mount; if there's no active stream the
  // effect is a no-op.
  useEffect(() => {
    const active = readActiveStream();
    if (!active) return;
    let cancelled = false;
    (async () => {
      try {
        await loadConversation(active.conversation_id);
      } catch {
        // The conversation may not exist yet (very fresh stream); the
        // resume will surface a 410 if the server has also lost it.
      }
      if (cancelled) return;
      await sendMessage(
        '', '', false, undefined, undefined, undefined,
        { streamId: active.stream_id, lastEventId: active.last_event_id },
      );
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // mount-only

  const stopGenerating = useCallback(() => {
    abortRef.current?.abort();
    setStreaming(s => ({ ...s, phase: 'done' }));
  }, []);

  return {
    messages,
    conversationId,
    conversationPersona,
    streaming,
    error,
    artifacts,
    setArtifacts,
    lastArtifactEvent,
    sendMessage,
    loadConversation,
    clearConversation,
    stopGenerating,
  };
}
