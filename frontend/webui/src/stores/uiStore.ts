/**
 * UI shell store (P2 #26 Migration commit 2).
 *
 * Owns transient UI flags that are independent of chat / user /
 * workspace state: which modal is open, whether the sidebar is
 * showing, whether a toast is up, etc.
 *
 * `sidebarOpen` is the only persisted slice — users expect their
 * sidebar preference to survive a reload. Modal-open flags
 * deliberately do NOT persist: opening Settings, refreshing the
 * page, and finding Settings still open would be jarring.
 *
 * Components that previously took `onClose` props purely to flip
 * a boolean in App.tsx now pull the close action directly from
 * this store. App.tsx becomes the place that *defines* the UI
 * state, not the place every panel has to route its `onClose`
 * call through.
 */

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';

import type { Announcement } from '../lib/api';


/** The reply a report is about, when it came from that reply's own button. */
export interface ReportReplyTarget {
  /** 1-based position among the conversation's replies. */
  number: number;
  /** The opening of the reply, to recognise it by. */
  excerpt: string;
}

interface UiState {
  // Modals / panels
  showSettings: boolean;
  showAdmin: boolean;
  showReportDialog: boolean;
  // Set while the report dialog is about one reply; null for the whole chat.
  reportReply: ReportReplyTarget | null;
  showInstallBanner: boolean;
  artifactPanelOpen: boolean;
  selectedArtifactId: string | null;
  // The artifact editor holds a change that has not been saved.
  artifactDraftChanged: boolean;
  knowledgePanelOpen: boolean;
  // True/string opens the knowledge page (string = initial route);
  // false hides it. Same triple-shape the original useState had.
  showKnowledgePage: string | true | false;

  // Sidebar
  sidebarOpen: boolean;
  sidebarRefreshKey: number;

  // Top-level announcement banner
  announcement: Announcement | null;
  announcementDismissed: boolean;

  // Toast (transient string shown at the top, or null)
  toast: string | null;

  // Actions — written as direct setters because the call sites
  // (App.tsx side-effects, panel close buttons) are simple boolean
  // flips that don't benefit from a custom-named action.
  setShowSettings: (open: boolean) => void;
  setShowAdmin: (open: boolean) => void;
  setShowReportDialog: (open: boolean) => void;
  openReportForReply: (target: ReportReplyTarget) => void;
  setShowInstallBanner: (open: boolean) => void;
  // The two side panels share one place on screen, so opening either closes the
  // other. That keeps a header pill's state in step with what is showing.
  setArtifactPanelOpen: (open: boolean) => void;
  setSelectedArtifactId: (id: string | null) => void;
  setArtifactDraftChanged: (changed: boolean) => void;
  setKnowledgePanelOpen: (open: boolean) => void;
  setShowKnowledgePage: (value: string | true | false) => void;
  setSidebarOpen: (open: boolean) => void;
  bumpSidebarRefresh: () => void;
  setAnnouncement: (a: Announcement | null) => void;
  setAnnouncementDismissed: (dismissed: boolean) => void;
  setToast: (msg: string | null) => void;
  // A toast that takes itself away.
  showToast: (msg: string, durationMs?: number) => void;
}


// Defaulted in two places: the initial mount picks based on
// viewport width if persist returns no stored value; otherwise
// the stored value wins. The persist hydrate happens before the
// first render so there's no flash of the wrong state.
const _defaultSidebarOpen = (): boolean => {
  // SSR / jsdom fallback — window may not exist or innerWidth
  // may be zero. Default to open on "desktop-ish" widths.
  try {
    // Open inline from lg; below it the sidebar is a drawer, closed at start.
    return typeof window !== 'undefined' && window.innerWidth >= 1024;
  } catch {
    return true;
  }
};


let toastTimer: ReturnType<typeof setTimeout> | undefined;


export const useUiStore = create<UiState>()(
  persist(
    immer((set) => ({
      showSettings: false,
      showAdmin: false,
      showReportDialog: false,
      reportReply: null,
      showInstallBanner: false,
      artifactPanelOpen: false,
      selectedArtifactId: null,
      artifactDraftChanged: false,
      knowledgePanelOpen: false,
      showKnowledgePage: false,

      sidebarOpen: _defaultSidebarOpen(),
      sidebarRefreshKey: 0,

      announcement: null,
      announcementDismissed: false,

      toast: null,

      setShowSettings: (open) => set(state => { state.showSettings = open; }),
      setShowAdmin: (open) => set(state => { state.showAdmin = open; }),
      // Opening from the header, or closing, is about the whole chat again.
      setShowReportDialog: (open) => set(state => { state.showReportDialog = open; state.reportReply = null; }),
      openReportForReply: (target) => set(state => { state.showReportDialog = true; state.reportReply = target; }),
      setShowInstallBanner: (open) => set(state => { state.showInstallBanner = open; }),
      setArtifactPanelOpen: (open) => set(state => {
        state.artifactPanelOpen = open;
        if (open) state.knowledgePanelOpen = false;
      }),
      setSelectedArtifactId: (id) => set(state => { state.selectedArtifactId = id; }),
      setArtifactDraftChanged: (changed) => set(state => { state.artifactDraftChanged = changed; }),
      setKnowledgePanelOpen: (open) => set(state => {
        state.knowledgePanelOpen = open;
        if (open) state.artifactPanelOpen = false;
      }),
      setShowKnowledgePage: (value) => set(state => { state.showKnowledgePage = value; }),
      setSidebarOpen: (open) => set(state => { state.sidebarOpen = open; }),
      bumpSidebarRefresh: () => set(state => { state.sidebarRefreshKey += 1; }),
      setAnnouncement: (a) => set(state => { state.announcement = a; }),
      setAnnouncementDismissed: (dismissed) => set(state => { state.announcementDismissed = dismissed; }),
      setToast: (msg) => {
        clearTimeout(toastTimer);
        set(state => { state.toast = msg; });
      },
      showToast: (msg, durationMs = 6000) => {
        clearTimeout(toastTimer);
        set(state => { state.toast = msg; });
        toastTimer = setTimeout(() => set(state => { state.toast = null; }), durationMs);
      },
    })),
    {
      // localStorage key — namespaced so it doesn't collide with
      // the existing munin_reported_chats key or the SSE-resume
      // sessionStorage entry from P1 #10.
      name: 'munin-ui',
      storage: createJSONStorage(() => localStorage),
      // Persist only sidebarOpen. The rest are transient: a stale
      // toast or open modal restored after reload would be jarring.
      partialize: (state) => ({ sidebarOpen: state.sidebarOpen }),
    },
  ),
);


// Test helper — see chatStore.ts for the same pattern + rationale.
export function _resetUiStoreForTests(): void {
  useUiStore.setState({
    showSettings: false,
    showAdmin: false,
    showReportDialog: false,
    reportReply: null,
    showInstallBanner: false,
    artifactPanelOpen: false,
    selectedArtifactId: null,
    artifactDraftChanged: false,
    knowledgePanelOpen: false,
    showKnowledgePage: false,
    sidebarOpen: _defaultSidebarOpen(),
    sidebarRefreshKey: 0,
    announcement: null,
    announcementDismissed: false,
    toast: null,
  });
}
