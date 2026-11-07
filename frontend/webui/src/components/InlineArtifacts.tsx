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
  isWidget,
  parseProteinSpec,
  svgDataUrl,
  type ArtifactRef,
  type ProteinSpec,
} from '../lib/artifactDisplay';
import { useUiStore } from '../stores/uiStore';
import { ProteinViewer } from './ProteinViewer';

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
      {refs.map(ref => {
        if (isWidget(ref)) return <ArtifactWidget key={ref.id} artifact={ref} conversationId={conversationId} />;
        if (isInlinePreview(ref)) return <ArtifactPreview key={ref.id} artifact={ref} conversationId={conversationId} />;
        return <ArtifactCard key={ref.id} artifact={ref} />;
      })}
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

function ArtifactWidget({ artifact, conversationId }: { artifact: ArtifactRef; conversationId?: string | null }) {
  const open = useOpenArtifact(artifact.id);
  const [spec, setSpec] = useState<ProteinSpec | null>(null);
  const [failed, setFailed] = useState(false);
  const { id, version } = artifact;

  useEffect(() => {
    if (!conversationId) return;
    let cancelled = false;
    fetchArtifact(conversationId, id, version)
      .then(full => {
        if (cancelled) return;
        const parsed = parseProteinSpec(full.content);
        if (parsed) setSpec(parsed);
        else setFailed(true);
      })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, [conversationId, id, version]);

  if (failed) return <ArtifactCard artifact={artifact} note="preview unavailable" />;
  if (!spec || !conversationId) return <PreviewLoading />;
  return (
    <ProteinViewer
      spec={spec}
      title={artifact.title}
      conversationId={conversationId}
      artifactId={id}
      version={version}
      onExpand={open}
    />
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
  if (!src) return <PreviewLoading />;
  return (
    <button
      type="button"
      data-testid="artifact-preview"
      onClick={open}
      title={`Open ${artifact.title}`}
      aria-label={`Open ${artifact.title}`}
      className="block max-w-full rounded-lg overflow-hidden border border-border hover:border-accent transition-colors cursor-pointer bg-bg-secondary text-left"
    >
      <img
        src={src}
        alt={artifact.title}
        loading="lazy"
        onError={() => setFailed(true)}
        className="block max-w-full max-h-96 object-contain bg-white"
      />
      {/* w-0 min-w-full: a long name truncates to the image's width rather than widening it. */}
      <span data-testid="artifact-preview-caption" className="flex items-center gap-3 w-0 min-w-full px-3 py-1.5 border-t border-border">
        <span className="min-w-0 flex-1 text-[11px] text-text-secondary truncate">{artifact.title}</span>
        <span className="shrink-0 text-[11px] text-accent">Open</span>
      </span>
    </button>
  );
}

function PreviewLoading() {
  return (
    <div
      data-testid="artifact-preview-loading"
      role="status"
      aria-busy="true"
      className="h-32 max-w-md flex items-center justify-center rounded-lg bg-bg-secondary border border-border text-xs text-text-secondary"
    >
      Loading preview...
    </div>
  );
}
