import { routedLabel, typedMode, withMode } from './modes';

describe('modes', () => {
  it('leads a forced mode with its command, and leaves Auto and typed commands alone', () => {
    expect(withMode('hi', 'auto')).toBe('hi');
    expect(withMode('hi', 'research')).toBe('/research hi');
    expect(withMode('/code x', 'research')).toBe('/code x');
    expect(withMode('/Chat x', 'code')).toBe('/Chat x');
  });

  it('reads a typed command only at the start and as a whole word', () => {
    expect(typedMode('/research x')).toBe('research');
    expect(typedMode('  /CODE')).toBe('code');
    expect(typedMode('/researcher x')).toBeNull();
    expect(typedMode('see /code')).toBeNull();
  });

  it('labels routed profiles, and gives plain chat none', () => {
    expect(routedLabel('research')).toBe('Research');
    expect(routedLabel('code')).toBe('Code');
    expect(routedLabel('chat')).toBeNull();
    expect(routedLabel('munin')).toBeNull();
    expect(routedLabel(null)).toBeNull();
  });
});

describe('splitMode', () => {
  it('takes a leading command off the shown text and names its mode', async () => {
    const { splitMode } = await import('./modes');
    expect(splitMode('/research GPCR desensitisation')).toEqual({ mode: 'research', text: 'GPCR desensitisation' });
    expect(splitMode('plain question')).toEqual({ mode: null, text: 'plain question' });
    expect(splitMode('/chat')).toEqual({ mode: 'chat', text: '' });
  });
});
