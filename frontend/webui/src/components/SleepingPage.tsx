import { useRef, useEffect } from 'react';
import { SEARCH_URL, UPLOAD_URL } from '../lib/urls';

declare function createVortex(canvas: HTMLCanvasElement, featherCount: number, sizeMultiplier: number, speedMultiplier?: number): () => void;

interface SleepingPageProps {
  nextStart?: string;
}

// Length of the nightly window (VLLM_STOP_HOUR to VLLM_START_HOUR in
// backend/retrieval/main.py). The status API sends next_start for every offline
// state, so only a start this close means the window is what we are in.
const WINDOW_MS = 4 * 60 * 60 * 1000;

function formatTime(d: Date): string {
  return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

/** The nightly window in the viewer's time, or null if this is not the window. */
function nightlyWindow(iso?: string): { start: string; end: string } | null {
  if (!iso) return null;
  const end = new Date(iso);
  // An unparseable date does not throw — it yields Invalid Date, whose getTime() is NaN.
  if (isNaN(end.getTime())) return null;
  const left = end.getTime() - Date.now();
  if (left <= 0 || left > WINDOW_MS) return null;
  return { start: formatTime(new Date(end.getTime() - WINDOW_MS)), end: formatTime(end) };
}

export function SleepingPage({ nextStart }: SleepingPageProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    let stop: (() => void) | undefined;
    if (canvasRef.current && typeof createVortex === 'function') {
      stop = createVortex(canvasRef.current, 10, 5.5, 0.3);
    }
    return () => stop?.();
  }, []);

  const nightly = nightlyWindow(nextStart);

  return (
    <div className="flex-1 flex flex-col items-center justify-center gap-8 px-4">
      <canvas
        ref={canvasRef}
        width={192}
        height={192}
        style={{ width: 96, height: 96 }}
      />

      <div className="text-center max-w-md">
        {nightly ? (
          <>
            <h1 className="text-2xl font-semibold text-text-primary mb-3">
              Munin is resting
            </h1>
            <p className="text-text-secondary text-sm leading-relaxed mb-1">
              The cluster GPUs are reserved for experiments between {nightly.start} and {nightly.end}.
            </p>
            <p className="text-text-secondary text-sm leading-relaxed">
              Chat will be back at <span className="text-text-primary font-medium">{nightly.end}</span>.
            </p>
          </>
        ) : (
          <>
            <h1 className="text-2xl font-semibold text-text-primary mb-3">
              Chat is temporarily offline
            </h1>
            <p className="text-text-secondary text-sm leading-relaxed">
              Please try again later.
            </p>
          </>
        )}
      </div>

      <div className="flex flex-col gap-3 w-full max-w-xs">
        <a
          href={SEARCH_URL}
          className="flex items-center justify-between px-4 py-3 bg-bg-secondary border border-border rounded-lg text-sm text-text-primary hover:border-accent transition-colors no-underline"
        >
          <span>Paper Search is still available</span>
          <span className="text-accent">{'\u2192'}</span>
        </a>
        <a
          href={UPLOAD_URL}
          className="flex items-center justify-between px-4 py-3 bg-bg-secondary border border-border rounded-lg text-sm text-text-primary hover:border-accent transition-colors no-underline"
        >
          <span>Upload papers</span>
          <span className="text-accent">{'\u2192'}</span>
        </a>
      </div>
    </div>
  );
}
