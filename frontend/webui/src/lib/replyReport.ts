// Reporting one reply: the report saves the whole conversation, so its reason
// starts by naming the reply. A reply that has just streamed in carries only a
// temporary id, so it is named by position and opening words instead.

/** The opening of a reply as plain text, to recognise it by in a report. */
export function replyExcerpt(markdown: string, max = 100): string {
  const plain = markdown.replace(/```[\s\S]*?```/g, ' ').replace(/[#*_`>|[\]]/g, '').replace(/\s+/g, ' ').trim();
  return plain.length > max ? `${plain.slice(0, max).trimEnd()}…` : plain;
}

/** What a report about one reply puts first in its reason. */
export function replyReportPrefix(target: { number: number; excerpt: string }): string {
  return `About reply ${target.number}: "${target.excerpt}"\n\n`;
}
