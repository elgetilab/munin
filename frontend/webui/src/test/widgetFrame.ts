import { act } from '@testing-library/react';

// jsdom does not run a frame's script, so tests give the frame a stand-in window
// and play its part. e2e/widgets.spec.ts covers the real thing.
export function stubWidgetFrame(frame: HTMLElement) {
  const target = { postMessage: vi.fn() };
  Object.defineProperty(frame, 'contentWindow', { value: target, configurable: true });
  const send = (data: unknown, source: unknown = target) => act(() => {
    const event = new MessageEvent('message', { data });
    Object.defineProperty(event, 'source', { value: source });
    window.dispatchEvent(event);
  });
  return { post: target.postMessage, send };
}
