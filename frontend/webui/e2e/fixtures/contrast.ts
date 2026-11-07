import type { Page } from '@playwright/test';

// A WCAG contrast check for what is on screen: every visible piece of text, form
// values and placeholders included, against what is actually painted behind it.
// Text needs 4.5:1, large text 3:1 (24px, or 18.66px and bold).
//
// What it does not judge, and so leaves out rather than guessing:
// - text over an image or gradient, and text drawn on a canvas or in a frame
//   (the protein viewer, widget frames);
// - disabled controls, which WCAG exempts;
// - text under something opaque (a scrim or a menu), and text that is mid-way
//   through an endless animation (a pulsing "thinking" line).

export interface ContrastFailure {
  selector: string;
  text: string;
  fg: string;
  bg: string;
  ratio: number;
  need: number;
}

export interface ContrastReport {
  checked: number;
  failures: ContrastFailure[];
  skipped: Record<string, number>;
}

type Rgba = [number, number, number, number];

/**
 * Colour maths, self-contained so it can also run inside the page: it is sent
 * there as source text. Keep it free of imports and outside references.
 */
export function contrastMath() {
  const clamp01 = (v: number) => Math.min(1, Math.max(0, v));
  const toByte = (v: number) => Math.round(clamp01(v) * 255);
  const encode = (c: number) => (c <= 0.0031308 ? 12.92 * c : 1.055 * Math.pow(c, 1 / 2.4) - 0.055);
  const decode = (c: number) => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));

  const num = (s: string, scale: number) => {
    const t = s.trim();
    if (t === 'none') return 0;
    return t.endsWith('%') ? (parseFloat(t) / 100) * scale : parseFloat(t);
  };

  function oklabToRgb(L: number, a: number, b: number): [number, number, number] {
    const l_ = L + 0.3963377774 * a + 0.2158037573 * b;
    const m_ = L - 0.1055613458 * a - 0.0638541728 * b;
    const s_ = L - 0.0894841775 * a - 1.291485548 * b;
    const l = l_ ** 3, m = m_ ** 3, s = s_ ** 3;
    return [
      toByte(encode(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s)),
      toByte(encode(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s)),
      toByte(encode(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s)),
    ];
  }

  /** A computed colour as [r, g, b, a] with r, g, b in 0-255, or null if the form is unknown. */
  function parse(css: string): Rgba | null {
    const s = css.trim().toLowerCase();
    if (s === 'transparent') return [0, 0, 0, 0];
    const m = s.match(/^([a-z]+)\((.*)\)$/);
    if (!m) return null;
    const [, fn, body] = m;
    // Both "r, g, b, a" and "r g b / a".
    const [main, slash] = body.split('/');
    let parts = main.split(/[\s,]+/).filter(Boolean);
    let alpha = slash !== undefined ? num(slash, 1) : 1;
    if (fn === 'rgb' || fn === 'rgba') {
      if (parts.length === 4) { alpha = num(parts[3], 1); parts = parts.slice(0, 3); }
      return [num(parts[0], 255), num(parts[1], 255), num(parts[2], 255), alpha];
    }
    if (fn === 'color' && parts[0] === 'srgb') {
      return [toByte(num(parts[1], 1)), toByte(num(parts[2], 1)), toByte(num(parts[3], 1)), alpha];
    }
    if (fn === 'oklab') {
      return [...oklabToRgb(num(parts[0], 1), num(parts[1], 0.4), num(parts[2], 0.4)), alpha];
    }
    if (fn === 'oklch') {
      const h = (num(parts[2], 1) * Math.PI) / 180;
      const c = num(parts[1], 0.4);
      return [...oklabToRgb(num(parts[0], 1), c * Math.cos(h), c * Math.sin(h)), alpha];
    }
    return null;
  }

  /** `top` painted over an opaque `bottom`. */
  function over(top: Rgba, bottom: Rgba): Rgba {
    const a = top[3];
    return [
      top[0] * a + bottom[0] * (1 - a),
      top[1] * a + bottom[1] * (1 - a),
      top[2] * a + bottom[2] * (1 - a),
      1,
    ];
  }

  function luminance(c: Rgba): number {
    const [r, g, b] = c.slice(0, 3).map(v => decode(v / 255));
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  }

  function ratio(fg: Rgba, bg: Rgba): number {
    const a = luminance(fg), b = luminance(bg);
    return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
  }

  function isLarge(fontSizePx: number, fontWeight: number): boolean {
    return fontSizePx >= 24 || (fontSizePx >= 18.66 && fontWeight >= 700);
  }

  function hex(c: Rgba): string {
    return '#' + c.slice(0, 3).map(v => Math.round(v).toString(16).padStart(2, '0')).join('');
  }

  return { parse, over, luminance, ratio, isLarge, hex };
}

