import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ComponentProps } from 'react';
import { EmbeddingMapView } from './EmbeddingMapView';
import type { EmbeddingMap, EmbeddingMapPoint } from '../lib/types';

// deck.gl needs WebGL, which jsdom has not got. Keeping what it was last given
// lets a test read the view and play the part of a pointer.
interface DeckProps {
  viewState: { target: number[]; zoom: number };
  touchAction?: string;
  onClick: (info: { object?: EmbeddingMapPoint; x?: number; y?: number }) => void;
  onViewStateChange: (change: { viewState: unknown }) => void;
  layers: { props: { data: EmbeddingMapPoint[]; onHover: (info: { object?: EmbeddingMapPoint; x?: number; y?: number }) => void } }[];
}
const deck = vi.hoisted(() => ({ props: null as unknown }));
vi.mock('@deck.gl/react', () => ({
  default: (props: unknown) => {
    deck.props = props;
    return <div data-testid="deck" />;
  },
}));
const deckProps = () => deck.props as DeckProps;

const POINTS: EmbeddingMapPoint[] = [
  { id: 'a', doi: '', title: 'Paper A', year: 2020, x: -100, y: -50, cluster: 0 },
  { id: 'b', doi: '', title: 'Paper B', year: 2021, x: 100, y: 50, cluster: 0 },
  { id: 'c', doi: '', title: 'Paper C', year: 2022, x: 10, y: 10, cluster: 1 },
  { id: 'd', doi: '', title: 'Paper D', year: 2023, x: 12, y: 12, cluster: 1 },
  { id: 'e', doi: '', title: 'Paper E', year: 2024, x: 0, y: 0, cluster: -1 },
];

const MAP: EmbeddingMap = {
  generated_at: '',
  paper_count: 5,
  cluster_count: 2,
  points: POINTS,
  clusters: [
    { id: 0, label: 'Lipid nanoparticles', slug: 'lipid', size: 2, centroid: [0, 0] },
    { id: 1, label: 'Cryo-EM', slug: 'cryo', size: 2, centroid: [11, 11] },
  ],
};

function renderMap(overrides: Partial<ComponentProps<typeof EmbeddingMapView>> = {}) {
  const props = {
    mapData: MAP,
    loading: false,
    error: null,
    onRetry: vi.fn(),
    onNavigateToTopic: vi.fn(),
    onChatWithTopic: vi.fn(),
    ...overrides,
  };
  render(<EmbeddingMapView {...props} />);
  return props;
}

function hover(point: EmbeddingMapPoint, x = 50, y = 50) {
  act(() => deckProps().layers[0].props.onHover({ object: point, x, y }));
}

