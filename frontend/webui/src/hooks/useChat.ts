import { useState, useCallback, useRef } from 'react';
import { streamChat, fetchChat } from '../lib/api';
import type { Message, MessageContent, SSEEvent, ToolCall, RagContext, AgentState, Clarification, ArtifactSummary, ArtifactCreatedEvent, ArtifactUpdatedEvent, TagChip, Delegation } from '../lib/types';

interface StreamingState {
  content: string;
  thinking: string;
  toolCalls: ToolCall[];
  ragContext: RagContext | null;
  clarification: Clarification | null;
  delegations: Delegation[];
  phase: 'idle' | 'thinking' | 'tool_call' | 'generating' | 'done' | 'error';
}

const INITIAL_STREAMING: StreamingState = {
  content: '',
  thinking: '',
  toolCalls: [],
  ragContext: null,
  clarification: null,
  delegations: [],
  phase: 'idle',
};

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

  const sendMessage = useCallback(async (content: string, persona: string, ephemeral?: boolean, multimodalContent?: MessageContent, projectId?: string, tags?: TagChip[]) => {
    setError(null);

    const userMessage: Message = {
      id: `temp-${Date.now()}`,
      role: 'user',
      content,
      created_at: new Date().toISOString(),
    };
    setMessages(prev => [...prev, userMessage]);
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
        case 'error':
          setError(event.data.message);
          setStreaming(s => ({ ...s, phase: 'error' }));
          break;
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
    } catch (e) {
      if ((e as Error).name !== 'AbortError') {
        setError(e instanceof Error ? e.message : 'Stream failed');
        setStreaming(s => ({ ...s, phase: 'error' }));
      }
    } finally {
      abortRef.current = null;
    }
  }, [conversationId]);

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
