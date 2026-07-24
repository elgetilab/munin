/**
 * Mount-time chat lifecycle effect (P2 #26 Migration commit 4).
 *
 * The Zustand chatStore can't hold React effects, but a couple of
 * lifecycle pieces still need a real `useEffect` somewhere — at the
 * moment, just the background-turns mount resume. App.tsx calls this
 * hook once at mount.
 *
 * Background turns: the localStorage pointer written during a live
 * stream is only a navigation hint — "this conversation had a turn in
 * flight". The server's `active_stream` field on the conversation is
 * the source of truth, so all this effect does is load the pointed-at
 * conversation; `loadConversation` re-attaches if the server reports
 * the stream is still live, and otherwise the transcript already
 * carries the completed (or save-always partial) answer. This replaces
 * the earlier P1 #10 Phase 3 shape that resumed straight from the
 * stored stream_id + last_event_id: resuming mid-log after a reload
 * left the final bubble missing everything before the checkpoint,
 * since the in-memory accumulators start empty.
 */

import { useEffect } from 'react';

import { readActiveStream } from '../lib/api';
import { useChatStore } from '../stores/chatStore';


export function useChatLifecycle(): void {
  useEffect(() => {
    const active = readActiveStream();
    if (!active) return;
    // The URL wins over the pointer: App.tsx's routing effect loads
    // /c/{id} (and /knowledge routes render no chat), and since the
    // pointer now survives in localStorage it may reference a
    // conversation other than the one the user deliberately opened.
    // Only navigate to the pending conversation from the root path;
    // everywhere else the sidebar's "generating" dot is the signal,
    // and opening that conversation re-attaches via loadConversation.
    if (window.location.pathname !== '/') return;
    // loadConversation handles the rest: re-attach when the server
    // reports a live stream, plain transcript render otherwise. A
    // 404 (conversation deleted since) just surfaces the normal
    // load error; the pointer is cleared when the stream ends or
    // the next turn starts, so we don't clear it here.
    void useChatStore.getState().loadConversation(active.conversation_id);
  }, []); // mount-only
}
