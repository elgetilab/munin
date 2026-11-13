// The composer's mode chip. Auto lets the per-turn router pick a profile for
// each message; Chat, Code and Research force one by sending the message with
// its slash command, which the router treats as absolute
// (backend/retrieval/router.py, tier 1). A persona pin would only be a prior.

export type Mode = 'auto' | 'chat' | 'code' | 'research';

export const MODES: ReadonlyArray<{ id: Mode; label: string; description: string }> = [
  { id: 'auto', label: 'Auto', description: 'Munin picks the mode for each message' },
  { id: 'chat', label: 'Chat', description: 'Writing, web search, quick lookups, brainstorming' },
  { id: 'code', label: 'Code', description: 'Programming, scientific computing, debugging' },
  { id: 'research', label: 'Research', description: 'Literature search, paper analysis, citations' },
];

const SLASH = /^\/(research|code|chat)\b/i;

/** The mode a message forces with its leading slash command, if any. */
export function typedMode(text: string): Mode | null {
  const m = text.trimStart().match(SLASH);
  return m ? (m[1].toLowerCase() as Mode) : null;
}

/** A message as shown: its leading slash command taken off, and the mode
 *  that command forced (null when it had none). */
export function splitMode(text: string): { mode: Mode | null; text: string } {
  const mode = typedMode(text);
  return mode ? { mode, text: text.trimStart().replace(SLASH, '').trimStart() } : { mode: null, text };
}

/** The text to send: a forced mode leads with its slash command, unless the
 *  message already starts with one (a typed command wins). */
export function withMode(text: string, mode: Mode): string {
  if (mode === 'auto' || typedMode(text)) return text;
  return `/${mode} ${text}`;
}

/** The label for a reply's routed profile, or null for plain chat. */
export function routedLabel(profile?: string | null): string | null {
  if (!profile || profile === 'chat' || profile === 'munin') return null;
  return MODES.find(m => m.id === profile)?.label ?? profile[0].toUpperCase() + profile.slice(1);
}
