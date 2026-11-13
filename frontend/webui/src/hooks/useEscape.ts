/**
 * Escape for part of the page that is neither a menu nor a dialog, such as a
 * side column: while enabled, Escape calls onEscape.
 *
 *   useEscape(() => setPanelOpen(false), panelOpen);
 *
 * It sits under every menu and dialog, so Escape closes those first, and a key
 * handler that keeps Escape for itself (preventDefault) leaves it alone.
 */

import { useEffect, useRef } from 'react';

import { pushLayer } from './overlayLayers';

export function useEscape(onEscape: () => void, enabled: boolean) {
  // Callers pass inline closures; keeping the latest here stops a re-render from
  // moving the layer.
  const onEscapeRef = useRef(onEscape);
  useEffect(() => { onEscapeRef.current = onEscape; });

  useEffect(() => {
    if (!enabled) return;
    return pushLayer({ modal: false, base: true, onEscape: () => onEscapeRef.current() });
  }, [enabled]);
}
