import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  CHART_SERIES_COUNT, CLUSTER_ALPHA, CLUSTER_COLORS, UNCLUSTERED_COLOR,
  clusterColor, clusterCss, pointFill, seriesColor,
} from './chartPalette';
import type { Rgb } from './chartPalette';

const SRC = resolve(__dirname, '..');
const chartsCss = readFileSync(resolve(SRC, 'styles/charts.css'), 'utf8');
const indexCss = readFileSync(resolve(SRC, 'index.css'), 'utf8');

// The variables set in the first block whose selector matches exactly.
function block(css: string, selector: string): Record<string, string> {
  const start = css.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`no ${selector} block`);
  const body = css.slice(start, css.indexOf('}', start));
  return Object.fromEntries([...body.matchAll(/(--[\w-]+):\s*([^;]+);/g)].map(m => [m[1], m[2].trim()]));
}

function hex(h: string): Rgb {
  return [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16)) as Rgb;
}
function luminance([r, g, b]: Rgb): number {
  const lin = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}
function contrast(a: Rgb, b: Rgb): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}
// What a dot drawn at this alpha looks like on the background.
function over(fg: Rgb, alpha: number, bg: Rgb): Rgb {
  const a = alpha / 255;
  return fg.map((c, i) => Math.round(c * a + bg[i] * (1 - a))) as Rgb;
}

const lightTokens = block(indexCss, ':root[data-theme="light"]');
const darkCharts = block(chartsCss, ':root');
const lightCharts = block(chartsCss, ':root[data-theme="light"]');
const series = Array.from({ length: CHART_SERIES_COUNT }, (_, i) => `--chart-${i + 1}`);

describe('seriesColor', () => {
  it('names the chart variables and cycles after twelve', () => {
    expect(seriesColor(0)).toBe('var(--chart-1)');
    expect(seriesColor(11)).toBe('var(--chart-12)');
    expect(seriesColor(12)).toBe('var(--chart-1)');
  });
});

describe('chart series colours', () => {
  it('defines all twelve in both themes', () => {
    for (const name of series) {
      expect(darkCharts[name]).toMatch(/^#[0-9a-f]{6}$/);
      expect(lightCharts[name]).toMatch(/^#[0-9a-f]{6}$/);
    }
  });

  it('keeps the dark colours the charts have always had', () => {
    expect(series.map(n => darkCharts[n])).toEqual([
      '#58a6ff', '#3fb950', '#d29922', '#f85149',
      '#a371f7', '#e3b341', '#56d4dd', '#ff7b72',
      '#79c0ff', '#85e89d', '#ffa657', '#f0883e',
    ]);
  });

  // The legend and the tooltip rows are text in the series colour, on the panel
  // (bg-secondary) and the page (bg-primary).
  it('gives light legend text 4.5:1 on the panel and the page', () => {
    for (const bg of ['--color-bg-secondary', '--color-bg-primary']) {
      for (const name of series) {
        expect(contrast(hex(lightCharts[name]), hex(lightTokens[bg])), `${name} on ${bg}`).toBeGreaterThanOrEqual(4.5);
      }
    }
  });

  it('keeps the light colours distinct', () => {
    expect(new Set(series.map(n => lightCharts[n])).size).toBe(CHART_SERIES_COUNT);
  });
});

describe('cluster colours', () => {
  it('keeps the dark map exactly as it was', () => {
    expect(CLUSTER_ALPHA.dark).toBe(200);
    expect(UNCLUSTERED_COLOR).toEqual([100, 100, 100, 80]);
    expect(CLUSTER_COLORS.dark[0]).toEqual([99, 102, 241]);
    expect(CLUSTER_COLORS.dark[19]).toEqual([45, 212, 191]);
    expect(pointFill(3, 'dark')).toEqual([239, 68, 68, 200]);
    expect(pointFill(-1, 'dark')).toEqual([100, 100, 100, 80]);
  });

  it('has twenty distinct colours in each theme', () => {
    for (const theme of ['dark', 'light'] as const) {
      expect(CLUSTER_COLORS[theme]).toHaveLength(20);
      expect(new Set(CLUSTER_COLORS[theme].map(c => c.join())).size).toBe(20);
    }
  });

  it('gives every light dot 3:1 on the map background', () => {
    const mapBg = hex(lightTokens['--color-bg-tertiary']);
    for (const color of CLUSTER_COLORS.light) {
      expect(contrast(over(color, CLUSTER_ALPHA.light, mapBg), mapBg), color.join()).toBeGreaterThanOrEqual(3);
    }
  });

  it('gives every light chip dot 3:1 on the chip', () => {
    const chipBg = hex(lightTokens['--color-bg-secondary']);
    for (const color of CLUSTER_COLORS.light) {
      expect(contrast(color, chipBg), color.join()).toBeGreaterThanOrEqual(3);
    }
  });

  it('cycles past twenty and tolerates odd ids', () => {
    expect(clusterColor(20, 'light')).toEqual(clusterColor(0, 'light'));
    expect(clusterColor(-2, 'dark')).toEqual(CLUSTER_COLORS.dark[18]);
  });

  it('gives the chips the same colour as the dots', () => {
    const [r, g, b] = pointFill(5, 'light');
    expect(clusterCss(5, 'light')).toBe(`rgb(${r}, ${g}, ${b})`);
    expect(clusterCss(0, 'dark')).toBe('rgb(99, 102, 241)');
  });
});
