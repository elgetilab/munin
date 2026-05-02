import { useState, useEffect, useMemo, useCallback } from 'react';
import DeckGL from '@deck.gl/react';
import { ScatterplotLayer } from '@deck.gl/layers';
import { OrthographicView } from '@deck.gl/core';
import type { EmbeddingMap, EmbeddingMapPoint } from '../lib/types';
import { fetchEmbeddingMap } from '../lib/api';

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

interface EmbeddingMapViewProps {
  onNavigateToTopic: (slug: string) => void;
}

export function EmbeddingMapView({ onNavigateToTopic }: EmbeddingMapViewProps) {
  const [mapData, setMapData] = useState<EmbeddingMap | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeCluster, setActiveCluster] = useState<number | null>(null);
  const [hovered, setHovered] = useState<EmbeddingMapPoint | null>(null);
  const [tooltipPos, setTooltipPos] = useState<{ x: number; y: number }>({ x: 0, y: 0 });

  useEffect(() => {
    fetchEmbeddingMap()
      .then(data => setMapData(data))
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false));
  }, []);

  const filteredPoints = useMemo(() => {
    if (!mapData) return [];
    if (activeCluster === null) return mapData.points;
    return mapData.points.filter(p => p.cluster === activeCluster);
  }, [mapData, activeCluster]);

  const bounds = useMemo(() => {
    if (!mapData || mapData.points.length === 0) return { minX: -100, maxX: 100, minY: -100, maxY: 100 };
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const p of mapData.points) {
      if (p.x < minX) minX = p.x;
      if (p.x > maxX) maxX = p.x;
      if (p.y < minY) minY = p.y;
      if (p.y > maxY) maxY = p.y;
    }
    const padX = (maxX - minX) * 0.05;
    const padY = (maxY - minY) * 0.05;
    return { minX: minX - padX, maxX: maxX + padX, minY: minY - padY, maxY: maxY + padY };
  }, [mapData]);

  const initialViewState = useMemo(() => {
    const width = bounds.maxX - bounds.minX;
    const height = bounds.maxY - bounds.minY;
    // Fit to container (assume ~600px wide, 420px tall as minimum)
    const zoomX = Math.log2(600 / (width || 1));
    const zoomY = Math.log2(420 / (height || 1));
    const zoom = Math.min(zoomX, zoomY);
    return {
      target: [(bounds.minX + bounds.maxX) / 2, (bounds.minY + bounds.maxY) / 2, 0] as [number, number, number],
      zoom,
      minZoom: zoom - 2,
      maxZoom: zoom + 8,
    };
  }, [bounds]);

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
    onHover: (info: { object?: EmbeddingMapPoint; x?: number; y?: number }) => {
      setHovered(info.object || null);
      if (info.x !== undefined && info.y !== undefined) {
        setTooltipPos({ x: info.x, y: info.y });
      }
    },
    onClick: (info: { object?: EmbeddingMapPoint }) => {
      if (info.object && info.object.cluster >= 0 && mapData) {
        const cluster = mapData.clusters.find(c => c.id === info.object!.cluster);
        if (cluster) onNavigateToTopic(cluster.slug);
      }
    },
  }), [filteredPoints, getColor, mapData, onNavigateToTopic]);

  const sortedClusters = useMemo(() => {
    if (!mapData) return [];
    return [...mapData.clusters]
      .filter(c => c.id >= 0)
      .sort((a, b) => b.size - a.size);
  }, [mapData]);

  if (loading) {
    return (
      <div className="bg-bg-secondary border border-border rounded-xl p-8 text-center text-text-secondary text-sm">
        Loading embedding map...
      </div>
    );
  }

  if (error || !mapData) {
    return (
      <div className="bg-bg-secondary border border-border rounded-xl p-8 text-center text-text-secondary text-sm">
        {error ? `Error: ${error}` : 'No data available'}
      </div>
    );
  }

  return (
    <div className="mb-8">
      <h3 className="text-sm font-semibold text-text-primary mb-3">Paper Embedding Map</h3>
      <p className="text-xs text-text-secondary mb-3">
        {mapData.paper_count.toLocaleString()} papers projected into 2D space. Colors represent topic clusters.
        {activeCluster !== null && (
          <button
            onClick={() => setActiveCluster(null)}
            className="ml-2 text-accent hover:underline cursor-pointer"
          >
            Show all
          </button>
        )}
      </p>

      {/* Map container */}
      <div className="relative bg-bg-tertiary border border-border rounded-xl overflow-hidden" style={{ height: 420 }}>
        <DeckGL
          views={new OrthographicView({ id: 'ortho' })}
          initialViewState={initialViewState}
          controller={true}
          layers={[layer]}
          style={{ position: 'absolute', inset: '0' }}
          getCursor={({ isHovering }: { isHovering: boolean }) => isHovering ? 'pointer' : 'grab'}
        />

        {/* Tooltip */}
        {hovered && (
          <div
            className="absolute pointer-events-none bg-bg-primary border border-border rounded-lg px-3 py-2 shadow-lg max-w-64 z-10"
            style={{ left: tooltipPos.x + 12, top: tooltipPos.y - 12 }}
          >
            <p className="text-xs font-medium text-text-primary leading-snug line-clamp-2">{hovered.title}</p>
            <p className="text-[10px] text-text-secondary mt-1">
              {hovered.year}{hovered.cluster >= 0 ? ` · Cluster ${hovered.cluster}` : ' · Unclustered'}
            </p>
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
          {sortedClusters.slice(0, 40).map(cluster => {
            const color = CLUSTER_COLORS[cluster.id % CLUSTER_COLORS.length];
            const isActive = activeCluster === cluster.id;
            return (
              <button
                key={cluster.id}
                onClick={() => setActiveCluster(isActive ? null : cluster.id)}
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
          {sortedClusters.length > 40 && (
            <span className="text-[10px] text-text-secondary px-2 py-1">
              + {sortedClusters.length - 40} more
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
