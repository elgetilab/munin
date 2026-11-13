// Retry and Edit send a user message again in place of a saved one
// (`replace_from` on /api/chat/completions), which drops it and everything
// after it on the server. Both need the message's saved id, and both resend
// text only.
import type { Message } from './types';

/** True for an id the server gave a stored row, not the client's stand-in
 *  for a message that has not come back from `done` yet. */
export function isSavedMessageId(id: string): boolean {
  return !id.startsWith('temp-') && !id.startsWith('msg-');
}

/** Whether a user message can be retried or edited: saved, and with nothing
 *  that sending its text again would lose (attachments, or a message that is
 *  only an attachment placeholder such as [Image]). */
export function canResend(message: Message): boolean {
  return message.role === 'user'
    && isSavedMessageId(message.id)
    && !(message.attachments && message.attachments.length > 0)
    && !/^\[.*\]$/.test(message.content.trim());
}
