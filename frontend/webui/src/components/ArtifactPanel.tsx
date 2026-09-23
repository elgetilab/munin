import { useState, useEffect, useCallback, useRef } from 'react';
import type { ArtifactSummary, ArtifactFull } from '../lib/types';
import { fetchArtifact, updateArtifact } from '../lib/api';
import { getExtension } from '../lib/artifactDownload';
import { printArtifactAsPdf } from '../lib/printPdf';
import { Markdown } from './Markdown';
import { PrismLight as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { useUiStore } from '../stores/uiStore';

// P2 #26 commit 2: onClose / selectedArtifactId / onSelectArtifact
// dropped from props — all three are pulled directly from uiStore.
// `artifacts` + `conversationId` stay on props because they're
// chat-domain state (the parent already has them in hand from the
// useChat hook, no benefit to re-subscribing inside this component).
interface ArtifactPanelProps {
  artifacts: ArtifactSummary[];
  conversationId: string;
}

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

function getContentTypeIcon(ct: string): string {
  return CONTENT_TYPE_ICONS[ct] || '\u{1F4C4}';
}

function getLanguageForHighlight(artifact: ArtifactFull): string | null {
  if (artifact.language) return artifact.language;
  const ct = artifact.content_type;
  if (ct === 'application/python') return 'python';
  if (ct === 'application/json') return 'json';
  if (ct === 'text/latex') return 'latex';
  if (ct === 'image/svg+xml') return 'xml';
  return null;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function ArtifactPanel({ artifacts, conversationId }: ArtifactPanelProps) {
  const selectedArtifactId = useUiStore(s => s.selectedArtifactId);
  const onSelectArtifact = useUiStore(s => s.setSelectedArtifactId);
  const setArtifactPanelOpen = useUiStore(s => s.setArtifactPanelOpen);
  const onClose = () => setArtifactPanelOpen(false);
  // Everything below is scoped to the artifact it belongs to and then
  // derived, rather than stored loose and reset by an effect when the
  // selection changes. The old version needed three synchronous setStates in
  // its selection effect to keep these honest; tying each one to an id means
  // switching artifacts invalidates them on its own.
  const [fetched, setFetched] = useState<ArtifactFull | null>(null);
  const loadedArtifact = fetched && fetched.id === selectedArtifactId ? fetched : null;

  // The version picked from the dropdown. Absent means "whatever is latest".
  const [pinnedVersion, setPinnedVersion] = useState<{ id: string; version: number } | null>(null);
  const wantVersion = pinnedVersion && pinnedVersion.id === selectedArtifactId
    ? pinnedVersion.version
    : undefined;
  const selectedVersion = loadedArtifact?.version ?? null;

  const [editingId, setEditingId] = useState<string | null>(null);
  const editing = editingId !== null && editingId === selectedArtifactId;
  const [editContent, setEditContent] = useState('');
  const [editSummary, setEditSummary] = useState('');
  const [saving, setSaving] = useState(false);
  const [copied, setCopied] = useState(false);
  // Failures carry the artifact they happened on, so selecting a different
  // one clears the message instead of showing the previous artifact's error
  // against the new one.
  const [failure, setFailure] = useState<{ id: string; message: string } | null>(null);
  const error = failure && failure.id === selectedArtifactId ? failure.message : null;
  const setError = useCallback((message: string | null) => {
    setFailure(message && selectedArtifactId ? { id: selectedArtifactId, message } : null);
  }, [selectedArtifactId]);
  // The rendered-content node, read by "Save as PDF" so the print output
  // carries the same formatting (headings, tables, math) the user sees.
  const contentRef = useRef<HTMLDivElement>(null);

  const summary = artifacts.find(a => a.id === selectedArtifactId);

  // One read, owned by the effect. It covers what used to be two effects:
  // the selection changing, and a newer version arriving over SSE, which now
  // shows up as summary.latest_version moving and re-running this. Nothing
  // is set synchronously, and a response for an artifact the user has since
  // navigated away from is dropped.
  //
  // `editing` is a dependency AND a guard: entering the editor stops the
  // panel pulling content out from under the textarea, and leaving it picks
  // up whatever is current, which is also what makes handleSave's explicit
  // reload unnecessary.
  const latestVersion = summary?.latest_version;
  useEffect(() => {
    if (!selectedArtifactId || editing) return;
    const id = selectedArtifactId;
    let cancelled = false;
    fetchArtifact(conversationId, id, wantVersion)
      .then(full => {
        if (cancelled) return;
        setFetched(full);
        setFailure(null);
      })
      .catch(e => {
        if (cancelled) return;
        setFailure({ id, message: e instanceof Error ? e.message : 'Failed to load artifact' });
      });
    return () => { cancelled = true; };
  }, [conversationId, selectedArtifactId, wantVersion, latestVersion, editing]);

  // True while something is selected but its content has not arrived yet.
  const loading = !!selectedArtifactId && !loadedArtifact && !error;

  const handleVersionChange = (version: number) => {
    if (selectedArtifactId) {
      setPinnedVersion({ id: selectedArtifactId, version });
    }
  };

  const handleCopy = () => {
    if (loadedArtifact?.content) {
      navigator.clipboard.writeText(loadedArtifact.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const handleDownload = () => {
    if (!loadedArtifact) return;
    const blob = new Blob([loadedArtifact.content], { type: loadedArtifact.content_type });
    const ext = getExtension(loadedArtifact.content_type, loadedArtifact.language);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${loadedArtifact.title}${ext}`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleDownloadPdf = () => {
    if (!loadedArtifact || !contentRef.current) return;
    const ok = printArtifactAsPdf(contentRef.current.innerHTML, loadedArtifact.title);
    if (!ok) setError('Could not open the print view - allow pop-ups for this site, then try again.');
  };

  const handleEdit = () => {
    if (loadedArtifact) {
      setEditContent(loadedArtifact.content);
      setEditSummary('');
      setEditingId(selectedArtifactId);
    }
  };

  const handleSave = async () => {
    if (!selectedArtifactId || !editContent) return;
    setSaving(true);
    setError(null);
    try {
      await updateArtifact(conversationId, selectedArtifactId, editContent, editSummary || 'User edit');
      // Leaving edit mode re-runs the read effect, which fetches the version
      // the save just created.
      setEditingId(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save');
    }
    setSaving(false);
  };

  const handleCancelEdit = () => {
    setEditingId(null);
    setEditContent('');
    setEditSummary('');
  };

  const isLatest = loadedArtifact && summary && loadedArtifact.version === summary.latest_version;
  const canEdit = isLatest && summary?.source === 'model_written';
  // "Save as PDF" applies to rendered text artifacts (reports, notes, code).
  // Existing PDFs already download as-is, and images have nothing to render.
  const ct = loadedArtifact?.content_type || '';
  const canPdf = !editing && !!loadedArtifact && ct !== 'application/pdf' && !ct.startsWith('image/');

  return (
    <div data-testid="artifact-panel" className="w-96 lg:w-[32rem] xl:w-[36rem] border-l border-border bg-bg-secondary flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
        {selectedArtifactId ? (
          <button
            onClick={() => onSelectArtifact(null)}
            className="text-text-secondary hover:text-text-primary transition-colors cursor-pointer text-sm"
          >
            &larr;
          </button>
        ) : null}
        <h3 className="text-sm font-semibold text-text-primary flex-1">
          {selectedArtifactId ? (loadedArtifact?.title || 'Loading...') : 'Artifacts'}
        </h3>
        <button
          onClick={onClose}
          className="text-text-secondary hover:text-text-primary transition-colors cursor-pointer text-xs"
        >
          &#10005;
        </button>
      </div>

      {/* Content area */}
      {!selectedArtifactId ? (
        // Artifact list
        <div className="flex-1 overflow-y-auto">
          {artifacts.length === 0 ? (
            <div className="px-4 py-8 text-center text-text-secondary text-sm">
              No artifacts yet
            </div>
          ) : (
            <div className="py-1">
              {artifacts.map(art => (
                <button
                  key={art.id}
                  data-testid="artifact-item"
                  onClick={() => onSelectArtifact(art.id)}
                  className="w-full text-left px-4 py-3 hover:bg-bg-tertiary transition-colors cursor-pointer border-b border-border last:border-0"
                >
                  <div className="flex items-center gap-2">
                    <span className="text-sm">{getContentTypeIcon(art.content_type)}</span>
                    <span className="text-sm text-text-primary truncate flex-1">{art.title}</span>
                    <span className="text-[10px] text-text-secondary">v{art.latest_version}</span>
                  </div>
                  <div className="text-[11px] text-text-secondary mt-0.5 ml-6">
                    {art.content_type}
                    {art.source === 'sandbox_generated' && ' · sandbox'}
                    {art.byte_size > 0 && ` · ${formatBytes(art.byte_size)}`}
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      ) : (
        // Artifact viewer
        <div className="flex-1 overflow-y-auto flex flex-col">
          {loading ? (
            <div className="flex-1 flex items-center justify-center text-text-secondary text-sm">
              Loading...
            </div>
          ) : error ? (
            <div className="px-4 py-4 text-error text-sm">{error}</div>
          ) : loadedArtifact ? (
            <>
              {/* Toolbar */}
              <div className="flex items-center gap-2 px-4 py-2 border-b border-border flex-wrap">
                {/* Version picker */}
                {summary && summary.latest_version > 1 && (
                  <select
                    value={selectedVersion || summary.latest_version}
                    onChange={e => handleVersionChange(Number(e.target.value))}
                    className="px-2 py-1 bg-bg-tertiary border border-border rounded text-xs text-text-primary outline-none cursor-pointer"
                  >
                    {Array.from({ length: summary.latest_version }, (_, i) => i + 1).reverse().map(v => (
                      <option key={v} value={v}>v{v}{v === summary.latest_version ? ' (latest)' : ''}</option>
                    ))}
                  </select>
                )}

                <div className="flex-1" />

                {canEdit && !editing && (
                  <button
                    onClick={handleEdit}
                    className="px-2 py-1 text-xs text-text-secondary hover:text-accent transition-colors cursor-pointer"
                  >
                    Edit
                  </button>
                )}
                <button
                  onClick={handleCopy}
                  className="px-2 py-1 text-xs text-text-secondary hover:text-accent transition-colors cursor-pointer"
                >
                  {copied ? 'Copied!' : 'Copy'}
                </button>
                <button
                  onClick={handleDownload}
                  className="px-2 py-1 text-xs text-text-secondary hover:text-accent transition-colors cursor-pointer"
                >
                  Download
                </button>
                {canPdf && (
                  <button
                    onClick={handleDownloadPdf}
                    className="px-2 py-1 text-xs text-text-secondary hover:text-accent transition-colors cursor-pointer"
                  >
                    PDF
                  </button>
                )}
              </div>

              {/* Edit mode */}
              {editing ? (
                <div className="flex-1 flex flex-col">
                  <textarea
                    value={editContent}
                    onChange={e => setEditContent(e.target.value)}
                    className="flex-1 px-4 py-3 bg-bg-primary text-sm text-text-primary font-mono outline-none resize-none"
                  />
                  <div className="px-4 py-3 border-t border-border space-y-2">
                    <div className="flex items-center justify-between text-[11px] text-text-secondary">
                      <span>{new Blob([editContent]).size > 500_000 ? '500 KB limit exceeded!' : formatBytes(new Blob([editContent]).size)}</span>
                    </div>
                    <input
                      type="text"
                      value={editSummary}
                      onChange={e => setEditSummary(e.target.value)}
                      placeholder="Change summary (optional)"
                      className="w-full px-3 py-1.5 bg-bg-tertiary border border-border rounded text-xs text-text-primary placeholder-text-secondary outline-none focus:border-accent"
                    />
                    <div className="flex gap-2">
                      <button
                        onClick={handleSave}
                        disabled={saving || new Blob([editContent]).size > 500_000}
                        className="px-3 py-1.5 bg-accent text-bg-primary rounded text-xs font-medium hover:bg-accent-hover transition-colors cursor-pointer disabled:opacity-50"
                      >
                        {saving ? 'Saving...' : 'Save'}
                      </button>
                      <button
                        onClick={handleCancelEdit}
                        className="px-3 py-1.5 text-xs text-text-secondary hover:text-text-primary transition-colors cursor-pointer"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                </div>
              ) : (
                // Content viewer
                <div ref={contentRef} className="flex-1 overflow-y-auto px-4 py-3">
                  <ArtifactContent artifact={loadedArtifact} />
                </div>
              )}
            </>
          ) : null}
        </div>
      )}
    </div>
  );
}

function PdfViewer({ url, title }: { url: string; title: string }) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let createdUrl: string | null = null;
    fetch(url, { credentials: 'include' })
      .then(r => {
        if (!r.ok) throw new Error(`Failed to load PDF (${r.status})`);
        return r.blob();
      })
      .then(blob => {
        if (cancelled) return;
        const pdfBlob = blob.type === 'application/pdf' ? blob : new Blob([blob], { type: 'application/pdf' });
        createdUrl = URL.createObjectURL(pdfBlob);
        setBlobUrl(createdUrl);
      })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load PDF'); });
    return () => {
      cancelled = true;
      if (createdUrl) URL.revokeObjectURL(createdUrl);
    };
  }, [url]);

  if (error) {
    return (
      <div className="text-error text-sm">
        {error}{' '}
        <a href={url} target="_blank" rel="noopener" className="underline">Open in new tab</a>
      </div>
    );
  }
  if (!blobUrl) {
    return <div className="text-text-secondary text-sm">Loading PDF...</div>;
  }
  return (
    <iframe
      src={blobUrl}
      title={title}
      className="w-full h-full min-h-[70vh] rounded border-0 bg-white"
    />
  );
}

function getBinaryArtifactUrl(artifact: ArtifactFull): string | null {
  if (artifact.external_url) return artifact.external_url;
  // Fallback: sandbox-generated binaries embed the fetch URL in their placeholder text
  // e.g. "...fetch them via GET /api/artifacts/<conv>/<hash>."
  const match = artifact.content?.match(/GET\s+(\/api\/artifacts\/[A-Za-z0-9_\-/]+)/);
  return match ? match[1] : null;
}

function ArtifactContent({ artifact }: { artifact: ArtifactFull }) {
  const ct = artifact.content_type;

  // Sandbox images — use external_url or parsed binary URL
  if (ct.startsWith('image/')) {
    const url = artifact.external_url || getBinaryArtifactUrl(artifact);
    if (url) {
      return <img src={url} alt={artifact.title} className="max-w-full rounded" />;
    }
  }

  // PDFs — fetch bytes and render via blob URL so Content-Disposition can't force a download
  if (ct === 'application/pdf') {
    const url = getBinaryArtifactUrl(artifact);
    if (url) {
      // key={url}: a different PDF is a different viewer, so React discards
      // the old blob URL and error with the old instance. That is what the
      // effect below used to do by hand, by clearing both before fetching.
      return <PdfViewer key={url} url={url} title={artifact.title} />;
    }
  }

  // Markdown
  if (ct === 'text/markdown') {
    return <Markdown content={artifact.content} />;
  }

  // Code / syntax highlighted
  const lang = getLanguageForHighlight(artifact);
  if (lang) {
    return (
      <div className="text-xs rounded-lg overflow-hidden">
        <SyntaxHighlighter language={lang} style={oneDark} customStyle={{ margin: 0, borderRadius: '0.5rem', fontSize: '0.75rem' }}>
          {artifact.content}
        </SyntaxHighlighter>
      </div>
    );
  }

  // Plain text fallback
  return (
    <pre className="text-sm text-text-primary whitespace-pre-wrap font-mono leading-relaxed">
      {artifact.content}
    </pre>
  );
}

