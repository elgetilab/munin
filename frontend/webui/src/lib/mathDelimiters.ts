/**
 * Normalize LaTeX-style math delimiters to the dollar-sign form that
 * `remark-math` understands.
 *
 * The chat persona emits math two different ways in the same response
 * (chat b4813f40, 2026-06-03 — Schrödinger Gleichung):
 *   - Display: `$$...$$`  (remark-math handles this)
 *   - Inline:  `\(...\)`  (remark-math does NOT handle this — it
 *                          renders as literal `\(i\)` text)
 *
 * Rather than rely on prompt engineering to make the model always pick
 * `$...$`, we normalise here. We rewrite `\(x\)` -> `$x$` and
 * `\[x\]` -> `$$x$$` BEFORE handing the text to remark-math, but only
 * inside non-code regions so that a code sample containing the literal
 * sequence `\(foo\)` stays intact.
 *
 * Code regions = fenced blocks (``` ... ```) AND inline code (` ... `).
 * Anything between those gets the regex pass; everything inside is
 * passed through untouched.
 *
 * Exported for testability.
 */
export function normalizeMathDelimiters(input: string): string {
  // Split on fenced code blocks first, then on inline-code spans within
  // the surviving non-code segments. Tokens at odd indices are code
  // (preserved as-is); tokens at even indices are prose that gets the
  // delimiter rewrite.
  const fencedSplit = input.split(/(```[\s\S]*?```)/g);
  const out: string[] = [];
  for (let i = 0; i < fencedSplit.length; i++) {
    const seg = fencedSplit[i];
    if (i % 2 === 1) {
      // Fenced code block: keep verbatim.
      out.push(seg);
      continue;
    }
    // Within this prose segment, also protect inline-code spans.
    const inlineSplit = seg.split(/(`[^`\n]*`)/g);
    const rewritten = inlineSplit.map((part, j) => {
      if (j % 2 === 1) return part; // inline code: keep verbatim
      return part
        // `\[...\]` -> `$$...$$` (display). Lazy + dotall via [\s\S]
        // so multi-line display blocks match; the lazy quantifier
        // prevents one `\[...\]` from gobbling text up to a later
        // `\]` somewhere else in the document.
        .replace(/\\\[([\s\S]+?)\\\]/g, '$$$$$1$$$$')
        // `\(...\)` -> `$...$` (inline). Same shape but inline math
        // must stay on a single line.
        .replace(/\\\((.+?)\\\)/g, '$$$1$$');
    });
    out.push(rewritten.join(''));
  }
  return out.join('');
}
