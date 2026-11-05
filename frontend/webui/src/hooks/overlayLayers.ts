/**
 * The stack of open overlays, shared by useModal and useMenu. Internal to those
 * two hooks.
 *
 * It exists so one Escape closes one thing: a menu open inside a dialog closes
 * the menu and leaves the dialog alone. A key handler inside an overlay can keep
 * Escape for itself by calling preventDefault.
 */

export interface Layer {
  modal: boolean;
  onEscape: () => void;
}

const layers: Layer[] = [];

function onKeyDown(e: KeyboardEvent) {
  if (e.key !== 'Escape' || e.defaultPrevented || e.isComposing) return;
  const top = layers[layers.length - 1];
  if (!top) return;
  e.preventDefault();
  top.onEscape();
}

/** Puts a layer on top of the stack and returns the function that removes it. */
export function pushLayer(layer: Layer): () => void {
  if (layers.length === 0) document.addEventListener('keydown', onKeyDown);
  layers.push(layer);
  return () => {
    layers.splice(layers.indexOf(layer), 1);
    if (layers.length === 0) document.removeEventListener('keydown', onKeyDown);
  };
}

/** Whether this is the modal the keyboard belongs to. Non-modal layers don't count. */
export function isTopModal(layer: Layer): boolean {
  return layers.filter(l => l.modal).pop() === layer;
}
