/**
 * Workspace store (P2 #26 Migration commit 3).
 *
 * Owns "what context is the user working in" — active project,
 * project edit modal, tag catalog + currently active tag filters,
 * the ephemeral-chat toggle, and the set of conversation ids the
 * user has already reported (kept locally so the Report button
 * can show its "reported" state without a DB round-trip).
 *
 * Persisted via localStorage:
 *   - reportedChats: was previously read/written by hand against
 *     `munin_reported_chats`. The persist middleware now owns the
 *     round-trip. Migrated from the old key shape (a JSON array
 *     of conversation ids) by the storage migration below.
 *   - isEphemeral: a per-session preference; survives reloads so
 *     a user who switched to incognito mode stays in incognito
 *     after F5. This is new behaviour vs. the old useState which
 *     always defaulted to false on reload — small QoL win.
 *
 * NOT persisted: activeProjectId / editingProject / tagCatalog /
 * activeTags. These come from URL routing, API loads, or are
 * transient UI state that should re-derive on each mount.
 */

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';

import type { Project, TagCatalog, TagChip } from '../lib/types';


interface WorkspaceState {
  activeProjectId: string | null;
  editingProject: Project | null;
  tagCatalog: TagCatalog | null;
  activeTags: TagChip[];
  reportedChats: Set<string>;
  isEphemeral: boolean;

  setActiveProjectId: (id: string | null) => void;
  setEditingProject: (project: Project | null) => void;
  setTagCatalog: (catalog: TagCatalog | null) => void;
  setActiveTags: (tags: TagChip[]) => void;
  addReportedChat: (conversationId: string) => void;
  setIsEphemeral: (isEphemeral: boolean) => void;
  toggleEphemeral: () => boolean;  // returns the NEW value
}


export const useWorkspaceStore = create<WorkspaceState>()(
  persist(
    immer((set, get) => ({
      activeProjectId: null,
      editingProject: null,
      tagCatalog: null,
      activeTags: [],
      reportedChats: new Set<string>(),
      isEphemeral: false,

      setActiveProjectId: (id) => set(state => { state.activeProjectId = id; }),
      setEditingProject: (project) => set(state => { state.editingProject = project; }),
      setTagCatalog: (catalog) => set(state => { state.tagCatalog = catalog; }),
      setActiveTags: (tags) => set(state => { state.activeTags = tags; }),
      addReportedChat: (cid) => set(state => {
        state.reportedChats.add(cid);
      }),
      setIsEphemeral: (isEphemeral) => set(state => { state.isEphemeral = isEphemeral; }),
      // Toggle helper — replaces the previous
      // `setIsEphemeral(prev => !prev)` functional setter pattern.
      // Returns the new value so the caller (App.tsx
      // handleToggleEphemeral) can branch on "entering ephemeral
      // mode" to also clear the conversation.
      toggleEphemeral: () => {
        const next = !get().isEphemeral;
        set(state => { state.isEphemeral = next; });
        return next;
      },
    })),
    {
      name: 'munin-workspace',
      storage: createJSONStorage(() => localStorage, {
        // Sets aren't JSON-serialisable; (de)serialise reportedChats
        // as a plain array on the wire. Other fields pass through
        // the default JSON pipeline.
        reviver: (key, value) => {
          if (key === 'reportedChats' && Array.isArray(value)) {
            return new Set(value as string[]);
          }
          return value;
        },
        replacer: (key, value) => {
          if (key === 'reportedChats' && value instanceof Set) {
            return Array.from(value);
          }
          return value;
        },
      }),
      // Only persist the two QoL slices. Everything else re-derives
      // on mount (active project from URL routing, tag catalog from
      // /api/tags load, etc.).
      partialize: (state) => ({
        reportedChats: state.reportedChats,
        isEphemeral: state.isEphemeral,
      }),
      // Migration from the legacy `munin_reported_chats` localStorage
      // key. If we find an entry there and nothing yet in our own
      // namespace, hydrate from it once and let the next persist cycle
      // settle the new storage. Idempotent — re-running is a no-op
      // because we read our own key first.
      onRehydrateStorage: () => (state) => {
        if (!state) return;
        // Already loaded reportedChats from our own key — keep it.
        if (state.reportedChats.size > 0) return;
        try {
          const legacy = localStorage.getItem('munin_reported_chats');
          if (!legacy) return;
          const parsed = JSON.parse(legacy);
          if (!Array.isArray(parsed)) return;
          // Direct setState (not via action) — we're in the rehydrate
          // callback before any subscriber renders.
          useWorkspaceStore.setState({
            reportedChats: new Set(parsed.filter(x => typeof x === 'string')),
          });
        } catch {
          /* legacy key absent or malformed — fresh slate */
        }
      },
    },
  ),
);


export function _resetWorkspaceStoreForTests(): void {
  useWorkspaceStore.setState({
    activeProjectId: null,
    editingProject: null,
    tagCatalog: null,
    activeTags: [],
    reportedChats: new Set(),
    isEphemeral: false,
  });
}
