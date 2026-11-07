// Chart colours for both themes.
//
// The metrics charts are SVG, so they use the --chart-* variables from
// src/styles/charts.css and follow a theme switch with no re-render.
//
// The paper map is drawn by deck.gl in WebGL, which cannot read CSS, so its colours
// live here as RGB and are picked from useTheme(). The topic chips under the map use
// the same colours, so a chip's dot matches the dots on the map.

import type { Theme } from './theme';

export type Rgb = [number, number, number];
export type Rgba = [number, number, number, number];

export const CHART_SERIES_COUNT = 12;

/** The CSS colour for the nth series, cycling through --chart-1 to --chart-12. */
export function seriesColor(index: number): string {
  return `var(--chart-${(index % CHART_SERIES_COUNT) + 1})`;
}

// Dark: the colours the map has always used, drawn at 200 of 255 alpha.
const CLUSTER_DARK: Rgb[] = [
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

// Light: the same hues, darker, so that every dot reaches 3:1 on the map's
// background (#e9edf1) at the light alpha. Lowest is 3.20:1 (orange-light).
const CLUSTER_LIGHT: Rgb[] = [
  [79, 70, 229],    // indigo
  [4, 120, 87],     // emerald
  [180, 83, 9],     // amber
  [220, 38, 38],    // red
  [37, 99, 235],    // blue
  [147, 51, 234],   // purple
  [219, 39, 119],   // pink
  [15, 118, 110],   // teal
  [194, 65, 12],    // orange
  [77, 124, 15],    // lime
  [14, 116, 144],   // cyan
  [192, 38, 211],   // fuchsia
  [161, 98, 7],     // yellow
  [21, 128, 61],    // green
  [225, 29, 72],    // rose
  [67, 56, 202],    // violet
  [3, 105, 161],    // sky
  [204, 79, 10],    // orange-light
  [63, 98, 18],     // lime-light
  [11, 128, 116],   // teal-light
];

export const CLUSTER_COLORS: Record<Theme, Rgb[]> = { dark: CLUSTER_DARK, light: CLUSTER_LIGHT };

/** Alpha for a clustered dot. Light draws a little more solid to keep 3:1. */
export const CLUSTER_ALPHA: Record<Theme, number> = { dark: 200, light: 220 };

// Papers in no cluster are background, kept faint on purpose in both themes.
export const UNCLUSTERED_COLOR: Rgba = [100, 100, 100, 80];

export function clusterColor(clusterId: number, theme: Theme): Rgb {
  const palette = CLUSTER_COLORS[theme];
  return palette[((clusterId % palette.length) + palette.length) % palette.length];
}

/** The fill for a dot on the map. A cluster id of -1 means no cluster. */
export function pointFill(clusterId: number, theme: Theme): Rgba {
  if (clusterId === -1) return UNCLUSTERED_COLOR;
  return [...clusterColor(clusterId, theme), CLUSTER_ALPHA[theme]];
}

/** The same colour as a CSS string, for the topic chips. */
export function clusterCss(clusterId: number, theme: Theme): string {
  const [r, g, b] = clusterColor(clusterId, theme);
  return `rgb(${r}, ${g}, ${b})`;
}
