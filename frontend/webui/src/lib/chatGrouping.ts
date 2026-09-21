/**
 * Buckets conversations into the sidebar's date groups.
 *
 * Lives here rather than beside the component that uses it because a
 * module that exports both a component and a plain function breaks React
 * Fast Refresh (react-refresh/only-export-components). It is a pure
 * function with its own tests, so a lib module is where it belongs.
 */

import type { ConversationSummary } from './types';


export function groupByTime(chats: ConversationSummary[]) {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today.getTime() - 86400000);
  const weekAgo = new Date(today.getTime() - 7 * 86400000);
  const monthAgo = new Date(today.getTime() - 30 * 86400000);

  const groups: { label: string; chats: ConversationSummary[] }[] = [
    { label: 'Pinned', chats: [] },
    { label: 'Today', chats: [] },
    { label: 'Yesterday', chats: [] },
    { label: 'This week', chats: [] },
    { label: 'This month', chats: [] },
    { label: 'Older', chats: [] },
  ];

  for (const chat of chats) {
    if (chat.pinned) {
      groups[0].chats.push(chat);
      continue;
    }
    const d = new Date(chat.updated_at);
    if (d >= today) groups[1].chats.push(chat);
    else if (d >= yesterday) groups[2].chats.push(chat);
    else if (d >= weekAgo) groups[3].chats.push(chat);
    else if (d >= monthAgo) groups[4].chats.push(chat);
    else groups[5].chats.push(chat);
  }

  return groups.filter(g => g.chats.length > 0);
}
