import { useRef, useEffect } from 'react';

declare function createVortex(canvas: HTMLCanvasElement, featherCount: number, sizeMultiplier: number, speedMultiplier?: number): () => void;

interface SleepingPageProps {
  nextStart?: string;
}

function formatTime(iso?: string): string {
  if (!iso) return '6:00 AM';
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  } catch {
    return '6:00 AM';
  }
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

  const time = formatTime(nextStart);

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
          Munin is resting
        </h1>
        <p className="text-text-secondary text-sm leading-relaxed mb-1">
          The cluster GPUs are reserved for experiments between 2:00 AM and 6:00 AM.
        </p>
        <p className="text-text-secondary text-sm leading-relaxed">
          Chat and Deep Research will be back at <span className="text-text-primary font-medium">{time}</span>.
        </p>
      </div>

      <div className="flex flex-col gap-3 w-full max-w-xs">
        <a
          href="https://search.muninai.org"
          className="flex items-center justify-between px-4 py-3 bg-bg-secondary border border-border rounded-lg text-sm text-text-primary hover:border-accent transition-colors no-underline"
        >
          <span>Paper Search is still available</span>
          <span className="text-accent">{'\u2192'}</span>
        </a>
        <a
          href="https://upload.muninai.org"
          className="flex items-center justify-between px-4 py-3 bg-bg-secondary border border-border rounded-lg text-sm text-text-primary hover:border-accent transition-colors no-underline"
        >
          <span>Upload papers</span>
          <span className="text-accent">{'\u2192'}</span>
        </a>
      </div>
    </div>
  );
}
