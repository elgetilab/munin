import { useRef, useEffect } from 'react';
import { SEARCH_URL, UPLOAD_URL } from '../lib/urls';

declare function createVortex(canvas: HTMLCanvasElement, featherCount: number, sizeMultiplier: number, speedMultiplier?: number): () => void;

interface MaintenancePageProps {
  message?: string;
  since?: string;
}

function formatSince(iso?: string): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  // An unparseable date does not throw — it yields Invalid Date, whose
  // getTime() is NaN. Reject it so we don't render "started Invalid Date".
  if (isNaN(d.getTime())) return null;
  return d.toLocaleString([], {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}

/**
 * Shown when the cluster is in operator-triggered maintenance mode
 * (`munin-maintenance on`). Distinct from SleepingPage, which is the
 * nightly 2-6 AM GPU window with a known return time — maintenance has
 * no schedule, so it carries an operator-set message instead.
 */
export function MaintenancePage({ message, since }: MaintenancePageProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    let stop: (() => void) | undefined;
    if (canvasRef.current && typeof createVortex === 'function') {
      stop = createVortex(canvasRef.current, 10, 5.5, 0.3);
    }
    return () => stop?.();
  }, []);

  const sinceLabel = formatSince(since);

  return (
    <div className="flex-1 flex flex-col items-center justify-center gap-8 px-4">
      <canvas
        ref={canvasRef}
        width={192}
        height={192}
        style={{ width: 96, height: 96 }}
      />

      <div className="text-center max-w-md">
        <h1 className="text-2xl font-semibold text-text-primary mb-3">
          Munin is under maintenance
        </h1>
        <p className="text-text-secondary text-sm leading-relaxed mb-1">
          {message
            ? message
            : 'Chat is temporarily unavailable while the cluster is being worked on.'}
        </p>
        {sinceLabel && (
          <p className="text-text-secondary text-sm leading-relaxed">
            Maintenance started <span className="text-text-primary font-medium">{sinceLabel}</span>.
          </p>
        )}
      </div>

      <div className="flex flex-col gap-3 w-full max-w-xs">
        <a
          href={SEARCH_URL}
          className="flex items-center justify-between px-4 py-3 bg-bg-secondary border border-border rounded-lg text-sm text-text-primary hover:border-accent transition-colors no-underline"
        >
          <span>Paper Search is still available</span>
          <span className="text-accent">{'→'}</span>
        </a>
        <a
          href={UPLOAD_URL}
          className="flex items-center justify-between px-4 py-3 bg-bg-secondary border border-border rounded-lg text-sm text-text-primary hover:border-accent transition-colors no-underline"
        >
          <span>Upload papers</span>
          <span className="text-accent">{'→'}</span>
        </a>
      </div>
    </div>
  );
}
