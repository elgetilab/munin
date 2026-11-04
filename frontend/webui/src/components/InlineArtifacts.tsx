import { useEffect, useState } from 'react';
import type { ToolCall } from '../lib/types';
import { fetchArtifact } from '../lib/api';
import {
  artifactRefsFromToolCalls,
  formatBytes,
  getContentTypeIcon,
  getContentTypeLabel,
  isEmbeddedInText,
  isInlinePreview,
  svgDataUrl,
  type ArtifactRef,
} from '../lib/artifactDisplay';
import { useUiStore } from '../stores/uiStore';

interface InlineArtifactsProps {
  toolCalls: ToolCall[] | null | undefined;
  // The message text, so an image the model already embedded is not shown twice.
  content?: string;
  conversationId?: string | null;
}

export function InlineArtifacts({ toolCalls, content = '', conversationId }: InlineArtifactsProps) {
  const refs = artifactRefsFromToolCalls(toolCalls).filter(ref => !isEmbeddedInText(ref, content));
  if (refs.length === 0) return null;
  return (
    <div data-testid="inline-artifacts" className="space-y-2">
      {refs.map(ref => (
        isInlinePreview(ref)
          ? <ArtifactPreview key={ref.id} artifact={ref} conversationId={conversationId} />
          : <ArtifactCard key={ref.id} artifact={ref} />
      ))}
    </div>
  );
}

// The panel does not show while the knowledge panel is open.
function useOpenArtifact(id: string) {
  const setSelectedArtifactId = useUiStore(s => s.setSelectedArtifactId);
  const setArtifactPanelOpen = useUiStore(s => s.setArtifactPanelOpen);
  const setKnowledgePanelOpen = useUiStore(s => s.setKnowledgePanelOpen);
  return () => {
    setKnowledgePanelOpen(false);
    setSelectedArtifactId(id);
    setArtifactPanelOpen(true);
  };
}

function ArtifactCard({ artifact, note }: { artifact: ArtifactRef; note?: string }) {
  const open = useOpenArtifact(artifact.id);
  const details = [
    getContentTypeLabel(artifact.content_type),
    artifact.version && artifact.version > 1 ? `v${artifact.version}` : null,
    artifact.size_bytes ? formatBytes(artifact.size_bytes) : null,
    note,
  ].filter(Boolean).join(' · ');
  return (
    <button
      type="button"
      data-testid="artifact-card"
      onClick={open}
      className="w-full max-w-md flex items-center gap-3 px-3 py-2.5 text-left rounded-lg bg-bg-secondary border border-border hover:border-accent transition-colors cursor-pointer"
    >
      <span className="text-lg shrink-0" aria-hidden="true">{getContentTypeIcon(artifact.content_type)}</span>
      <span className="min-w-0 flex-1">
        <span className="block text-sm text-text-primary truncate">{artifact.title}</span>
        <span className="block text-[11px] text-text-secondary">{details}</span>
      </span>
    </button>
  );
}

function ArtifactPreview({ artifact, conversationId }: { artifact: ArtifactRef; conversationId?: string | null }) {
  const open = useOpenArtifact(artifact.id);
  const [source, setSource] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const { id, external_url: url, version } = artifact;

  // A model-written SVG is stored as text, so it has no URL to point an <img> at.
  useEffect(() => {
    if (url || !conversationId) return;
    let cancelled = false;
    fetchArtifact(conversationId, id, version)
      .then(full => { if (!cancelled) setSource(full.content); })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, [url, conversationId, id, version]);

  const src = url ?? (source !== null ? svgDataUrl(source) : null);
  // The message outlives the sandbox's copy of the file, which is gone after a
  // reset and, on a lite host, after a restart.
  if (failed) return <ArtifactCard artifact={artifact} note="preview unavailable" />;
  if (!src) return <div data-testid="artifact-preview-loading" className="h-32 max-w-md rounded-lg bg-bg-secondary border border-border" />;
  return (
    <button
      type="button"
      data-testid="artifact-preview"
      onClick={open}
      title={`Open ${artifact.title}`}
      className="block max-w-full rounded-lg overflow-hidden border border-border hover:border-accent transition-colors cursor-pointer bg-white"
    >
      <img
        src={src}
        alt={artifact.title}
        loading="lazy"
        onError={() => setFailed(true)}
        className="block max-w-full max-h-96 object-contain"
      />
    </button>
  );
}
