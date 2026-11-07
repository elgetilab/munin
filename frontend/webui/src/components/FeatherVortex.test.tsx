import { render } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { FeatherVortex } from './FeatherVortex';

// The vortex and the rotating message are started by globals from
// /shared/feather-vortex.js, each returning its own stop function. Those stop
// functions used to be parked on a single mutable ref shared by every run of
// the effect; these tests pin that each run cleans up exactly what it started.

type Globals = Record<string, unknown>;
const g = globalThis as unknown as Globals;

let stops: Array<ReturnType<typeof vi.fn>>;
let messageStops: Array<ReturnType<typeof vi.fn>>;

beforeEach(() => {
  stops = [];
  messageStops = [];
  g.createVortex = vi.fn(() => {
    const stop = vi.fn();
    stops.push(stop);
    return stop;
  });
  g.createRotatingMessage = vi.fn(() => {
    const stop = vi.fn();
    messageStops.push(stop);
    return stop;
  });
});

afterEach(() => {
  delete g.createVortex;
  delete g.createRotatingMessage;
});

describe('FeatherVortex', () => {
  it('starts the animation and stops it on unmount', () => {
    const { unmount } = render(<FeatherVortex size="inline" phase="thinking" />);
    expect(stops).toHaveLength(1);
    expect(stops[0]).not.toHaveBeenCalled();

    unmount();
    expect(stops[0]).toHaveBeenCalledTimes(1);
  });

  it('stops the previous run before starting a new one when the phase changes', () => {
    const { rerender, unmount } = render(<FeatherVortex size="inline" phase="thinking" />);
    expect(stops).toHaveLength(1);

    rerender(<FeatherVortex size="inline" phase="paper_search" />);

    // The first run's stop belongs to the first run, and is the one called.
    expect(stops).toHaveLength(2);
    expect(stops[0]).toHaveBeenCalledTimes(1);
    expect(stops[1]).not.toHaveBeenCalled();

    unmount();
    expect(stops[1]).toHaveBeenCalledTimes(1);
    // The first one is not stopped twice.
    expect(stops[0]).toHaveBeenCalledTimes(1);
  });

  it('starts the rotating message only for sizes that show one', () => {
    const { unmount } = render(<FeatherVortex size="idle" />);
    expect(messageStops).toHaveLength(0);
    unmount();

    render(<FeatherVortex size="large" phase="thinking" />);
    expect(messageStops).toHaveLength(1);
  });

  it('renders without the globals present', () => {
    delete g.createVortex;
    delete g.createRotatingMessage;
    expect(() => render(<FeatherVortex size="inline" />)).not.toThrow();
  });
});

// The real shared script, which the SPA and the static pages load as a plain
// <script>. Outside the Vite root, so read from disk; Vitest runs from frontend/webui.
describe('feather-vortex.js colours', () => {
  const source = readFileSync(resolve(process.cwd(), '../static/shared/feather-vortex.js'), 'utf-8');
  type CreateVortex = (canvas: HTMLCanvasElement, count: number, size: number, speed?: number) => () => void;
  const createVortex = new Function(`${source}\nreturn createVortex;`)() as CreateVortex;

  let frames: FrameRequestCallback[];
  let fills: string[];
  let strokes: string[];
  let canvas: HTMLCanvasElement;
  const root = document.documentElement;

  beforeEach(() => {
    frames = [];
    fills = [];
    strokes = [];
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => { frames.push(cb); return frames.length; });
    vi.stubGlobal('cancelAnimationFrame', () => {});
    // jsdom has no 2D canvas, so record what each feather is painted with.
    const ctx: Record<string, unknown> = { fillStyle: '', strokeStyle: '', globalAlpha: 1, lineWidth: 1 };
    for (const name of ['save', 'restore', 'translate', 'rotate', 'beginPath', 'moveTo', 'bezierCurveTo', 'quadraticCurveTo', 'clearRect']) {
      ctx[name] = () => {};
    }
    ctx.fill = () => { fills.push(ctx.fillStyle as string); };
    ctx.stroke = () => { strokes.push(ctx.strokeStyle as string); };
    canvas = document.createElement('canvas');
    canvas.width = 128;
    canvas.height = 128;
    canvas.getContext = (() => ctx) as unknown as HTMLCanvasElement['getContext'];
    document.body.appendChild(canvas);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    root.style.removeProperty('--text-primary');
    root.style.removeProperty('--text-secondary');
    canvas.remove();
  });

  const nextFrame = () => {
    fills = [];
    strokes = [];
    frames.shift()!(0);
  };

  it('paints with the dark values when the page sets none', () => {
    createVortex(canvas, 4, 7);
    expect(fills.length).toBe(4);
    expect(new Set(fills)).toEqual(new Set(['#e6edf3']));
    expect(new Set(strokes)).toEqual(new Set(['#8b949e']));
  });

  it('paints with the page text colours, and picks up a theme switch on the next frame', () => {
    root.style.setProperty('--text-primary', '#e6edf3');
    root.style.setProperty('--text-secondary', '#8b949e');
    createVortex(canvas, 4, 7);
    expect(new Set(fills)).toEqual(new Set(['#e6edf3']));

    // The light palette, switched while the animation runs.
    root.style.setProperty('--text-primary', '#1f2429');
    root.style.setProperty('--text-secondary', '#57606a');
    nextFrame();
    expect(fills.length).toBe(4);
    expect(new Set(fills)).toEqual(new Set(['#1f2429']));
    expect(new Set(strokes)).toEqual(new Set(['#57606a']));
  });
});
