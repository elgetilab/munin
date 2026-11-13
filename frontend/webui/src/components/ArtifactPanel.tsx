import { useState, useEffect, useCallback, useRef } from 'react';
import type { ArtifactSummary, ArtifactFull } from '../lib/types';
import { apiFetch, fetchArtifact, fetchArtifactStructure, updateArtifact } from '../lib/api';
import { copyText } from '../lib/copyText';
import { downloadFilename, saveBlob } from '../lib/artifactDownload';
import { fileSizeBytes, formatBytes, getContentTypeIcon, getContentTypeLabel, isFilePlaceholder, isWidget, parseProteinSpec, svgDataUrl } from '../lib/artifactDisplay';
import { ProteinViewer } from './ProteinViewer';
import { printArtifactAsPdf } from '../lib/printPdf';
import { Markdown } from './Markdown';
import { PrismLight as SyntaxHighlighter } from 'react-syntax-highlighter';
import { codeHighlightStyle } from '../lib/codeTheme';
import { useUiStore } from '../stores/uiStore';
import { useChatStore } from '../stores/chatStore';
import { useBelowXl, useModal } from '../hooks/useModal';
import { Dialog } from './ui/Dialog';
import { ErrorNotice } from './ui/ErrorNotice';
import { CloseIcon } from './ui/icons';

// P2 #26 commit 2: onClose / selectedArtifactId / onSelectArtifact
// dropped from props — all three are pulled directly from uiStore.
// `artifacts` + `conversationId` stay on props because they're
// chat-domain state (the parent already has them in hand from the
// useChat hook, no benefit to re-subscribing inside this component).
interface ArtifactPanelProps {
  artifacts: ArtifactSummary[];
  conversationId: string;
}

// Where the user was heading when an unsaved draft stopped them.
type LeaveEditor = 'cancel' | 'back' | 'close';

function getLanguageForHighlight(artifact: ArtifactFull): string | null {
  if (artifact.language) return artifact.language;
  const ct = artifact.content_type;
  if (ct === 'application/python') return 'python';
  if (ct === 'application/json') return 'json';
  if (ct === 'text/latex') return 'latex';
  return null;
}

