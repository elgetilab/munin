/**
 * Plain wording for errors about the model server. The raw text names internals
 * (vLLM, upstream status codes), which mean nothing to the people reading it.
 */

const MODEL_SERVER_ERROR = /\bvllm\b|\bupstream\b|\b50[234]\b/i;

/** The message to show for an error. Anything not about the model server is kept as it is. */
export function friendlyError(raw: string): string {
  return MODEL_SERVER_ERROR.test(raw) ? 'The model is unavailable right now' : raw;
}

// The marker the backend and chatStore both append to a cut-off answer, which is
// also stored in saved conversations, so it is reworded where it is shown.
const INTERRUPTED_MARKER = /_\(stream interrupted: ([\s\S]*?)\)_\s*$/;

/** Rewords a trailing `_(stream interrupted: reason)_` marker in message content. */
export function rewordInterruptedMarker(content: string): string {
  return content.replace(
    INTERRUPTED_MARKER,
    (_, reason: string) => `_(This answer was cut off. ${friendlyError(reason)}.)_`,
  );
}
