import { describe, expect, it } from 'vitest';

import { errorMessage } from './api';

describe('errorMessage', () => {
  it("reads the backend's HTTPException envelope", () => {
    expect(errorMessage({ detail: { error: { message: 'Deep Research is busy' } } }))
      .toBe('Deep Research is busy');
  });

  it('reads the direct {error: {message}} shape', () => {
    expect(errorMessage({ error: { message: 'File too large' } })).toBe('File too large');
  });

  it("reads the auth service's {error: string}", () => {
    expect(errorMessage({ error: 'Not authenticated' })).toBe('Not authenticated');
  });

  it("reads FastAPI's own {detail: string}", () => {
    expect(errorMessage({ detail: 'Not Found' })).toBe('Not Found');
  });

  it('returns empty for anything else', () => {
    expect(errorMessage({})).toBe('');
    expect(errorMessage(null)).toBe('');
    expect(errorMessage('oops')).toBe('');
    expect(errorMessage({ detail: [{ loc: ['body'], msg: 'x' }] })).toBe('');
  });
});
