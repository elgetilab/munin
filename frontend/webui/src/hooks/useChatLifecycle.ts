/**
 * Mount-time chat lifecycle effect (P2 #26 Migration commit 4).
 *
 * The Zustand chatStore can't hold React effects, but a couple of
 * lifecycle pieces still need a real `useEffect` somewhere — at the
 * moment, just the P1 #10 Phase 3 SSE resume that checks
 * sessionStorage for an active stream and reconnects to it. App.tsx
 * calls this hook once at mount.
 *
 * Before commit 4 this effect lived inside a much larger
 * `useChat()` wrapper that also re-exposed every store slice as a
 * destructured tuple. The wrapper has been retired; only the effect
 * remains, on its own.
 */

import { useEffect } from 'react';

import { readActiveStream } from '../lib/api';
import { useChatStore } from '../stores/chatStore';


export function useChatLifecycle(): void {
  // P1 #10 Phase 3 — on mount, check sessionStorage for an active
  // SSE stream and resume it. Cross-browser-refresh case: refreshing
  // the chat tab mid-stream re-attaches to the in-flight turn
  // (provided the server hasn't yet evicted it past the grace
  // window). Runs once per mount; no-op when there's no active
  // stream.
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
}
