import { useState, useEffect, useMemo, useCallback } from 'react';
import DeckGL from '@deck.gl/react';
import { ScatterplotLayer } from '@deck.gl/layers';
import { OrthographicView } from '@deck.gl/core';
import type { EmbeddingMap, EmbeddingMapCluster, EmbeddingMapPoint } from '../lib/types';
import { ErrorNotice } from './ui/ErrorNotice';

// Color palette for clusters (20 distinct colors, repeating)
const CLUSTER_COLORS: [number, number, number][] = [
  [99, 102, 241],   // indigo
  [16, 185, 129],   // emerald
  [245, 158, 11],   // amber
  [239, 68, 68],    // red
  [59, 130, 246],   // blue
  [168, 85, 247],   // purple
  [236, 72, 153],   // pink
  [20, 184, 166],   // teal
  [249, 115, 22],   // orange
  [132, 204, 22],   // lime
  [6, 182, 212],    // cyan
  [217, 70, 239],   // fuchsia
  [234, 179, 8],    // yellow
  [34, 197, 94],    // green
  [244, 63, 94],    // rose
  [79, 70, 229],    // violet
  [14, 165, 233],   // sky
  [251, 146, 60],   // orange-light
  [163, 230, 53],   // lime-light
  [45, 212, 191],   // teal-light
];

const UNCLUSTERED_COLOR: [number, number, number] = [100, 100, 100];
const VISIBLE_CLUSTERS = 40;
// Until the container has been measured, and where it cannot be.
const DEFAULT_SIZE = { width: 600, height: 420 };
const MAP_VIEW = new OrthographicView({ id: 'ortho' });

interface MapViewState {
  target: [number, number, number];
  zoom: number;
  minZoom: number;
  maxZoom: number;
}

function fitView(points: EmbeddingMapPoint[], size: { width: number; height: number }): MapViewState {
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const p of points) {
    if (p.x < minX) minX = p.x;
    if (p.x > maxX) maxX = p.x;
    if (p.y < minY) minY = p.y;
    if (p.y > maxY) maxY = p.y;
  }
  if (points.length === 0) {
    minX = minY = -100;
    maxX = maxY = 100;
  }
  // 5% padding on each side, so points on the edge are not cut in half.
  const width = (maxX - minX) * 1.1;
  const height = (maxY - minY) * 1.1;
  const zoom = Math.min(Math.log2(size.width / (width || 1)), Math.log2(size.height / (height || 1)));
  return {
    target: [(minX + maxX) / 2, (minY + maxY) / 2, 0],
    zoom,
    minZoom: zoom - 2,
    maxZoom: zoom + 8,
  };
}

const TOOLTIP_MAX_WIDTH = 256;
// A title on two lines and the details beneath it.
const TOOLTIP_HEIGHT = 96;
const TOOLTIP_MARGIN = 8;
const TOOLTIP_OFFSET = 12;

// Beside the point, flipped to the other side rather than cut off by the map's
// edge, and never wider than a narrow map.
function tooltipPlacement(x: number, y: number, size: { width: number; height: number }) {
  const width = Math.min(TOOLTIP_MAX_WIDTH, size.width - TOOLTIP_MARGIN * 2);
  let left = x + TOOLTIP_OFFSET;
  if (left + width + TOOLTIP_MARGIN > size.width) left = x - TOOLTIP_OFFSET - width;
  left = Math.max(TOOLTIP_MARGIN, Math.min(left, size.width - width - TOOLTIP_MARGIN));
  let top = y - TOOLTIP_OFFSET;
  if (top + TOOLTIP_HEIGHT + TOOLTIP_MARGIN > size.height) top = y - TOOLTIP_OFFSET - TOOLTIP_HEIGHT;
  top = Math.max(TOOLTIP_MARGIN, top);
  return { left, top, width };
}

// A device with no hover has no way to preview a point before tapping it.
function hasNoHover(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia('(hover: none)').matches;
}

interface EmbeddingMapViewProps {
  mapData: EmbeddingMap | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  onNavigateToTopic: (slug: string) => void;
  onChatWithTopic: (slug: string) => void;
}

interface PickInfo {
  object?: EmbeddingMapPoint;
  x?: number;
  y?: number;
}

