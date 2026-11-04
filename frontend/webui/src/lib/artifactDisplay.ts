// What the chat shows for an artifact, worked out from the tool calls of the
// turn that made it. Tool results are persisted with the message, so the same
// refs come back after a reload without a separate link between the two.
import type { ToolCall } from './types';

export interface ArtifactRef {
  id: string;
  title: string;
  content_type: string;
  source: 'model_written' | 'sandbox_generated';
  version?: number;
  size_bytes?: number;
  external_url?: string;
}

// Above this an image gets a card, so one huge plot cannot stall the transcript.
export const MAX_INLINE_PREVIEW_BYTES = 5 * 1024 * 1024;

const INLINE_IMAGE_TYPES = new Set([
  'image/png',
  'image/jpeg',
  'image/webp',
  'image/gif',
  'image/svg+xml',
]);

const CONTENT_TYPE_ICONS: Record<string, string> = {
  'text/markdown': '\u{1F4DD}',
  'text/latex': '\u{1F4C4}',
  'text/plain': '\u{1F4C3}',
  'application/python': '\u{1F40D}',
  'application/json': '{}',
  'image/svg+xml': '\u{1F5BC}',
  'image/png': '\u{1F5BC}',
  'image/jpeg': '\u{1F5BC}',
  'application/pdf': '\u{1F4C4}',
};

const CONTENT_TYPE_LABELS: Record<string, string> = {
  'text/markdown': 'Document',
  'text/latex': 'LaTeX',
  'text/plain': 'Text',
  'text/html': 'HTML',
  'text/csv': 'CSV',
  'application/python': 'Python',
  'application/json': 'JSON',
  'application/pdf': 'PDF',
  'image/svg+xml': 'SVG',
  'image/png': 'Image',
  'image/jpeg': 'Image',
  'image/webp': 'Image',
  'image/gif': 'Image',
};

export function getContentTypeIcon(ct: string): string {
  return CONTENT_TYPE_ICONS[ct] || '\u{1F4C4}';
}

export function getContentTypeLabel(ct: string): string {
  return CONTENT_TYPE_LABELS[ct] || 'File';
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function isInlinePreview(ref: ArtifactRef): boolean {
  if (!INLINE_IMAGE_TYPES.has(ref.content_type)) return false;
  // Only a model-written SVG has its source in the store rather than a URL.
  if (!ref.external_url && ref.content_type !== 'image/svg+xml') return false;
  return (ref.size_bytes ?? 0) <= MAX_INLINE_PREVIEW_BYTES;
}

// An <img> does not run an SVG's scripts or load anything it references, so
// model-written source is safe to show this way.
export function svgDataUrl(svg: string): string {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function refsFromResult(name: string, result: unknown): ArtifactRef[] {
  if (!isRecord(result)) return [];

  if (name === 'create_artifact' || name === 'update_artifact') {
    if (result.error || typeof result.id !== 'string') return [];
    return [{
      id: result.id,
      title: typeof result.title === 'string' ? result.title : 'Untitled',
      content_type: typeof result.content_type === 'string' ? result.content_type : 'text/plain',
      source: 'model_written',
      version: typeof result.version === 'number' ? result.version : undefined,
    }];
  }

  // run_python, edit_python, compile_latex and compute all return registered
  // sandbox files in this shape. list_artifacts also has an `artifacts` key,
  // but its rows have no registered_artifact_id and are not new to this turn.
  if (!Array.isArray(result.artifacts)) return [];
  return result.artifacts.flatMap((art: unknown): ArtifactRef[] => {
    if (!isRecord(art) || typeof art.registered_artifact_id !== 'string') return [];
    return [{
      id: art.registered_artifact_id,
      title: typeof art.filename === 'string' ? art.filename : 'unnamed',
      content_type: typeof art.content_type === 'string' ? art.content_type : 'application/octet-stream',
      source: 'sandbox_generated',
      size_bytes: typeof art.size_bytes === 'number' ? art.size_bytes : undefined,
      external_url: typeof art.external_url === 'string' ? art.external_url : undefined,
    }];
  });
}

/** One ref per artifact, in order of first appearance, at its newest version. */
export function artifactRefsFromToolCalls(toolCalls: ToolCall[] | null | undefined): ArtifactRef[] {
  const refs = new Map<string, ArtifactRef>();
  const walk = (calls: ToolCall[]) => {
    for (const call of calls) {
      for (const ref of refsFromResult(call.name, call.result)) refs.set(ref.id, ref);
      if (call.agent?.toolCalls) walk(call.agent.toolCalls);
    }
  };
  walk(toolCalls ?? []);
  return [...refs.values()];
}

/** True when the model already placed this image in its text with ![](url). */
export function isEmbeddedInText(ref: ArtifactRef, content: string): boolean {
  return !!ref.external_url && content.includes('![') && content.includes(`](${ref.external_url})`);
}
