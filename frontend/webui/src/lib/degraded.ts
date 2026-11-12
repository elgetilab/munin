import type { SystemStatus } from './types';

/**
 * The notice to show when Munin is answering in a degraded way
 * (docs/ops/HAIKU-FALLBACK.md), or null:
 *
 * - `backend.mode === 'reduced'`: the VPS gateway serves from its standby
 *   because the cluster is unreachable. Older chats are not visible there.
 * - `llm_fallback.active`: the cluster's own model is down and a hosted Claude
 *   model answers. Chat works, but messages now leave the cluster, which users
 *   are entitled to know.
 *
 * Reduced mode wins: it already runs on a hosted model, so one notice is enough.
 */
export function degradedMessage(status: SystemStatus | null | undefined): string | null {
  if (!status) return null;
  if (status.backend?.mode === 'reduced') {
    return status.backend.history_note
      || 'Reduced mode: the main Munin server cannot be reached. Older chats are not shown until it returns.';
  }
  if (status.llm_fallback?.active) {
    const model = status.llm_fallback.model || 'a hosted model';
    return `The local model is offline, so ${model} (Anthropic API) is answering for now. `
      + 'Your messages are sent to Anthropic while this notice is shown. Paper search still works.';
  }
  return null;
}