export function EmbeddingMapView({ mapData, loading, error, onRetry, onNavigateToTopic, onChatWithTopic }: EmbeddingMapViewProps) {
  const [activeClusterId, setActiveClusterId] = useState<number | null>(null);
  const [showAllClusters, setShowAllClusters] = useState(false);
  const [hovered, setHovered] = useState<{ point: EmbeddingMapPoint; x: number; y: number } | null>(null);
  const [pinned, setPinned] = useState<{ point: EmbeddingMapPoint; x: number; y: number } | null>(null);
  const [touchOnly] = useState(hasNoHover);
  // Null means "fitted": the view follows the container and the selected cluster
  // until the user pans or zooms, and Reset view goes back to it.
  const [movedView, setMovedView] = useState<MapViewState | null>(null);
  const [mapElement, setMapElement] = useState<HTMLDivElement | null>(null);
  const [size, setSize] = useState(DEFAULT_SIZE);

  useEffect(() => {
    if (!mapElement || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => {
      const { width, height } = mapElement.getBoundingClientRect();
      // Zero while the overview is hidden behind a browse view: keep the last size.
      if (width > 0 && height > 0) {
        setSize(prev => (prev.width === width && prev.height === height ? prev : { width, height }));
      }
    });
    observer.observe(mapElement);
    return () => observer.disconnect();
  }, [mapElement]);

  const filteredPoints = useMemo(() => {
    if (!mapData) return [];
    if (activeClusterId === null) return mapData.points;
    return mapData.points.filter(p => p.cluster === activeClusterId);
  }, [mapData, activeClusterId]);

  const fittedView = useMemo(() => fitView(filteredPoints, size), [filteredPoints, size]);

  const clusterOf = useCallback(
    (point: EmbeddingMapPoint): EmbeddingMapCluster | undefined =>
      point.cluster >= 0 ? mapData?.clusters.find(c => c.id === point.cluster) : undefined,
    [mapData],
  );

  const getColor = useCallback((p: EmbeddingMapPoint): [number, number, number, number] => {
    if (p.cluster === -1) return [...UNCLUSTERED_COLOR, 80];
    const color = CLUSTER_COLORS[p.cluster % CLUSTER_COLORS.length];
    return [...color, 200];
  }, []);

  const layer = useMemo(() => new ScatterplotLayer<EmbeddingMapPoint>({
    id: 'embedding-scatter',
    data: filteredPoints,
    getPosition: (d: EmbeddingMapPoint) => [d.x, d.y, 0],
    getFillColor: (d: EmbeddingMapPoint) => getColor(d),
    getRadius: (d: EmbeddingMapPoint) => d.cluster === -1 ? 0.3 : 0.4,
    radiusUnits: 'common',
    radiusMinPixels: 1.5,
    radiusMaxPixels: 8,
    pickable: true,
    onHover: (info: PickInfo) => {
      setHovered(info.object ? { point: info.object, x: info.x ?? 0, y: info.y ?? 0 } : null);
    },
  }), [filteredPoints, getColor]);

  // With a mouse the tooltip has already shown what a click will open. On touch
  // the first tap has to be that preview, and the tooltip carries the way on.
  const handleMapClick = (info: PickInfo) => {
    if (touchOnly) {
      setPinned(info.object ? { point: info.object, x: info.x ?? 0, y: info.y ?? 0 } : null);
      return;
    }
    const cluster = info.object && clusterOf(info.object);
    if (cluster) onNavigateToTopic(cluster.slug);
  };

  const selectCluster = (id: number | null) => {
    setActiveClusterId(id);
    setMovedView(null);
    setHovered(null);
    setPinned(null);
  };

  const sortedClusters = useMemo(() => {
    if (!mapData) return [];
    return [...mapData.clusters]
      .filter(c => c.id >= 0)
      .sort((a, b) => b.size - a.size);
  }, [mapData]);

  if (loading) {
    return (
      <div className="mb-8 bg-bg-secondary border border-border rounded-xl p-8 text-center text-text-secondary text-sm">
        Loading embedding map...
      </div>
    );
  }

  if (error) {
    return <ErrorNotice message="Could not load the paper map." onRetry={onRetry} className="mb-8" />;
  }

  if (!mapData || mapData.points.length === 0) {
    return (
      <div className="mb-8 bg-bg-secondary border border-border rounded-xl p-8 text-center text-text-secondary text-sm">
        No papers mapped yet.
      </div>
    );
  }

  const activeCluster = sortedClusters.find(c => c.id === activeClusterId) ?? null;
  const visibleClusters = showAllClusters ? sortedClusters : sortedClusters.slice(0, VISIBLE_CLUSTERS);
  const tip = pinned ?? hovered;
  const tipCluster = tip ? clusterOf(tip.point) : undefined;
  const actionClass = 'text-xs px-3 py-1.5 bg-bg-tertiary border border-border rounded-lg text-text-secondary hover:text-text-primary hover:border-accent transition-colors cursor-pointer';

  return (
    <div className="mb-8">
      <h3 className="text-sm font-semibold text-text-primary mb-3">Paper Embedding Map</h3>
      <p className="text-xs text-text-secondary mb-3">
        {mapData.paper_count.toLocaleString()} papers projected into 2D space. Colors represent topic clusters.
      </p>

      {activeCluster && (
        <div data-testid="cluster-bar" className="flex flex-wrap items-center gap-2 mb-3 px-3 py-2 bg-bg-secondary border border-border rounded-xl">
          <span className="text-sm font-medium text-text-primary min-w-0 wrap-anywhere">{activeCluster.label}</span>
          <span className="text-xs text-text-secondary tabular-nums flex-1">{activeCluster.size.toLocaleString()} papers</span>
          <button type="button" onClick={() => onNavigateToTopic(activeCluster.slug)} className={actionClass}>Browse papers</button>
          <button
            type="button"
            onClick={() => onChatWithTopic(activeCluster.slug)}
            className="text-xs px-3 py-1.5 bg-accent/15 border border-accent/30 rounded-lg text-accent hover:bg-accent/25 transition-colors cursor-pointer"
          >
            Discuss in chat
          </button>
          <button type="button" onClick={() => selectCluster(null)} className={actionClass}>Clear</button>
        </div>
      )}

      {/* Map container */}
      <div ref={setMapElement} className="relative h-[300px] sm:h-[420px] bg-bg-tertiary border border-border rounded-xl overflow-hidden">
        <DeckGL
          views={MAP_VIEW}
          viewState={movedView ?? fittedView}
          onViewStateChange={({ viewState }: { viewState: unknown }) => {
            setMovedView(viewState as MapViewState);
            setPinned(null);
          }}
          controller={true}
          // Lets a vertical swipe scroll the page past the map on a phone. A
          // sideways drag and a pinch still move and zoom it.
          touchAction="pan-y"
          onClick={handleMapClick}
          layers={[layer]}
          style={{ position: 'absolute', inset: '0' }}
          getCursor={({ isHovering }: { isHovering: boolean }) => isHovering ? 'pointer' : 'grab'}
        />

        <button
          type="button"
          onClick={() => setMovedView(null)}
          className="absolute top-2 right-2 z-10 px-2 py-1 bg-bg-tertiary border border-border rounded text-xs text-text-primary cursor-pointer"
        >
          Reset view
        </button>

        {/* Tooltip */}
        {tip && (
          <div
            data-testid="map-tooltip"
            className={`absolute bg-bg-primary border border-border rounded-lg px-3 py-2 shadow-lg z-20 ${pinned ? '' : 'pointer-events-none'}`}
            style={tooltipPlacement(tip.x, tip.y, size)}
          >
            <p className="text-xs font-medium text-text-primary leading-snug line-clamp-2">{tip.point.title}</p>
            <p className="text-[10px] text-text-secondary mt-1 truncate">
              {tip.point.year}{tipCluster ? ` · ${tipCluster.label}` : ' · Unclustered'}
            </p>
            {tipCluster && (pinned ? (
              <button
                type="button"
                onClick={() => onNavigateToTopic(tipCluster.slug)}
                className="mt-1.5 text-xs text-accent hover:underline cursor-pointer"
              >
                Open this topic
              </button>
            ) : (
              <p className="text-[10px] text-text-secondary mt-0.5">Click to open this topic</p>
            ))}
          </div>
        )}
      </div>

      {/* Cluster list */}
      <div className="mt-4">
        <div className="flex items-center gap-2 mb-2">
          <span className="text-xs font-medium text-text-primary">Clusters</span>
          <span className="text-[10px] text-text-secondary">({sortedClusters.length} topics)</span>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {visibleClusters.map(cluster => {
            const color = CLUSTER_COLORS[cluster.id % CLUSTER_COLORS.length];
            const isActive = activeClusterId === cluster.id;
            return (
              <button
                key={cluster.id}
                aria-pressed={isActive}
                onClick={() => selectCluster(isActive ? null : cluster.id)}
                className={`inline-flex items-center gap-1.5 px-2 py-1 rounded-lg text-[11px] border transition-colors cursor-pointer ${
                  isActive
                    ? 'border-accent bg-accent/15 text-text-primary'
                    : 'border-border bg-bg-secondary text-text-secondary hover:border-accent/50 hover:text-text-primary'
                }`}
              >
                <span
                  className="w-2 h-2 rounded-full flex-shrink-0"
                  style={{ backgroundColor: `rgb(${color[0]}, ${color[1]}, ${color[2]})` }}
                />
                <span className="truncate max-w-32">{cluster.label}</span>
                <span className="text-[10px] tabular-nums opacity-60">{cluster.size}</span>
              </button>
            );
          })}
          {sortedClusters.length > VISIBLE_CLUSTERS && (
            <button
              type="button"
              onClick={() => setShowAllClusters(s => !s)}
              className="text-[11px] text-accent hover:underline px-2 py-1 cursor-pointer"
            >
              {showAllClusters ? 'Show fewer' : `+ ${sortedClusters.length - VISIBLE_CLUSTERS} more`}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