export function ArtifactPanel({ artifacts, conversationId }: ArtifactPanelProps) {
  const selectedArtifactId = useUiStore(s => s.selectedArtifactId);
  const onSelectArtifact = useUiStore(s => s.setSelectedArtifactId);
  const setArtifactPanelOpen = useUiStore(s => s.setArtifactPanelOpen);
  const closeRef = useRef<HTMLButtonElement>(null);
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
  const [confirmLeave, setConfirmLeave] = useState<LeaveEditor | null>(null);
  const [copied, setCopied] = useState(false);
  const [downloading, setDownloading] = useState(false);
  // Failures carry the artifact they happened on, so selecting a different
  // one clears the message instead of showing the previous artifact's error
  // against the new one. A failed read only takes over the panel when there is
  // nothing loaded to show. A failed action (save, download, copy, print) never
  // does: it must not take the editor and the user's draft off the screen.
  const [failure, setFailure] = useState<{ id: string; message: string } | null>(null);
  const error = failure && failure.id === selectedArtifactId ? failure.message : null;
  const [reloadKey, setReloadKey] = useState(0);
  const [actionFailure, setActionFailure] = useState<{ id: string; message: string } | null>(null);
  const actionError = actionFailure && actionFailure.id === selectedArtifactId ? actionFailure.message : null;
  const setActionError = useCallback((message: string | null) => {
    setActionFailure(message && selectedArtifactId ? { id: selectedArtifactId, message } : null);
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
  }, [conversationId, selectedArtifactId, wantVersion, latestVersion, editing, reloadKey]);

  // True while something is selected but its content has not arrived yet.
  const loading = !!selectedArtifactId && !loadedArtifact && !error;

  const handleVersionChange = (version: number) => {
    if (selectedArtifactId) {
      // Picking the latest is the same as not pinning, so a later save or a new
      // version over SSE still moves the panel on.
      setPinnedVersion(version === summary?.latest_version ? null : { id: selectedArtifactId, version });
    }
  };

  const handleCopy = async () => {
    if (!loadedArtifact?.content) return;
    setActionError(null);
    if (await copyText(loadedArtifact.content)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } else {
      setActionError('Could not copy - the browser refused access to the clipboard.');
    }
  };

  const handleDownload = async () => {
    if (!loadedArtifact) return;
    setActionError(null);
    // The stored body of a protein artifact is the viewer's settings. What a
    // person wants from it is the structure those settings point at.
    if (proteinSpec) {
      setDownloading(true);
      try {
        const structure = await fetchArtifactStructure(conversationId, loadedArtifact.id, loadedArtifact.version);
        saveBlob(new Blob([structure.data], { type: 'text/plain' }), `${proteinSpec.id}.${structure.format}`);
      } catch (e) {
        setActionError(e instanceof Error ? e.message : 'Could not load the structure');
      }
      setDownloading(false);
      return;
    }
    const filename = downloadFilename(loadedArtifact);
    if (!isFilePlaceholder(loadedArtifact)) {
      saveBlob(new Blob([loadedArtifact.content], { type: loadedArtifact.content_type }), filename);
      return;
    }
    // The stored body only describes a sandbox file, so the bytes come from its
    // URL. Fetching rather than linking means a file the sandbox no longer has
    // is reported here, and the proxy's Content-Disposition cannot rename it.
    const url = getBinaryArtifactUrl(loadedArtifact);
    setDownloading(true);
    try {
      if (!url) throw new Error('no file URL');
      const res = await apiFetch(url, { credentials: 'include' });
      if (!res.ok) throw new Error(`status ${res.status}`);
      saveBlob(await res.blob(), filename);
    } catch {
      setActionError('Could not download this file. It may no longer be available.');
    }
    setDownloading(false);
  };

  const handleDownloadPdf = () => {
    if (!loadedArtifact || !contentRef.current) return;
    const ok = printArtifactAsPdf(contentRef.current.innerHTML, loadedArtifact.title);
    if (!ok) setActionError('Could not open the print view - allow pop-ups for this site, then try again.');
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
    setActionError(null);
    try {
      const saved = await updateArtifact(conversationId, selectedArtifactId, editContent, editSummary || 'User edit');
      // A user's save produces no SSE event, so the list has to learn about the
      // new version here. Read from the store, not the prop: an artifact event
      // may have landed while this request was in flight.
      const { artifacts: current, setArtifacts } = useChatStore.getState();
      setArtifacts(current.map(a => a.id === selectedArtifactId
        ? { ...a, latest_version: saved.version, byte_size: new Blob([editContent]).size, word_count: editContent.split(/\s+/).filter(Boolean).length }
        : a));
      // Leaving edit mode re-runs the read effect, which fetches the version
      // the save just created. A pin on an older version must not outlive that.
      setPinnedVersion(null);
      setEditingId(null);
    } catch (e) {
      const reason = e instanceof Error ? e.message : 'Failed to save';
      setActionError(`${reason}. Your edits are still here.`);
    }
    setSaving(false);
  };

  const leaveEditor = (where: LeaveEditor) => {
    setConfirmLeave(null);
    setEditingId(null);
    setEditContent('');
    setEditSummary('');
    if (where === 'back') onSelectArtifact(null);
    if (where === 'close') setArtifactPanelOpen(false);
  };

  // The panel's own ways out of the editor. Anything else that can take it away
  // (the sidebar and header, another artifact's link in the chat, a demo card,
  // browser Back or Forward, a reload) asks through lib/artifactDraft.ts, using
  // the flag set below.
  const requestLeave = (where: LeaveEditor) => {
    if (draftChanged) setConfirmLeave(where);
    else leaveEditor(where);
  };

  const modal = useModal({ onClose: () => requestLeave('close'), active: useBelowXl(), initialFocusRef: closeRef });

  const isLatest = loadedArtifact && summary && loadedArtifact.version === summary.latest_version;
  const canEdit = isLatest && summary?.source === 'model_written' && !isWidget(summary);
  // "Save as PDF" applies to rendered text artifacts (reports, notes, code).
  // Existing PDFs already download as-is, and images have nothing to render.
  const ct = loadedArtifact?.content_type || '';
  const isFile = !!loadedArtifact && isFilePlaceholder(loadedArtifact);
  const canPdf = !!loadedArtifact && !isFile && ct !== 'application/pdf' && !ct.startsWith('image/') && !isWidget(loadedArtifact);
  // A spec this panel cannot draw falls through to showing, and copying, its text.
  const proteinSpec = loadedArtifact && isWidget(loadedArtifact) ? parseProteinSpec(loadedArtifact.content) : null;
  const canCopy = !isFile && !proteinSpec;
  const showFileCard = !!loadedArtifact && isFile && !hasFilePreview(loadedArtifact);
  const draftChanged = editing && !!loadedArtifact && editContent !== loadedArtifact.content;
  useEffect(() => {
    useUiStore.getState().setArtifactDraftChanged(draftChanged);
    return () => useUiStore.getState().setArtifactDraftChanged(false);
  }, [draftChanged]);
  const draftBytes = editing ? new Blob([editContent]).size : 0;
  const draftTooLarge = draftBytes > 500_000;

  return (
    // A full-screen overlay below xl, where a side column would crush the chat.
    <div {...modal.panelProps} data-testid="artifact-panel" className="fixed inset-0 z-40 w-full flex flex-col bg-bg-secondary xl:static xl:z-auto xl:w-[36rem] xl:border-l xl:border-border xl:h-full">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
        {selectedArtifactId ? (
          <button
            aria-label="Back to artifact list"
            onClick={() => requestLeave('back')}
            className="p-2 -m-2 text-text-secondary hover:text-text-primary transition-colors cursor-pointer"
          >
            <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M19 12H5M12 19l-7-7 7-7" />
            </svg>
          </button>
        ) : null}
        <h3 id={modal.titleId} className="text-sm font-semibold text-text-primary flex-1 min-w-0 wrap-anywhere">
          {selectedArtifactId ? (loadedArtifact?.title || 'Loading...') : 'Artifacts'}
        </h3>
        <button
          ref={closeRef}
          aria-label="Close artifacts"
          onClick={() => requestLeave('close')}
          className="p-2 -m-2 text-text-secondary hover:text-text-primary transition-colors cursor-pointer"
        >
          <CloseIcon />
        </button>
      </div>

      {/* Content area */}
      {!selectedArtifactId ? (
        // Artifact list
        <div className="flex-1 overflow-y-auto">
          {artifacts.length === 0 ? (
            <div className="px-4 py-8 text-center text-text-secondary text-sm">
              No artifacts yet. Reports, code and figures Munin creates in this chat appear here.
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
                    <span aria-hidden="true" className="w-5 flex-shrink-0 text-center text-sm">{getContentTypeIcon(art.content_type)}</span>
                    <span className="text-sm text-text-primary truncate flex-1">{art.title}</span>
                    <span className="text-[10px] text-text-secondary">v{art.latest_version}</span>
                  </div>
                  <div className="text-[11px] text-text-secondary mt-0.5 ml-7">
                    {getContentTypeLabel(art.content_type)}
                    {art.source === 'sandbox_generated' && ' · Generated by code'}
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
          ) : !loadedArtifact ? (
            error ? (
              <div className="px-4 py-4">
                <ErrorNotice message={error} onRetry={() => { setFailure(null); setReloadKey(k => k + 1); }} />
              </div>
            ) : null
          ) : (
            <>
              {error && (
                <ErrorNotice
                  message={error}
                  onRetry={() => { setFailure(null); setReloadKey(k => k + 1); }}
                  className="mx-4 mt-3"
                />
              )}
              {actionError && (
                <ErrorNotice message={actionError} onDismiss={() => setActionError(null)} className="mx-4 mt-3" />
              )}
              {/* Toolbar. It acts on the saved content, so it goes away while
                  editing a draft, and a file card carries its own Download. */}
              {!editing && !showFileCard && <div className="flex items-center gap-2 px-4 py-2 border-b border-border flex-wrap">
                {/* Version picker */}
                {summary && summary.latest_version > 1 && (
                  <select
                    aria-label="Version"
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

                {canEdit && (
                  <button
                    onClick={handleEdit}
                    className="px-2 py-1 text-xs text-text-secondary hover:text-text-primary transition-colors cursor-pointer"
                  >
                    Edit
                  </button>
                )}
                {canCopy && (
                  <button
                    onClick={handleCopy}
                    className="px-2 py-1 text-xs text-text-secondary hover:text-text-primary transition-colors cursor-pointer"
                  >
                    {copied ? 'Copied!' : 'Copy'}
                  </button>
                )}
                <button
                  onClick={handleDownload}
                  disabled={downloading}
                  className="px-2 py-1 text-xs text-text-secondary hover:text-text-primary transition-colors cursor-pointer disabled:opacity-50"
                >
                  {downloading ? 'Downloading...' : 'Download'}
                </button>
                {canPdf && (
                  <button
                    onClick={handleDownloadPdf}
                    className="px-2 py-1 text-xs text-text-secondary hover:text-text-primary transition-colors cursor-pointer"
                  >
                    Save as PDF
                  </button>
                )}
              </div>}

              {/* Edit mode */}
              {editing ? (
                <div className="flex-1 flex flex-col">
                  <textarea
                    aria-label="Artifact content"
                    value={editContent}
                    onChange={e => setEditContent(e.target.value)}
                    className="flex-1 px-4 py-3 bg-bg-primary text-sm text-text-primary font-mono outline-none resize-none"
                  />
                  <div className="px-4 py-3 border-t border-border space-y-2">
                    <div className="flex items-center justify-between text-[11px] text-text-secondary">
                      <span className={draftTooLarge ? 'text-error' : undefined}>{draftTooLarge ? '500 KB limit exceeded!' : formatBytes(draftBytes)}</span>
                    </div>
                    <input
                      type="text"
                      aria-label="Change summary"
                      value={editSummary}
                      onChange={e => setEditSummary(e.target.value)}
                      placeholder="Change summary (optional)"
                      className="w-full px-3 py-1.5 bg-bg-tertiary border border-border rounded text-xs text-text-primary placeholder-text-secondary outline-none focus:border-text-secondary"
                    />
                    <div className="flex gap-2">
                      <button
                        onClick={handleSave}
                        disabled={saving || draftTooLarge || !editContent || !draftChanged}
                        className="px-3 py-1.5 bg-accent text-bg-primary rounded text-xs font-medium hover:bg-accent-hover transition-colors cursor-pointer disabled:opacity-50"
                      >
                        {saving ? 'Saving...' : 'Save'}
                      </button>
                      <button
                        onClick={() => requestLeave('cancel')}
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
                  <ArtifactContent
                    artifact={loadedArtifact}
                    conversationId={conversationId}
                    downloading={downloading}
                    onDownload={handleDownload}
                  />
                </div>
              )}
            </>
          )}
        </div>
      )}

      {confirmLeave && (
        <Dialog
          title="Discard your changes?"
          onClose={() => setConfirmLeave(null)}
          footer={(
            <>
              <button
                onClick={() => setConfirmLeave(null)}
                className="px-3 py-1.5 text-xs text-text-secondary hover:text-text-primary transition-colors cursor-pointer"
              >
                Keep editing
              </button>
              <button
                onClick={() => leaveEditor(confirmLeave)}
                className="px-3 py-1.5 bg-error text-bg-primary rounded text-xs font-medium hover:opacity-90 transition-opacity cursor-pointer"
              >
                Discard
              </button>
            </>
          )}
        >
          <p className="text-sm text-text-secondary">Your edits to this artifact have not been saved.</p>
        </Dialog>
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
    apiFetch(url, { credentials: 'include' })
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

// Whether a file the panel only has a description of can still be shown, from its URL.
function hasFilePreview(artifact: ArtifactFull): boolean {
  const ct = artifact.content_type;
  return (ct.startsWith('image/') || ct === 'application/pdf') && !!getBinaryArtifactUrl(artifact);
}

function FileCard({ artifact, downloading, onDownload }: { artifact: ArtifactFull; downloading: boolean; onDownload: () => void }) {
  const size = fileSizeBytes(artifact);
  const details = [getContentTypeLabel(artifact.content_type), size ? formatBytes(size) : null].filter(Boolean).join(' · ');
  return (
    <div data-testid="artifact-file-card" className="flex flex-col items-center gap-3 px-4 py-8 text-center">
      <span aria-hidden="true" className="text-3xl">{getContentTypeIcon(artifact.content_type)}</span>
      <div className="min-w-0">
        <p className="text-sm font-medium text-text-primary wrap-anywhere">{artifact.filename || artifact.title}</p>
        <p className="text-xs text-text-secondary mt-1">{details}</p>
      </div>
      <p className="text-xs text-text-secondary">No preview for this file type</p>
      {getBinaryArtifactUrl(artifact) && (
        <button
          onClick={onDownload}
          disabled={downloading}
          className="px-3 py-1.5 bg-accent text-bg-primary rounded text-xs font-medium hover:bg-accent-hover transition-colors cursor-pointer disabled:opacity-50"
        >
          {downloading ? 'Downloading...' : 'Download'}
        </button>
      )}
    </div>
  );
}

function ArtifactContent({ artifact, conversationId, downloading, onDownload }: {
  artifact: ArtifactFull;
  conversationId: string;
  downloading: boolean;
  onDownload: () => void;
}) {
  const ct = artifact.content_type;

  if (isFilePlaceholder(artifact) && !hasFilePreview(artifact)) {
    return <FileCard artifact={artifact} downloading={downloading} onDownload={onDownload} />;
  }

  if (isWidget(artifact)) {
    const spec = parseProteinSpec(artifact.content);
    if (spec) {
      return (
        <ProteinViewer
          spec={spec}
          title={artifact.title}
          conversationId={conversationId}
          artifactId={artifact.id}
          version={artifact.version}
          heightClass="h-[min(60vh,100vw)]"
        />
      );
    }
  }

  // Sandbox images — use external_url or parsed binary URL
  if (ct.startsWith('image/')) {
    const url = artifact.external_url || getBinaryArtifactUrl(artifact);
    if (url) {
      return <img src={url} alt={artifact.title} className="max-w-full rounded bg-white" />;
    }
    if (ct === 'image/svg+xml') {
      return <img src={svgDataUrl(artifact.content)} alt={artifact.title} className="max-w-full rounded bg-white" />;
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
        <SyntaxHighlighter language={lang} style={codeHighlightStyle} customStyle={{ margin: 0, borderRadius: '0.5rem', fontSize: '0.75rem' }}>
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

