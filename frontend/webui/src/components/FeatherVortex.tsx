import { useRef, useEffect } from 'react';

// Import the shared feather vortex functions from the global scope.
// The script is loaded via <script src="/shared/feather-vortex.js"> in index.html.
declare function createVortex(canvas: HTMLCanvasElement, featherCount: number, sizeMultiplier: number, speedMultiplier?: number): () => void;
declare function createRotatingMessage(el: HTMLElement, phase: string): () => void;

interface FeatherVortexProps {
  size?: 'inline' | 'large' | 'idle' | 'generating';
  phase?: string;
  className?: string;
}

const CONFIG = {
  inline: { canvas: 128, css: 40, feathers: 10, multiplier: 7.0, speed: 1.0 },
  large: { canvas: 320, css: 160, feathers: 13, multiplier: 5.5, speed: 1.0 },
  idle: { canvas: 128, css: 36, feathers: 8, multiplier: 9.0, speed: 0.15 },
  generating: { canvas: 128, css: 36, feathers: 8, multiplier: 9.0, speed: 0.8 },
};

export function FeatherVortex({ size = 'inline', phase = 'thinking', className = '' }: FeatherVortexProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const messageRef = useRef<HTMLSpanElement>(null);

  const config = CONFIG[size];
  const showMessages = size === 'large' || size === 'inline';

  useEffect(() => {
    // Locals, not a ref: each run of this effect owns the stop functions it
    // created, so its cleanup closes over exactly the pair it started. The
    // previous version parked both on one mutable ref shared by every run,
    // which only worked because React happens to run a cleanup before the
    // next effect. That is what exhaustive-deps was warning about when it
    // said the ref value would likely have changed by cleanup time.
    let stopVortex: (() => void) | undefined;
    let stopMessages: (() => void) | undefined;
    if (canvasRef.current && typeof createVortex === 'function') {
      stopVortex = createVortex(canvasRef.current, config.feathers, config.multiplier, config.speed);
    }
    if (showMessages && messageRef.current && typeof createRotatingMessage === 'function') {
      stopMessages = createRotatingMessage(messageRef.current, phase);
    }
    return () => {
      stopVortex?.();
      stopMessages?.();
    };
  }, [config.feathers, config.multiplier, config.speed, phase, showMessages]);

  return (
    <div className={`flex ${size === 'large' ? 'flex-col' : ''} items-center gap-3 ${className}`}>
      <canvas
        ref={canvasRef}
        width={config.canvas}
        height={config.canvas}
        style={{ width: config.css, height: config.css }}
      />
      {showMessages && (
        <span
          ref={messageRef}
          className="text-text-secondary text-sm italic"
        />
      )}
    </div>
  );
}
