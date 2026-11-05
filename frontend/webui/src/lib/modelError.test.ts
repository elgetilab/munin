import { friendlyError, rewordInterruptedMarker } from './modelError';

describe('friendlyError', () => {
  it.each(['vLLM unavailable', 'vLLM stream error', 'upstream 503', 'HTTP 502 from gateway'])(
    'rewords a model server error: %s',
    (raw) => {
      expect(friendlyError(raw)).toBe('The model is unavailable right now');
    },
  );

  it('keeps other errors as they are', () => {
    expect(friendlyError('Conversation not found')).toBe('Conversation not found');
  });
});

describe('rewordInterruptedMarker', () => {
  it('rewords the marker and keeps the text before it', () => {
    expect(rewordInterruptedMarker('Partial answer\n\n_(stream interrupted: vLLM unavailable)_'))
      .toBe('Partial answer\n\n_(This answer was cut off. The model is unavailable right now.)_');
  });

  it('keeps a reason that is not about the model server', () => {
    expect(rewordInterruptedMarker('_(stream interrupted: Error in input stream)_'))
      .toBe('_(This answer was cut off. Error in input stream.)_');
  });

  it('leaves the same words alone when they are not the trailing marker', () => {
    const text = 'The log said stream interrupted: vLLM unavailable, then recovered.';
    expect(rewordInterruptedMarker(text)).toBe(text);
  });
});
