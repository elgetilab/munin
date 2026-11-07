import { test, expect } from '@playwright/test';
import { contrastMath, formatFailures, measureContrast } from './fixtures/contrast';

// The guard behind contrast.spec.ts, checked on its own: the colour maths
// against known WCAG figures, then the page walk on a hand-made page. A guard
// that is wrong passes everything, so these pin what it catches and skips.
const M = contrastMath();
const ratioOf = (a: string, b: string) => M.ratio(M.parse(a)!, M.parse(b)!);

test.describe('contrast maths', () => {
  test('black on white is 21:1 and a colour on itself is 1:1', () => {
    expect(ratioOf('rgb(0, 0, 0)', 'rgb(255, 255, 255)')).toBeCloseTo(21, 5);
    expect(ratioOf('rgb(88, 166, 255)', 'rgb(88, 166, 255)')).toBeCloseTo(1, 5);
  });

  test('matches known pairs either side of 4.5:1', () => {
    // #767676 on white is the usual lightest passing grey, #777777 just fails.
    expect(ratioOf('rgb(118, 118, 118)', 'rgb(255, 255, 255)')).toBeCloseTo(4.54, 2);
    expect(ratioOf('rgb(119, 119, 119)', 'rgb(255, 255, 255)')).toBeLessThan(4.5);
    // The dark theme's body text on its page colour (src/index.css).
    expect(ratioOf('rgb(230, 237, 243)', 'rgb(15, 20, 25)')).toBeGreaterThan(15);
  });

  test('reads every form a browser computes a colour in', () => {
    expect(M.parse('rgb(10, 20, 30)')).toEqual([10, 20, 30, 1]);
    expect(M.parse('rgba(10, 20, 30, 0.5)')).toEqual([10, 20, 30, 0.5]);
    expect(M.parse('rgb(10 20 30 / 0.25)')).toEqual([10, 20, 30, 0.25]);
    expect(M.parse('transparent')).toEqual([0, 0, 0, 0]);
    expect(M.parse('color(srgb 1 0 0.5 / 0.5)')).toEqual([255, 0, 128, 0.5]);
    // White and black in oklab and oklch.
    const white = M.parse('oklab(1 0 0)')!;
    expect(white.slice(0, 3)).toEqual([255, 255, 255]);
    const black = M.parse('oklch(0 0 0)')!;
    expect(black.slice(0, 3)).toEqual([0, 0, 0]);
    // Tailwind v4 blue-500 in oklch lands on its sRGB fallback, #2b7fff.
    const blue = M.parse('oklch(0.623 0.214 259.815)')!;
    expect(Math.abs(blue[0] - 0x2b)).toBeLessThanOrEqual(3);
    expect(Math.abs(blue[1] - 0x7f)).toBeLessThanOrEqual(3);
    expect(Math.abs(blue[2] - 0xff)).toBeLessThanOrEqual(3);
    expect(M.parse('currentcolor')).toBeNull();
  });

  test('blends a see-through colour over what is behind it', () => {
    expect(M.over([255, 255, 255, 0.5], [0, 0, 0, 1])).toEqual([127.5, 127.5, 127.5, 1]);
    expect(M.over([10, 20, 30, 1], [200, 200, 200, 1])).toEqual([10, 20, 30, 1]);
    expect(M.over([10, 20, 30, 0], [200, 200, 200, 1])).toEqual([200, 200, 200, 1]);
  });

  test('large text is 24px, or 18.66px and bold', () => {
    expect(M.isLarge(24, 400)).toBe(true);
    expect(M.isLarge(23.9, 400)).toBe(false);
    expect(M.isLarge(18.66, 700)).toBe(true);
    expect(M.isLarge(18.66, 600)).toBe(false);
    expect(M.isLarge(18, 700)).toBe(false);
  });

  test('a failure reads as where, what and the numbers', () => {
    expect(formatFailures([{ selector: 'p.note', text: 'Hi', fg: '#777777', bg: '#ffffff', ratio: 4.48, need: 4.5 }]))
      .toBe('4.48:1 (needs 4.5) #777777 on #ffffff  p.note  "Hi"');
  });
});

test.describe('contrast walk', () => {
  const PAGE = `<!doctype html>
    <body style="margin:0;background:#ffffff;color:#000;font:16px/1.4 sans-serif">
      <p id="good" style="color:#595959">Readable grey</p>
      <p id="bad" style="color:#999999">Faint grey</p>
      <p id="large" style="color:#949494;font-size:24px">Large and lighter</p>
      <p id="small-bold" style="color:#949494;font-size:18px;font-weight:700">Bold but not large</p>
      <div style="background:#0f1419"><span id="on-dark" style="color:#808891">Tertiary on dark</span></div>
      <div style="background:rgba(0,0,0,0.5)"><span id="see-through" style="color:#333333">On half black</span></div>
      <p id="faded" style="color:#000;opacity:0.3">Faded by opacity</p>
      <span id="sr" style="position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap;color:#eee">Screen readers only</span>
      <div style="background:linear-gradient(#fff,#eee)"><span style="color:#eee">Over a gradient</span></div>
      <button disabled style="color:#ddd;background:#fff">Disabled</button>
      <input id="ph" placeholder="Faint placeholder" style="color:#000;background:#fff">
      <style>#ph::placeholder { color: #bbbbbb; }</style>
      <div style="position:relative">
        <span id="under" style="color:#eee">Under a menu</span>
        <div style="position:absolute;inset:0;background:#000"></div>
      </div>
    </body>`;

  test('fails faint text, passes readable text, and leaves out what it cannot judge', async ({ page }) => {
    await page.setContent(PAGE);
    const report = await measureContrast(page);
    const failed = report.failures.map((f) => f.text).sort();
    expect(failed).toEqual([
      'Bold but not large', 'Faded by opacity', 'Faint grey', 'Faint placeholder', 'On half black',
    ].sort());
    // #999 on white is 2.85:1.
    const faint = report.failures.find((f) => f.text === 'Faint grey')!;
    expect(faint).toMatchObject({ fg: '#999999', bg: '#ffffff', need: 4.5 });
    expect(faint.ratio).toBeCloseTo(2.85, 2);
    expect(report.skipped).toMatchObject({
      'visually hidden': 1, 'over an image or gradient': 1, 'disabled or ignored': 1, covered: 1,
    });
  });

  test('can be pointed at one part of the page, or told to leave a part out', async ({ page }) => {
    await page.setContent(PAGE);
    expect((await measureContrast(page, { root: '#bad' })).failures.map((f) => f.text)).toEqual(['Faint grey']);
    const rest = await measureContrast(page, { ignore: ['#bad', '#faded', '#see-through', '#ph', '#small-bold'] });
    expect(rest.failures).toEqual([]);
  });
});
