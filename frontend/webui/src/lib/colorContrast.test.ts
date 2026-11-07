import { describe, expect, it } from 'vitest';
import { contrastRatio, parseColor } from './colorContrast';

describe('colorContrast', () => {
  it('parses hex and hsl colours', () => {
    expect(parseColor('#fff')).toEqual([255, 255, 255]);
    expect(parseColor('#0f1419')).toEqual([15, 20, 25]);
    expect(parseColor('hsl(220, 13%, 18%)')).toEqual([40, 44, 52]);
    expect(parseColor('hsl(220, 14%, 71%)')).toEqual([171, 178, 191]);
  });

  it('gives the WCAG ratio', () => {
    expect(contrastRatio('#000000', '#ffffff')).toBeCloseTo(21, 5);
    expect(contrastRatio('#ffffff', '#ffffff')).toBeCloseTo(1, 5);
    // The light accent on white, as given in docs/UI-POLISH-AND-LIGHT-MODE.md.
    expect(contrastRatio('#0a5cb8', '#ffffff')).toBeCloseTo(6.49, 1);
  });

  it('rejects colours it cannot read', () => {
    expect(() => parseColor('var(--x)')).toThrow();
  });
});
