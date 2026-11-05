import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { fetchArtifactStructure } from '../lib/api';
import { PROTEIN_COLORS, PROTEIN_STYLES, type ProteinColor, type ProteinSpec, type ProteinStyle } from '../lib/artifactDisplay';
import { buildWidgetDocument } from '../lib/widgetDocument';
import { WidgetFrame, type WidgetEvent } from './WidgetFrame';

interface ProteinViewerProps {
  spec: ProteinSpec;
  title: string;
  conversationId: string;
  artifactId: string;
  version?: number;
  // Height of the 3D view, as Tailwind classes.
  heightClass?: string;
  onExpand?: () => void;
}

const VIEWER_CSS = 'html,body,#viewer{margin:0;width:100%;height:100%;overflow:hidden;background:#0f1419}#viewer{position:relative}';

// 3Dmol.js is a large download, so it loads with the first viewer and is shared.
let viewerDocument: Promise<string> | null = null;
function loadViewerDocument(): Promise<string> {
  viewerDocument ??= Promise.all([
    import('3dmol/build/3Dmol-min.js?raw'),
    import('../widgets/proteinRuntime.js?raw'),
  ]).then(([library, runtime]) => buildWidgetDocument({
    scripts: [library.default, runtime.default],
    css: VIEWER_CSS,
    body: '<div id="viewer"></div>',
  })).catch(error => {
    viewerDocument = null;
    throw error;
  });
  return viewerDocument;
}

// Whether the element is on or near the screen. Browsers allow only a limited
// number of live 3D contexts, so a long chat keeps just the visible viewers.
function useNearScreen<T extends Element>() {
  const ref = useRef<T>(null);
  const [near, setNear] = useState(typeof IntersectionObserver === 'undefined');
  useEffect(() => {
    if (typeof IntersectionObserver === 'undefined' || !ref.current) return;
    const observer = new IntersectionObserver(
      ([entry]) => setNear(entry.isIntersecting),
      { rootMargin: '300px' },
    );
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return [ref, near] as const;
}

function databaseUrl(spec: ProteinSpec): string {
  const id = encodeURIComponent(spec.id);
  return spec.source === 'alphafold'
    ? `https://alphafold.ebi.ac.uk/entry/${id}`
    : `https://www.rcsb.org/structure/${id}`;
}

export function ProteinViewer({ spec, title, conversationId, artifactId, version, heightClass = 'h-80', onExpand }: ProteinViewerProps) {
  const [containerRef, near] = useNearScreen<HTMLDivElement>();
  const [srcDoc, setSrcDoc] = useState<string | null>(null);
  const [structure, setStructure] = useState<{ key: string; format: string; data: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rendered, setRendered] = useState(false);
  const [style, setStyle] = useState<ProteinStyle>(spec.style);
  const [color, setColor] = useState<ProteinColor>(spec.color);
  const [spin, setSpin] = useState(false);
  const [reset, setReset] = useState(0);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!near) return;
    let cancelled = false;
    const key = `${spec.source}:${spec.id}`;
    Promise.all([loadViewerDocument(), fetchArtifactStructure(conversationId, artifactId, version)])
      .then(([document, loaded]) => {
        if (cancelled) return;
        setSrcDoc(document);
        setStructure({ key, format: loaded.format, data: loaded.data });
        // Scrolling back into view fetches again, and a fetch that works ends an
        // earlier failure: the frame, and so 'rendered', depend on there being none.
        setError(null);
      })
      .catch(e => {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Could not load the structure');
      });
    return () => { cancelled = true; };
  }, [near, conversationId, artifactId, version, spec.source, spec.id, attempt]);

  const state = useMemo(() => ({
    structure,
    source: spec.source,
    highlight: spec.highlight,
    style,
    color,
    spin,
    reset,
  }), [structure, spec.source, spec.highlight, style, color, spin, reset]);

  const onEvent = useCallback((event: WidgetEvent) => {
    if (event.type === 'rendered') {
      setRendered(true);
      setError(null);
    } else if (event.type === 'error') {
      setError(typeof event.message === 'string' ? event.message : 'The viewer failed');
    }
  }, []);

  // Scrolling away drops the frame, and with it the live 3D context.
  const showFrame = near && srcDoc && structure && !error;
  const retry = () => {
    setError(null);
    setRendered(false);
    setAttempt(a => a + 1);
  };
  const databaseName = spec.source === 'alphafold' ? 'AlphaFold' : 'RCSB';
  // Nothing is there to restyle until the viewer has drawn the structure.
  const controlsDisabled = !rendered || !!error;
  const colorLabel = (value: ProteinColor, label: string) =>
    value === 'bfactor' && spec.source === 'alphafold' ? 'Confidence' : label;
  const controlClass = 'px-2 py-1 bg-bg-tertiary border border-border rounded text-xs text-text-primary outline-none cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed';

  return (
    <div
      ref={containerRef}
      data-testid="protein-viewer"
      data-status={error ? 'error' : rendered ? 'rendered' : 'loading'}
      className="w-full rounded-lg border border-border bg-bg-secondary overflow-hidden"
    >
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border">
        <span className="text-sm text-text-primary truncate flex-1" title={title}>{title}</span>
        {!error && (
          <a
            href={databaseUrl(spec)}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-text-secondary hover:text-accent transition-colors shrink-0"
          >
            {databaseName}
          </a>
        )}
        {onExpand && (
          <button
            type="button"
            onClick={onExpand}
            className="text-xs text-text-secondary hover:text-accent transition-colors cursor-pointer shrink-0"
          >
            Expand
          </button>
        )}
      </div>

      <div className={`relative ${heightClass}`} style={{ background: '#0f1419' }}>
        {showFrame && (
          <WidgetFrame
            srcDoc={srcDoc}
            title={`3D structure: ${title}`}
            state={state}
            onEvent={onEvent}
            className="absolute inset-0 w-full h-full border-0"
          />
        )}
        {error ? (
          <div role="alert" className="absolute inset-0 flex flex-col items-center justify-center gap-3 p-4 text-center text-sm text-error">
            <p className="wrap-anywhere">{error}</p>
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={retry}
                className="px-2 py-1 border border-error rounded text-xs font-medium hover:bg-error/10 cursor-pointer"
              >
                Retry
              </button>
              <a
                href={databaseUrl(spec)}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs text-text-secondary hover:text-accent transition-colors underline"
              >
                Open on {databaseName}
              </a>
            </div>
          </div>
        ) : !rendered && (
          <div className="absolute inset-0 flex items-center justify-center text-sm text-text-secondary pointer-events-none">Loading structure...</div>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2 px-3 py-2 border-t border-border">
        <select aria-label="Style" disabled={controlsDisabled} value={style} onChange={e => setStyle(e.target.value as ProteinStyle)} className={controlClass}>
          {PROTEIN_STYLES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
        </select>
        <select aria-label="Color" disabled={controlsDisabled} value={color} onChange={e => setColor(e.target.value as ProteinColor)} className={controlClass}>
          {PROTEIN_COLORS.map(c => <option key={c.value} value={c.value}>{colorLabel(c.value, c.label)}</option>)}
        </select>
        <button type="button" aria-pressed={spin} disabled={controlsDisabled} onClick={() => setSpin(s => !s)} className={controlClass}>
          {spin ? 'Stop' : 'Spin'}
        </button>
        <button type="button" disabled={controlsDisabled} onClick={() => setReset(r => r + 1)} className={controlClass}>Reset view</button>
      </div>
    </div>
  );
}
