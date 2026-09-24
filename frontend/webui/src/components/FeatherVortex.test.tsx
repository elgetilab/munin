import { render } from '@testing-library/react';
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