interface WalkOptions {
  mathSrc: string;
  root: string;
  ignore: string[];
}

/** Runs in the page. */
function walk({ mathSrc, root, ignore }: WalkOptions): ContrastReport {
  const M = new Function(`return (${mathSrc})()`)() as ReturnType<typeof contrastMath>;
  const report: ContrastReport = { checked: 0, failures: [], skipped: {} };
  const skip = (why: string) => { report.skipped[why] = (report.skipped[why] ?? 0) + 1; };
  const WHITE: Rgba = [255, 255, 255, 1];

  // Finish what will finish (a fade-in), and note what never will.
  const endless = new Set<Element>();
  for (const a of document.getAnimations()) {
    const t = (a.effect as KeyframeEffect | null)?.target;
    const timing = a.effect?.getComputedTiming();
    if (timing && timing.endTime === Infinity) {
      if (t) endless.add(t);
    } else {
      try { a.finish(); } catch { /* already done */ }
    }
  }
  const inEndless = (el: Element) => {
    for (const t of endless) if (t === el || t.contains(el)) return true;
    return false;
  };

  const opacityCache = new Map<Element, number>();
  const cumOpacity = (el: Element | null): number => {
    if (!el) return 1;
    const hit = opacityCache.get(el);
    if (hit !== undefined) return hit;
    const v = parseFloat(getComputedStyle(el).opacity || '1') * cumOpacity(el.parentElement);
    opacityCache.set(el, v);
    return v;
  };

  const describe = (el: Element): string => {
    const one = (e: Element) => {
      let s = e.tagName.toLowerCase();
      const testid = e.getAttribute('data-testid');
      if (testid) return `${s}[data-testid="${testid}"]`;
      if (e.id) return `${s}#${e.id}`;
      const cls = typeof e.className === 'string' ? e.className.split(/\s+/).filter(c => c && !c.includes(':')).slice(0, 4) : [];
      if (cls.length) s += '.' + cls.join('.');
      return s;
    };
    const chain: string[] = [];
    let e: Element | null = el;
    while (e && chain.length < 3 && e !== document.body) {
      chain.unshift(one(e));
      if (e.getAttribute('data-testid') || e.id) break;
      e = e.parentElement;
    }
    return chain.join(' > ');
  };

  // The colour behind the text: the layers, nearest first, down to an opaque one.
  const backdrop = (layers: Element[]): Rgba | 'image' | 'unknown' => {
    const stack: Rgba[] = [];
    for (const el of layers) {
      const cs = getComputedStyle(el);
      if (cs.backgroundImage && cs.backgroundImage !== 'none') return 'image';
      const c = M.parse(cs.backgroundColor);
      if (!c) return 'unknown';
      const a = c[3] * cumOpacity(el);
      if (a <= 0) continue;
      stack.push([c[0], c[1], c[2], a]);
      if (a >= 0.999) break;
    }
    let bg: Rgba = WHITE;
    for (let i = stack.length - 1; i >= 0; i--) bg = M.over(stack[i], bg);
    return bg;
  };
  const ancestors = (el: Element): Element[] => {
    const out: Element[] = [];
    for (let e: Element | null = el; e; e = e.parentElement) out.push(e);
    return out;
  };

  const seen = new Set<string>();
  const check = (el: Element, rect: DOMRect, text: string, fgCss: string, fontEl: Element) => {
    // Under something, or behind it? elementsFromPoint gives what paints there.
    const x = rect.left + rect.width / 2, y = rect.top + rect.height / 2;
    let layers: Element[] = ancestors(el);
    if (x >= 0 && y >= 0 && x < innerWidth && y < innerHeight) {
      const stack = document.elementsFromPoint(x, y);
      const at = stack.findIndex(n => n === el || el.contains(n));
      if (at >= 0) {
        for (const above of stack.slice(0, at)) {
          if (above.contains(el)) continue;
          const c = M.parse(getComputedStyle(above).backgroundColor);
          if (c && c[3] * cumOpacity(above) >= 0.3) { skip('covered'); return; }
        }
        layers = stack.slice(at);
      }
    }
    const bg = backdrop(layers);
    if (bg === 'image') { skip('over an image or gradient'); return; }
    if (bg === 'unknown') { skip('unreadable colour'); return; }
    const fgRaw = M.parse(fgCss);
    if (!fgRaw) { skip('unreadable colour'); return; }
    const fa = fgRaw[3] * cumOpacity(el);
    if (fa <= 0.01) { skip('transparent text'); return; }
    const fg = M.over([fgRaw[0], fgRaw[1], fgRaw[2], fa], bg);
    const cs = getComputedStyle(fontEl);
    const need = M.isLarge(parseFloat(cs.fontSize), parseFloat(cs.fontWeight)) ? 3 : 4.5;
    const r = M.ratio(fg, bg);
    report.checked++;
    // A hair of slack for 8-bit rounding in colour-mix results.
    if (r >= need - 0.02) return;
    const f: ContrastFailure = {
      selector: describe(el), text: text.replace(/\s+/g, ' ').trim().slice(0, 40),
      fg: M.hex(fg), bg: M.hex(bg), ratio: Math.round(r * 100) / 100, need,
    };
    const key = `${f.selector}|${f.fg}|${f.bg}`;
    if (seen.has(key)) return;
    seen.add(key);
    report.failures.push(f);
  };

  const visible = (el: Element) =>
    el.checkVisibility({ opacityProperty: true, visibilityProperty: true, contentVisibilityAuto: true });
  // Text for screen readers only (sr-only), or inside something folded shut: a
  // box of a pixel or less that clips what overflows it, or a clip-path that
  // cuts it all away. Nobody sees it, so its colours do not matter.
  const clippedAway = (el: Element) => {
    for (let e: Element | null = el; e && e !== document.body; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.clipPath === 'inset(50%)') return true;
      if (cs.overflow === 'visible' && cs.clip === 'auto') continue;
      const r = e.getBoundingClientRect();
      if (r.width <= 1 || r.height <= 1) return true;
    }
    return false;
  };
  const excluded = (el: Element) =>
    (ignore.length > 0 && el.closest(ignore.join(','))) ||
    el.closest(':disabled, [aria-disabled="true"], [inert]');

  const scope = document.querySelector(root) ?? document.body;

  // Text nodes, grouped by the element that styles them.
  const byParent = new Map<Element, Text[]>();
  const tw = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT);
  for (let n = tw.nextNode(); n; n = tw.nextNode()) {
    const t = n as Text;
    const p = t.parentElement;
    if (!p || !t.data.trim()) continue;
    if (['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'OPTION', 'TITLE'].includes(p.tagName)) continue;
    const list = byParent.get(p) ?? [];
    list.push(t);
    byParent.set(p, list);
  }
  for (const [el, texts] of byParent) {
    if (!visible(el)) continue;
    if (clippedAway(el)) { skip('visually hidden'); continue; }
    if (excluded(el)) { skip('disabled or ignored'); continue; }
    if (inEndless(el)) { skip('animating'); continue; }
    for (const t of texts) {
      const range = document.createRange();
      range.selectNodeContents(t);
      const rect = [...range.getClientRects()].find(r => r.width > 1 && r.height > 1);
      if (!rect) continue;
      const cs = getComputedStyle(el);
      // SVG text is painted with fill.
      const fgCss = el instanceof SVGElement ? cs.fill : (cs.webkitTextFillColor || cs.color);
      check(el, rect, t.data, fgCss, el);
      break;
    }
  }

  // What a control shows that is not a text node: its value, or its placeholder.
  for (const el of scope.querySelectorAll('input, textarea, select')) {
    if (!visible(el)) continue;
    if (excluded(el)) { skip('disabled or ignored'); continue; }
    const rect = el.getBoundingClientRect();
    if (rect.width <= 1 || rect.height <= 1) continue;
    if (el instanceof HTMLSelectElement) {
      const label = el.selectedOptions[0]?.text ?? '';
      if (label.trim()) check(el, rect, label, getComputedStyle(el).color, el);
      continue;
    }
    const input = el as HTMLInputElement | HTMLTextAreaElement;
    if (['checkbox', 'radio', 'range', 'color', 'file', 'hidden', 'submit', 'button', 'image'].includes(input.type)) continue;
    if (input.value) check(el, rect, input.value, getComputedStyle(el).color, el);
    else if (input.placeholder) check(el, rect, input.placeholder, getComputedStyle(el, '::placeholder').color, el);
  }
  return report;
}

/** Walk the page (or `root`) and return every text that falls short. */
export async function measureContrast(page: Page, opts: { root?: string; ignore?: string[] } = {}): Promise<ContrastReport> {
  return page.evaluate(walk, {
    mathSrc: contrastMath.toString(),
    root: opts.root ?? 'body',
    ignore: opts.ignore ?? [],
  });
}

/** A failure list a person can act on: where, what, and the numbers. */
export function formatFailures(failures: ContrastFailure[]): string {
  return failures
    .map(f => `${f.ratio.toFixed(2)}:1 (needs ${f.need}) ${f.fg} on ${f.bg}  ${f.selector}  "${f.text}"`)
    .join('\n');
}