describe('EmbeddingMapView', () => {
  const realObserver = globalThis.ResizeObserver;
  afterEach(() => {
    globalThis.ResizeObserver = realObserver;
    delete (window as Partial<Window>).matchMedia;
    vi.restoreAllMocks();
  });

  function stubContainer(width: number, height: number) {
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ width, height } as DOMRect);
    globalThis.ResizeObserver = class {
      constructor(private callback: ResizeObserverCallback) {}
      observe() { this.callback([], this as unknown as ResizeObserver); }
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver;
  }

  describe('fitting', () => {
    it('fits all the points to the measured container', () => {
      stubContainer(342, 300);
      renderMap();
      const { viewState } = deckProps();
      // 200 x 100 world units, padded 10%, into 342 x 300: width is the limit.
      expect(viewState.zoom).toBeCloseTo(Math.log2(342 / 220));
      expect(viewState.target).toEqual([0, 0, 0]);
    });

    it('zooms further in on a wider map', () => {
      stubContainer(342, 300);
      renderMap();
      const phone = deckProps().viewState.zoom;
      stubContainer(848, 420);
      renderMap();
      expect(deckProps().viewState.zoom).toBeGreaterThan(phone);
    });

    it('asks the browser to scroll on a vertical swipe', () => {
      renderMap();
      expect(deckProps().touchAction).toBe('pan-y');
    });

    it('goes back to the fitted view from Reset view', async () => {
      renderMap();
      const fitted = deckProps().viewState;
      const moved = { target: [40, 40, 0], zoom: 3 };
      act(() => deckProps().onViewStateChange({ viewState: moved }));
      expect(deckProps().viewState).toEqual(moved);

      await userEvent.click(screen.getByRole('button', { name: 'Reset view' }));
      expect(deckProps().viewState).toEqual(fitted);
    });
  });

  describe('tooltip', () => {
    it('names the topic, not the cluster id, and says what a click does', () => {
      renderMap();
      hover(POINTS[0]);
      const tip = screen.getByTestId('map-tooltip');
      expect(tip).toHaveTextContent('Paper A');
      expect(tip).toHaveTextContent('2020 · Lipid nanoparticles');
      expect(tip).toHaveTextContent('Click to open this topic');
      expect(tip).not.toHaveTextContent('Cluster');
    });

    it('offers nothing to open for an unclustered point', () => {
      renderMap();
      hover(POINTS[4]);
      const tip = screen.getByTestId('map-tooltip');
      expect(tip).toHaveTextContent('Unclustered');
      expect(tip).not.toHaveTextContent('Click to open');
    });

    it('flips away from the right and bottom edges and stays inside', () => {
      stubContainer(600, 420);
      renderMap();
      hover(POINTS[0], 590, 415);
      const { left, top, width } = screen.getByTestId('map-tooltip').style;
      expect(parseFloat(left) + parseFloat(width)).toBeLessThanOrEqual(600);
      expect(parseFloat(left) + parseFloat(width)).toBeLessThan(590);
      expect(parseFloat(top) + 96).toBeLessThanOrEqual(420);
    });

    it('stays inside at the top edge and on a map narrower than itself', () => {
      stubContainer(200, 300);
      renderMap();
      hover(POINTS[0], 100, 2);
      const { left, top, width } = screen.getByTestId('map-tooltip').style;
      expect(parseFloat(left)).toBeGreaterThanOrEqual(0);
      expect(parseFloat(left) + parseFloat(width)).toBeLessThanOrEqual(200);
      expect(parseFloat(top)).toBeGreaterThanOrEqual(0);
    });
  });

  describe('clicking a point', () => {
    it('opens the topic with a mouse', () => {
      const { onNavigateToTopic } = renderMap();
      act(() => deckProps().onClick({ object: POINTS[2], x: 1, y: 1 }));
      expect(onNavigateToTopic).toHaveBeenCalledWith('cryo');
    });

    it('previews on the first tap on touch, and opens from the preview', async () => {
      window.matchMedia = vi.fn(() => ({ matches: true })) as unknown as typeof window.matchMedia;
      const { onNavigateToTopic } = renderMap();
      act(() => deckProps().onClick({ object: POINTS[2], x: 50, y: 50 }));
      expect(onNavigateToTopic).not.toHaveBeenCalled();
      expect(screen.getByTestId('map-tooltip')).toHaveTextContent('Paper C');

      await userEvent.click(screen.getByRole('button', { name: 'Open this topic' }));
      expect(onNavigateToTopic).toHaveBeenCalledWith('cryo');
    });

    it('drops the preview on a tap elsewhere', () => {
      window.matchMedia = vi.fn(() => ({ matches: true })) as unknown as typeof window.matchMedia;
      renderMap();
      act(() => deckProps().onClick({ object: POINTS[2], x: 50, y: 50 }));
      act(() => deckProps().onClick({ x: 5, y: 5 }));
      expect(screen.queryByTestId('map-tooltip')).not.toBeInTheDocument();
    });
  });

  describe('clusters', () => {
    it('fits to the selected cluster and offers what to do next', async () => {
      const { onNavigateToTopic, onChatWithTopic } = renderMap();
      const allZoom = deckProps().viewState.zoom;
      const chip = screen.getByRole('button', { name: /Cryo-EM/ });
      expect(chip).toHaveAttribute('aria-pressed', 'false');

      await userEvent.click(chip);

      expect(chip).toHaveAttribute('aria-pressed', 'true');
      expect(deckProps().layers[0].props.data).toHaveLength(2);
      expect(deckProps().viewState.target).toEqual([11, 11, 0]);
      expect(deckProps().viewState.zoom).toBeGreaterThan(allZoom);

      const bar = screen.getByTestId('cluster-bar');
      expect(bar).toHaveTextContent('Cryo-EM');
      expect(bar).toHaveTextContent('2 papers');
      await userEvent.click(screen.getByRole('button', { name: 'Browse papers' }));
      expect(onNavigateToTopic).toHaveBeenCalledWith('cryo');
      await userEvent.click(screen.getByRole('button', { name: 'Discuss in chat' }));
      expect(onChatWithTopic).toHaveBeenCalledWith('cryo');

      await userEvent.click(screen.getByRole('button', { name: 'Clear' }));
      expect(screen.queryByTestId('cluster-bar')).not.toBeInTheDocument();
      expect(deckProps().layers[0].props.data).toHaveLength(5);
      expect(deckProps().viewState.zoom).toBeCloseTo(allZoom);
    });

    it('reveals clusters past the first 40', async () => {
      const clusters = Array.from({ length: 43 }, (_, i) => ({ id: i, label: `Topic ${i}`, slug: `t${i}`, size: 100 - i, centroid: [0, 0] as [number, number] }));
      renderMap({ mapData: { ...MAP, clusters } });
      expect(screen.queryByRole('button', { name: /Topic 42/ })).not.toBeInTheDocument();
      await userEvent.click(screen.getByRole('button', { name: '+ 3 more' }));
      expect(screen.getByRole('button', { name: /Topic 42/ })).toBeInTheDocument();
    });
  });

  describe('other states', () => {
    it('explains an empty map rather than drawing a blank one', () => {
      renderMap({ mapData: { ...MAP, paper_count: 0, points: [], clusters: [] } });
      expect(screen.getByText('No papers mapped yet.')).toHaveClass('mb-8');
      expect(screen.queryByTestId('deck')).not.toBeInTheDocument();
      expect(screen.queryByText('Clusters')).not.toBeInTheDocument();
    });

    it('offers Retry on an error, with room below it', async () => {
      const { onRetry } = renderMap({ mapData: null, error: 'Failed to fetch embedding map' });
      const alert = screen.getByRole('alert');
      expect(alert).toHaveTextContent('Could not load the paper map.');
      expect(alert).toHaveClass('mb-8');
      await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
      expect(onRetry).toHaveBeenCalledOnce();
    });

    it('leaves room below the loading state too', () => {
      renderMap({ mapData: null, loading: true });
      expect(screen.getByText('Loading embedding map...')).toHaveClass('mb-8');
    });
  });
});
