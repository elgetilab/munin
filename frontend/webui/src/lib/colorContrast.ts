// WCAG 2 contrast for the colour strings our CSS uses: #rgb, #rrggbb and
// hsl(h, s%, l%). The tests use it to hold palettes to 4.5:1.

export type Rgb = [number, number, number];

export function parseColor(value: string): Rgb {
  const v = value.trim().toLowerCase();
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/.exec(v);
  if (hex) {
    const h = hex[1].length === 3 ? hex[1].split('').map((c) => c + c).join('') : hex[1];
    return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as Rgb;
  }
  const hsl = /^hsl\(\s*([\d.]+)\s*,\s*([\d.]+)%\s*,\s*([\d.]+)%\s*\)$/.exec(v);
  if (hsl) {
    const h = Number(hsl[1]);
    const s = Number(hsl[2]) / 100;
    const l = Number(hsl[3]) / 100;
    const a = s * Math.min(l, 1 - l);
    const f = (n: number) => {
      const k = (n + h / 30) % 12;
      return Math.round((l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1))) * 255);
    };
    return [f(0), f(8), f(4)];
  }
  throw new Error(`Unsupported colour: ${value}`);
}

function luminance([r, g, b]: Rgb): number {
  const channel = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

/** Contrast ratio between two colours, 1 to 21. */
export function contrastRatio(a: string, b: string): number {
  const la = luminance(parseColor(a));
  const lb = luminance(parseColor(b));
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}
