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

export const WIDGET_CONTENT_TYPE = 'application/vnd.munin.widget+json';

export const PROTEIN_STYLES = [
  { value: 'cartoon', label: 'Cartoon' },
  { value: 'stick', label: 'Sticks' },
  { value: 'sphere', label: 'Spheres' },
  { value: 'line', label: 'Lines' },
] as const;

export const PROTEIN_COLORS = [
  { value: 'spectrum', label: 'Spectrum' },
  { value: 'chain', label: 'Chain' },
  { value: 'secondary', label: 'Secondary structure' },
  { value: 'bfactor', label: 'B-factor' },
] as const;

export type ProteinStyle = typeof PROTEIN_STYLES[number]['value'];
export type ProteinColor = typeof PROTEIN_COLORS[number]['value'];

export interface ProteinSpec {
  widget: 'protein';
  source: 'pdb' | 'alphafold';
  id: string;
  style: ProteinStyle;
  color: ProteinColor;
  highlight: { chain?: string; residues: number[] }[];
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
  [WIDGET_CONTENT_TYPE]: '\u{1F9EC}',
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
  [WIDGET_CONTENT_TYPE]: '3D structure',
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

export function isWidget(ref: { content_type: string }): boolean {
  return ref.content_type === WIDGET_CONTENT_TYPE;
}

// What artifact_store.register_sandbox_artifact stores when the bytes live in
// the sandbox: a sentence for the model, not the file.
const SANDBOX_PLACEHOLDER_PREFIX = '[Sandbox-generated file:';

/** True when an artifact's body only points at a file held elsewhere. */
export function isFilePlaceholder(artifact: { source?: string; content?: string | null }): boolean {
  const body = artifact.content ?? '';
  return body.startsWith(SANDBOX_PLACEHOLDER_PREFIX)
    || (artifact.source === 'sandbox_generated' && !body.trim());
}

/** A sandbox file's size. Its record has none, but the placeholder states it. */
export function fileSizeBytes(artifact: { size_bytes?: number; content?: string | null }): number | null {
  if (artifact.size_bytes) return artifact.size_bytes;
  const stated = artifact.content?.match(/^\[Sandbox-generated file:.*, (\d+) bytes\)/s);
  return stated ? Number(stated[1]) : null;
}

// The model can rewrite a spec with update_artifact, so a stored one is checked
// before use, and anything unknown falls back rather than reaching the viewer.
export function parseProteinSpec(content: string): ProteinSpec | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(content);
  } catch {
    return null;
  }
  if (!isRecord(parsed) || parsed.widget !== 'protein') return null;
  if ((parsed.source !== 'pdb' && parsed.source !== 'alphafold') || typeof parsed.id !== 'string') return null;
  const style = PROTEIN_STYLES.find(s => s.value === parsed.style)?.value ?? 'cartoon';
  const color = PROTEIN_COLORS.find(c => c.value === parsed.color)?.value ?? 'spectrum';
  const highlight = (Array.isArray(parsed.highlight) ? parsed.highlight : []).flatMap((item: unknown) => {
    if (!isRecord(item) || !Array.isArray(item.residues)) return [];
    const residues = item.residues.filter((r: unknown): r is number => Number.isInteger(r));
    if (residues.length === 0) return [];
    return [typeof item.chain === 'string' ? { chain: item.chain, residues } : { residues }];
  });
  return { widget: 'protein', source: parsed.source, id: parsed.id, style, color, highlight };
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

  // design_primers returns the artifact record inside its envelope, the same
  // keys create_artifact does, so one widget artifact comes back per call.
  if (name === 'create_artifact' || name === 'update_artifact' || name === 'show_protein' || name === 'design_primers') {
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
  for (const call of toolCalls ?? []) {
    for (const ref of refsFromResult(call.name, call.result)) refs.set(ref.id, ref);
  }
  return [...refs.values()];
}

/** True when the model already placed this image in its text with ![](url). */
export function isEmbeddedInText(ref: ArtifactRef, content: string): boolean {
  return !!ref.external_url && content.includes('![') && content.includes(`](${ref.external_url})`);
}
