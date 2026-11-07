import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useTheme, type Theme } from '../lib/theme';

export interface WidgetEvent {
  type: string;
  [key: string]: unknown;
}

interface WidgetFrameProps {
  // From buildWidgetDocument. A different document needs a different key.
  srcDoc: string;
  title: string;
  // Sent to the widget once it says it is ready, and again whenever it changes.
  state: unknown;
  onEvent?: (event: WidgetEvent) => void;
  className?: string;
  // The frame cannot see the theme cookie or the app's CSS, so the theme goes
  // with the state: { type: 'state', state, theme }. It follows the app unless
  // this pins it, as the protein viewer does with 'dark'.
  theme?: Theme;
}

export function WidgetFrame({ srcDoc, title, state, onEvent, className, theme: pinnedTheme }: WidgetFrameProps) {
  const frameRef = useRef<HTMLIFrameElement>(null);
  const [ready, setReady] = useState(false);
  const { theme: appTheme } = useTheme();
  const theme = pinnedTheme ?? appTheme;

  // In the same task that inserts the frame, before its script can run. With a
  // later effect its 'ready' could be missed, and the widget would never get its
  // state.
  useLayoutEffect(() => {
    const onMessage = (event: MessageEvent) => {
      // Only this frame's own window counts. Its origin is opaque, so there is
      // no origin to compare.
      if (event.source !== frameRef.current?.contentWindow) return;
      const data = event.data as WidgetEvent | null;
      if (typeof data?.type !== 'string') return;
      if (data.type === 'ready') setReady(true);
      else onEvent?.(data);
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [onEvent]);

  useEffect(() => {
    if (ready) frameRef.current?.contentWindow?.postMessage({ type: 'state', state, theme }, '*');
  }, [ready, state, theme]);

  return (
    <iframe
      ref={frameRef}
      data-testid="widget-frame"
      title={title}
      srcDoc={srcDoc}
      sandbox="allow-scripts"
      referrerPolicy="no-referrer"
      className={className}
    />
  );
}
