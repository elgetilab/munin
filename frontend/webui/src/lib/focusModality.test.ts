import { lastInputWasKeyboard, trackFocusModality } from './focusModality';

describe('focusModality', () => {
  beforeAll(() => {
    trackFocusModality();
    trackFocusModality(); // twice is harmless
  });

  it('follows the last input: a key, then the pointer', () => {
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab' }));
    expect(lastInputWasKeyboard()).toBe(true);
    document.dispatchEvent(new Event('pointerdown'));
    expect(lastInputWasKeyboard()).toBe(false);
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown' }));
    expect(lastInputWasKeyboard()).toBe(true);
    document.dispatchEvent(new MouseEvent('mousedown'));
    expect(lastInputWasKeyboard()).toBe(false);
  });

  it('ignores shortcuts held with Ctrl, Alt or Meta', () => {
    document.dispatchEvent(new Event('pointerdown'));
    for (const mod of ['ctrlKey', 'altKey', 'metaKey'] as const) {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', [mod]: true }));
      expect(lastInputWasKeyboard()).toBe(false);
    }
  });

  it('hears a key whose handler stops it', () => {
    document.dispatchEvent(new Event('pointerdown'));
    const stop = (e: Event) => e.stopPropagation();
    document.body.addEventListener('keydown', stop);
    document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    document.body.removeEventListener('keydown', stop);
    expect(lastInputWasKeyboard()).toBe(true);
  });
});
