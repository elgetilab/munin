/**
 * Copies text to the clipboard. Resolves to true only if the browser accepted
 * it, so a caller says "Copied" on true and something else on false. Never
 * rejects.
 *
 * It resolves to false when permission is refused, and when the Clipboard API
 * doesn't exist, as on a page served over plain HTTP.
 *
 *   if (await copyText(key)) setCopied(true); else setCopyFailed(true);
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
