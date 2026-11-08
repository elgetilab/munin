import { useUiStore } from '../stores/uiStore';

// The artifact panel's editor holds a draft only in React state, so anything that
// takes the panel or its artifact away throws the draft out. The panel asks with
// its own dialog for its own buttons; everything else asks through these.

/** True when there is no changed draft, or the user agreed to throw it away. */
export function mayDropArtifactDraft(): boolean {
  return !useUiStore.getState().artifactDraftChanged || window.confirm('Discard your changes to this artifact?');
}

/** For a link that opens an artifact in the panel: another one replaces the draft. */
export function mayOpenArtifact(id: string): boolean {
  return id === useUiStore.getState().selectedArtifactId || mayDropArtifactDraft();
}
