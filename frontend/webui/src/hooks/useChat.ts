/**
 * useChat — transitional wrapper around the Zustand chat store
 * (P2 #26 Migration commit 1).
 *
 * Subscribes to every reactive slice the store exposes, pulls the
 * stable action references, and returns the destructured tuple
 * that App.tsx + the ~30 existing tests in useChat.test.ts +
 * useChat.error.test.ts expect. This lets the migration land in
 * safe passes — consumers and tests don't need to change shape
 * until commits 2-4 do the prop-drilling cleanup.
 *
 * The mount-time SSE resume effect (P1 #10) lives here, not in
 * the store, because React effects can't live inside Zustand
 * stores. The store provides the actions; this hook hosts the
 * `useEffect` that calls them at mount.
 *
 * Retired in commit 4 once every consumer is on direct
 * `useChatStore` access.
 */

import { useEffect } from 'react';

import { readActiveStream } from '../lib/api';
import { useChatStore } from '../stores/chatStore';

// Re-export so consumers that referenced these via this file keep
// working without a path update. Internal store-shape changes can
// happen without churning every import site.
export type { StreamingState } from '../stores/chatStore';
export { INITIAL_STREAMING } from '../stores/chatStore';

export function useChat() {
  // Reactive slices — re-render when the selected slice changes.
  const messages = useChatStore(s => s.messages);
  const conversationId = useChatStore(s => s.conversationId);
  const conversationPersona = useChatStore(s => s.conversationPersona);
  const streaming = useChatStore(s => s.streaming);
  const error = useChatStore(s => s.error);
  const artifacts = useChatStore(s => s.artifacts);
  const lastArtifactEvent = useChatStore(s => s.lastArtifactEvent);

  // Action references — stable across re-renders (Zustand creates
  // each action once at store creation time).
  const setArtifacts = useChatStore(s => s.setArtifacts);
  const sendMessage = useChatStore(s => s.sendMessage);
  const loadConversation = useChatStore(s => s.loadConversation);
  const clearConversation = useChatStore(s => s.clearConversation);
  const stopGenerating = useChatStore(s => s.stopGenerating);
  const dismissMemoryProposal = useChatStore(s => s.dismissMemoryProposal);

  // P1 #10 Phase 3 — on mount, check sessionStorage for an active
  // SSE stream and resume it. This is the cross-browser-refresh
  // case: refreshing the chat tab mid-stream re-attaches to the
  // in-flight turn (provided the server hasn't yet evicted it past
  // the grace window). Runs once per mount; if there's no active
  // stream the effect is a no-op.
  useEffect(() => {
    const active = readActiveStream();
    if (!active) return;
    let cancelled = false;
    (async () => {
      try {
        await useChatStore.getState().loadConversation(active.conversation_id);
      } catch {
        // The conversation may not exist yet (very fresh stream);
        // the resume will surface a 410 if the server has also
        // lost it.
      }
      if (cancelled) return;
      await useChatStore.getState().sendMessage(
        '', '', false, undefined, undefined, undefined,
        { streamId: active.stream_id, lastEventId: active.last_event_id },
      );
    })();
    return () => { cancelled = true; };
  }, []); // mount-only

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
    dismissMemoryProposal,
  };
}
