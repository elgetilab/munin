import { useEffect, useCallback, useRef, useState, useSyncExternalStore } from 'react';
import { useChatLifecycle } from './hooks/useChatLifecycle';
import { friendlyError } from './lib/modelError';
import { useChatStore } from './stores/chatStore';
import { useUiStore } from './stores/uiStore';
import { useUserStore } from './stores/userStore';
import { useWorkspaceStore } from './stores/workspaceStore';
import { useDeepResearchStore, isActive as isDrActive } from './stores/deepResearchStore';
import { DemoDeck, type DemoCard } from './components/DemoDeck';
import { ResearchTimeline } from './components/ResearchTimeline';
import { useStatus } from './hooks/useStatus';
import { fetchPersonas, fetchMe, fetchAnnouncement, fetchUsageStats, fetchArtifacts, fetchProject, fetchTags, getResearchStatus, fetchResearchForConversation, startDeepResearch } from './lib/api';
import type { UserProfile } from './lib/api';
import { getGreeting } from './lib/greetings';
import { Sidebar } from './components/Sidebar';
import { MessageList } from './components/MessageList';
import { ChatInput, type ChatInputHandle } from './components/ChatInput';
import { FeatherVortex } from './components/FeatherVortex';
import { SleepingPage } from './components/SleepingPage';
import { MaintenancePage } from './components/MaintenancePage';
import { Settings } from './components/Settings';
import { ArtifactPanel } from './components/ArtifactPanel';
import { mayDropArtifactDraft } from './lib/artifactDraft';
import { isWidget } from './lib/artifactDisplay';
import { ProjectSettings } from './components/ProjectSettings';
import { AdminPanel } from './components/AdminPanel';
import { ReportDialog } from './components/ReportDialog';
import { PhoneHeader } from './components/PhoneHeader';
import { CHAT_COLUMN } from './components/ui/chatColumn';
import { canResend } from './lib/resend';
import { KnowledgePanel } from './components/KnowledgePanel';
import { KnowledgePage } from './components/KnowledgePage';
import { ErrorNotice } from './components/ui/ErrorNotice';
import { LOGO_FILTER, SCRIM } from './components/ui/themeClasses';
import { BookIcon, FlagIcon, GhostIcon, LayersIcon, MenuIcon } from './components/ui/icons';
import { ThemeToggle } from './components/ThemeToggle';
import { SessionNotice } from './components/SessionNotice';
import { DegradedBanner } from './components/DegradedBanner';
import { useEscape } from './hooks/useEscape';
import { useBelowXl, useModal } from './hooks/useModal';
import type { Project } from './lib/types';
import { AUTH_BASE, DOCS_URL, SEARCH_URL, SITE_HOME, UPLOAD_URL } from './lib/urls';
import { conversationPath, correctChatPath, dropStaleOverlayEntry, finishAppPath, isModifiedClick, popClosedOverlay, pushAppPath, readOverlayEntry, syncChatPath, syncOverlayHistory } from './lib/nav';

// Tailwind's lg breakpoint, below which the sidebar is a drawer over the page.
// A tablet gets the drawer too, so the header keeps its full width and its
// site links instead of losing them beside an inline sidebar (2026-11).
const BELOW_LG = '(width < 64rem)';

function subscribeBelowLg(onChange: () => void) {
  if (typeof window.matchMedia !== 'function') return () => {};
  const query = window.matchMedia(BELOW_LG);
  query.addEventListener('change', onChange);
  return () => query.removeEventListener('change', onChange);
}

function isBelowLg() {
  return typeof window.matchMedia === 'function' && window.matchMedia(BELOW_LG).matches;
}

// Set once per browser tab session; see the isEphemeral reset below.
const SESSION_STARTED_KEY = 'munin_session_started';

// One look for everything in the header, the chat toggles on the left and the
// site links on the right: a borderless 32px item that tints on hover. "On" (a
// pressed toggle, the current page) is the accent tint; a temporary or reported
// chat is the warning tint.
const NAV_ITEM = 'inline-flex flex-shrink-0 items-center gap-1.5 h-8 px-2.5 rounded-md text-[13px] font-medium leading-none whitespace-nowrap no-underline transition-colors cursor-pointer';
const NAV_IDLE = 'text-text-secondary hover:text-text-primary hover:bg-bg-tertiary';
const NAV_ON = 'bg-accent/15 text-accent';
const NAV_WARN = 'bg-warning/15 text-warning';
// The chat toggles drop to their icon when the header (not the window: the
// sidebar takes its share) is narrower than 64rem. The aria-label keeps the name.
const TOGGLE_LABEL = 'hidden @5xl:inline';
const COUNT_BADGE = 'min-w-4 px-1 rounded bg-bg-primary text-[11px] leading-4 text-center tabular-nums';
// The ✕ stays at full strength: faded, it falls under 4.5:1 on the banner's tint.
const DISMISS_BUTTON = 'flex-shrink-0 min-w-8 min-h-8 -my-1 flex items-center justify-center rounded hover:bg-current/10 transition-colors cursor-pointer text-xs';

export default function App() {
  const status = useStatus();
  // P2 #26 commit 3: user identity slice lives in userStore. The
  // persona-from-URL initialiser moved into the store's _initialPersona
  // helper; `hadPersonaParam` stays here as a ref because it's a
  // mount-time signal, not reactive state.
  const personas = useUserStore(s => s.personas);
  const setPersonas = useUserStore(s => s.setPersonas);
  const personaFromUrl = new URLSearchParams(window.location.search).get('persona');
  const selectedPersona = useUserStore(s => s.selectedPersona);
  const conversationTitle = useChatStore(s => s.conversationTitle);
  const setSelectedPersona = useUserStore(s => s.setSelectedPersona);
  const hadPersonaParam = useRef(!!personaFromUrl);
  const greeting = useUserStore(s => s.greeting);
  const setGreeting = useUserStore(s => s.setGreeting);
  const userProfile = useUserStore(s => s.userProfile);
  const setUserProfile = useUserStore(s => s.setUserProfile);
  // P2 #26 commit 2: UI shell state lives in uiStore. Names match
  // the previous useState destructures so the 55 use-sites in this
  // file don't churn — only the declarations change. Functional
  // setter pattern (setSidebarRefreshKey(k => k + 1)) is replaced
  // by the explicit bumpSidebarRefresh action.
  const showSettings = useUiStore(s => s.showSettings);
  const setShowSettings = useUiStore(s => s.setShowSettings);
  const sidebarOpen = useUiStore(s => s.sidebarOpen);
  const setSidebarOpen = useUiStore(s => s.setSidebarOpen);
  const sidebarRefreshKey = useUiStore(s => s.sidebarRefreshKey);
  const bumpSidebarRefresh = useUiStore(s => s.bumpSidebarRefresh);
  const announcement = useUiStore(s => s.announcement);
  const setAnnouncement = useUiStore(s => s.setAnnouncement);
  const announcementDismissed = useUiStore(s => s.announcementDismissed);
  const setAnnouncementDismissed = useUiStore(s => s.setAnnouncementDismissed);
  const isAdmin = useUserStore(s => s.isAdmin);
  const setIsAdmin = useUserStore(s => s.setIsAdmin);
  const showInstallBanner = useUiStore(s => s.showInstallBanner);
  const setShowInstallBanner = useUiStore(s => s.setShowInstallBanner);
  // P2 #26 commit 3: isEphemeral lives in workspaceStore — persisted
  // across reloads so a user who switched to incognito stays in
  // incognito after F5. A new session starts with it off, below, so
  // nobody comes back days later in a mode that saves nothing.
  const isEphemeral = useWorkspaceStore(s => s.isEphemeral);
  const setIsEphemeral = useWorkspaceStore(s => s.setIsEphemeral);
  const toggleEphemeral = useWorkspaceStore(s => s.toggleEphemeral);
  const deferredPromptRef = useRef<BeforeInstallPromptEvent | null>(null);
  // Mirrors "we hold a live beforeinstallprompt event" into state. The banner
  // used to read deferredPromptRef.current during render, which does not
  // re-render when the ref changes: after a dismissed install prompt cleared
  // the ref, the banner kept offering an Install button that did nothing.
  const [canInstall, setCanInstall] = useState(false);
  // P2 #26 commit 4: chat slices now consumed via per-field
  // selectors directly from useChatStore — the transitional
  // useChat() wrapper has been retired. Selector form means a
  // token-stream update (state.streaming.content += chunk) only
  // re-renders the components that subscribe to streaming, not
  // every component that destructured the whole tuple. The
  // mount-time SSE resume effect lives in useChatLifecycle()
  // because Zustand stores can't host React effects.
  useChatLifecycle();
  const messages = useChatStore(s => s.messages);
  const conversationId = useChatStore(s => s.conversationId);
  const conversationPersona = useChatStore(s => s.conversationPersona);
  const streaming = useChatStore(s => s.streaming);
  const error = useChatStore(s => s.error);
  const loadFailed = useChatStore(s => s.loadFailed);
  const artifacts = useChatStore(s => s.artifacts);
  const setArtifacts = useChatStore(s => s.setArtifacts);
  const lastArtifactEvent = useChatStore(s => s.lastArtifactEvent);
  const sendMessage = useChatStore(s => s.sendMessage);
  const loadConversation = useChatStore(s => s.loadConversation);
  const clearConversation = useChatStore(s => s.clearConversation);
  const stopGenerating = useChatStore(s => s.stopGenerating);
  const dismissMemoryProposal = useChatStore(s => s.dismissMemoryProposal);
  const artifactPanelOpen = useUiStore(s => s.artifactPanelOpen);
  const setArtifactPanelOpen = useUiStore(s => s.setArtifactPanelOpen);
  // Only the setter — ArtifactPanel itself subscribes to the
  // selected-id slice directly, so App.tsx doesn't need the read.
  const setSelectedArtifactId = useUiStore(s => s.setSelectedArtifactId);
  const activeProjectId = useWorkspaceStore(s => s.activeProjectId);
  const setActiveProjectId = useWorkspaceStore(s => s.setActiveProjectId);
  const editingProject = useWorkspaceStore(s => s.editingProject);
  const setEditingProject = useWorkspaceStore(s => s.setEditingProject);
  const showAdmin = useUiStore(s => s.showAdmin);
  const setShowAdmin = useUiStore(s => s.setShowAdmin);
  const showReportDialog = useUiStore(s => s.showReportDialog);
  const setShowReportDialog = useUiStore(s => s.setShowReportDialog);
  const toast = useUiStore(s => s.toast);
  const setToast = useUiStore(s => s.setToast);
  const showToast = useUiStore(s => s.showToast);
  const tagCatalog = useWorkspaceStore(s => s.tagCatalog);
  const setTagCatalog = useWorkspaceStore(s => s.setTagCatalog);
  const activeTags = useWorkspaceStore(s => s.activeTags);
  const setActiveTags = useWorkspaceStore(s => s.setActiveTags);
  const knowledgePanelOpen = useUiStore(s => s.knowledgePanelOpen);
  const setKnowledgePanelOpen = useUiStore(s => s.setKnowledgePanelOpen);
  const showKnowledgePage = useUiStore(s => s.showKnowledgePage);
  const setShowKnowledgePage = useUiStore(s => s.setShowKnowledgePage);
  // P2 #26 commit 3: reportedChats now persists via workspaceStore's
  // persist middleware. The old `munin_reported_chats` localStorage
  // entry is migrated in via onRehydrateStorage on first load.
  const reportedChats = useWorkspaceStore(s => s.reportedChats);
  const addReportedChat = useWorkspaceStore(s => s.addReportedChat);

  useEffect(() => {
    try {
      if (sessionStorage.getItem(SESSION_STARTED_KEY)) return;
      sessionStorage.setItem(SESSION_STARTED_KEY, '1');
      setIsEphemeral(false);
    } catch { /* no session storage: the persisted value stands */ }
  }, [setIsEphemeral]);

  // Load user info on mount
  useEffect(() => {
    fetchMe()
      .then(res => {
        setUserProfile(res);
        setGreeting(getGreeting(res.name));
      })
      .catch((e: unknown) => {
        // Signed out, but this page came from the browser's cache or an open tab,
        // so Caddy never got to redirect. Nothing here can work without a session,
        // and the login page does not bounce back, so this cannot loop.
        if ((e as { status?: number }).status === 401) {
          window.location.assign(`${AUTH_BASE}/login?redirect=${encodeURIComponent(window.location.href)}`);
          return;
        }
        setGreeting(getGreeting(null));
      });
  }, [setGreeting, setUserProfile]);

  // Load announcement and admin status on mount
  useEffect(() => {
    fetchAnnouncement()
      .then(a => setAnnouncement(a))
      .catch(() => {});
    fetchUsageStats()
      .then(stats => { if (stats.is_admin) setIsAdmin(true); })
      .catch(() => {});
  }, [setAnnouncement, setIsAdmin]);

  // Load tag catalog on mount
  useEffect(() => {
    fetchTags()
      .then(setTagCatalog)
      .catch(() => {});
  }, [setTagCatalog]);

  // Load personas on mount
  useEffect(() => {
    fetchPersonas()
      .then(res => {
        setPersonas(res.personas);
        if (!hadPersonaParam.current) {
          setSelectedPersona(res.default_persona);
        }
      })
      .catch(() => {
        // Fallback to the single Munin identity if the API is down. The model
        // auto-routes server-side; "munin" maps to no-pin (router decides).
        setPersonas([
          {
            id: 'munin',
            name: 'Munin',
            description: 'Munin routes each question automatically.',
            icon_url: '/shared/munin_logo_without_script.svg',
            tags: [],
            capabilities: {},
            prompt_suggestions: [],
          },
        ]);
        setSelectedPersona('munin');
      });
  }, [setPersonas, setSelectedPersona]);

  // URL routing: read conversation ID, persona, and knowledge route from URL on mount
  // Presenter deck at the unlinked /demo URL. demoMode stays on after a card
  // hands over to a chat, so the Slides button can bring the deck back.
  const [showDemo, setShowDemo] = useState(() => window.location.pathname === '/demo');
  const [demoMode] = useState(showDemo);
  const [demoSlide, setDemoSlide] = useState(0);
  // True while the /c/<id> this page was opened at is still loading. Until it
  // settles, conversationId says nothing about where the address bar should be.
  const initialChatLoading = useRef(/^\/c\/.+/.test(window.location.pathname));

  useEffect(() => {
    const path = window.location.pathname;
    const knowledgeMatch = path.match(/^\/knowledge(?:\/(.+))?$/);
    if (knowledgeMatch) {
      setShowKnowledgePage(knowledgeMatch[1] || true);
      return;
    }
    const match = path.match(/^\/c\/(.+)$/);
    if (match) {
      void loadConversation(match[1]).finally(() => { initialChatLoading.current = false; });
    }
    const params = new URLSearchParams(window.location.search);
    const persona = params.get('persona');
    if (persona) {
      setSelectedPersona(persona);
    }
    // `?project=<id>` is what the sidebar's "New chat in project" link
    // carries, so opening it in a new tab lands in the same project
    // context an in-tab click would have set. Mirrors
    // handleNewChatInProject: project active, incognito off.
    const project = params.get('project');
    if (project) {
      setActiveProjectId(project);
      setIsEphemeral(false);
    }
    if (persona || project) {
      // Clean the URL
      window.history.replaceState(null, '', window.location.pathname);
    }
  }, [loadConversation, setActiveProjectId, setIsEphemeral, setSelectedPersona, setShowKnowledgePage]);

  // Update URL when conversation changes
  useEffect(() => {
    if (showDemo) return;
    const currentPath = window.location.pathname;
    // Not a chat URL, and whoever leaves it pushes the right path themselves.
    if (/^\/knowledge(\/|$)/.test(currentPath) || initialChatLoading.current) return;
    const expectedPath = conversationId ? conversationPath(conversationId) : '/';
    if (currentPath !== expectedPath) {
      syncChatPath(expectedPath);
    }
  }, [conversationId, showDemo]);

  // Overlays and the phone drawer in the browser history (see lib/nav.ts).
  const belowLg = useSyncExternalStore(subscribeBelowLg, isBelowLg);
  const drawerOpen = sidebarOpen && belowLg && !showKnowledgePage;
  const overlayView = showAdmin ? 'admin' : editingProject ? 'project' : showSettings ? 'settings' : null;
  const overlayProject = overlayView === 'project' ? editingProject : null;
  const overlayHistoryStarted = useRef(false);
  useEffect(() => {
    if (!overlayHistoryStarted.current) {
      overlayHistoryStarted.current = true;
      dropStaleOverlayEntry();
    }
    syncOverlayHistory({ view: overlayView, project: overlayProject, drawer: drawerOpen });
  }, [overlayView, overlayProject, drawerOpen]);

  const drawerModal = useModal({ onClose: () => setSidebarOpen(false), active: drawerOpen });

  // Hide the support button while a side panel is showing (see index.css).
  const sidePanelShowing = !showKnowledgePage && (
    (knowledgePanelOpen && !!tagCatalog) || (artifactPanelOpen && !isEphemeral)
  );
  useEffect(() => {
    document.body.classList.toggle('munin-side-panel-open', sidePanelShowing);
    return () => document.body.classList.remove('munin-side-panel-open');
  }, [sidePanelShowing]);

  // Escape closes the side column from xl up, as its header toggle would, and
  // hands focus back to that toggle. Below xl the panel is a dialog and handles
  // Escape itself. The report dialog listens for Escape outside the layer stack,
  // so the panel waits while it is open, and the demo slides cover the panel.
  const artifactsToggleRef = useRef<HTMLButtonElement>(null);
  const sourcesToggleRef = useRef<HTMLButtonElement>(null);
  const belowXl = useBelowXl();
  useEscape(() => {
    if (useUiStore.getState().knowledgePanelOpen) {
      setKnowledgePanelOpen(false);
      sourcesToggleRef.current?.focus();
    } else if (mayDropArtifactDraft()) {
      setArtifactPanelOpen(false);
      artifactsToggleRef.current?.focus();
    }
  }, sidePanelShowing && !belowXl && !showReportDialog && !showDemo);

  // The button would sit over the rows of a Knowledge browse page (see index.css).
  const browsingKnowledge = typeof showKnowledgePage === 'string';
  useEffect(() => {
    document.body.classList.toggle('munin-browsing-knowledge', browsingKnowledge);
    return () => document.body.classList.remove('munin-browsing-knowledge');
  }, [browsingKnowledge]);

  // The active project's name, for the banner that says where the chat will go.
  const [activeProjectInfo, setActiveProjectInfo] = useState<{ id: string; name: string } | null>(null);
  useEffect(() => {
    if (!activeProjectId) return;
    let cancelled = false;
    fetchProject(activeProjectId)
      .then(p => { if (!cancelled) setActiveProjectInfo({ id: p.id, name: p.name }); })
      .catch(() => { /* the banner falls back to "this project" */ });
    return () => { cancelled = true; };
  }, [activeProjectId]);
  const activeProjectName = activeProjectInfo?.id === activeProjectId ? activeProjectInfo?.name : null;

  // Load artifacts when conversation changes. Clear the selected artifact FIRST:
  // otherwise the panel keeps the previous conversation's selectedArtifactId and
  // refetches it against the new conversation, which 404s ("artifact not found").
  useEffect(() => {
    setSelectedArtifactId(null);
    if (conversationId && !isEphemeral) {
      fetchArtifacts(conversationId)
        .then(res => setArtifacts(res.artifacts))
        .catch(() => {}); // may not have artifacts
    } else {
      // A new chat starts with none, not the list of the chat it came from.
      setArtifacts([]);
    }
  }, [conversationId, isEphemeral, setArtifacts, setSelectedArtifactId]);

  // Deep Research: the job runs detached; its progress is a durable event log
  // rendered inline (ResearchTimeline). On opening a conversation, load its DR
  // job (if any); while a job is active, poll its log; on completion, refetch
  // artifacts so the report appears in the panel.
  const drJob = useDeepResearchStore(s => s.job);
  const drSetJob = useDeepResearchStore(s => s.setJob);
  const drClear = useDeepResearchStore(s => s.clear);
  const drJobId = drJob?.id;
  const drJobConv = drJob?.conversationId;
  const drJobActive = isDrActive(drJob?.status) && drJobConv === conversationId;

  // Load the conversation's DR job on open (and clear a stale one on switch).
  useEffect(() => {
    let mounted = true;
    if (!conversationId || isEphemeral) { drClear(); return; }
    fetchResearchForConversation(conversationId)
      .then(({ job }) => { if (mounted && job) drSetJob(job); else if (mounted && drJob?.conversationId !== conversationId) drClear(); })
      .catch(() => {});
    return () => { mounted = false; };
    // drJob is read but deliberately not a dependency: it is what this
    // effect ultimately writes, so listing it would refetch on every job
    // update. The conversation id is the real trigger.
  }, [conversationId, isEphemeral, drSetJob, drClear]);  // eslint-disable-line react-hooks/exhaustive-deps

  // Poll the active job's event log.
  useEffect(() => {
    if (!drJobId || !drJobActive) return;
    let mounted = true;
    const tick = async () => {
      try {
        const s = await getResearchStatus(drJobId);
        if (!mounted) return;
        drSetJob(s);
        if (s.status === 'done' && drJobConv) {
          const res = await fetchArtifacts(drJobConv);
          if (mounted) setArtifacts(res.artifacts);
        }
      } catch { /* transient; keep polling */ }
    };
    const id = setInterval(tick, 2000);
    tick();
    return () => { mounted = false; clearInterval(id); };
  }, [drJobId, drJobConv, drJobActive, drSetJob, setArtifacts]);

  // Open the panel on a document the model is writing. Sandbox files and widgets
  // already show in the message, so they only become the selection if that does
  // not replace something the user is reading in the panel.
  useEffect(() => {
    if (!lastArtifactEvent) return;
    // The user is editing: the new artifact waits in the list, the draft stays.
    if (useUiStore.getState().artifactDraftChanged) return;
    const inMessage = 'content_type' in lastArtifactEvent && isWidget(lastArtifactEvent);
    if (lastArtifactEvent.source === 'model_written' && !inMessage) {
      setArtifactPanelOpen(true);
      setSelectedArtifactId(lastArtifactEvent.id);
    } else if (!useUiStore.getState().artifactPanelOpen) {
      setSelectedArtifactId(lastArtifactEvent.id);
    }
  }, [lastArtifactEvent, setArtifactPanelOpen, setSelectedArtifactId]);

  // Sync the persona indicator to the conversation's persona on
  // conversation load (the persisted persona is authoritative).
  useEffect(() => {
    if (conversationPersona && conversationPersona !== selectedPersona) {
      setSelectedPersona(conversationPersona);
    }
  }, [conversationPersona, selectedPersona, setSelectedPersona]);

  // Refresh sidebar when a stream completes (new messages) — skip for ephemeral
  // Also clear activeProjectId once the conversation is created (filed by backend)
  useEffect(() => {
    if (streaming.phase === 'idle' && messages.length > 0 && !isEphemeral) {
      bumpSidebarRefresh();
      if (activeProjectId && conversationId) {
        setActiveProjectId(null);
      }
    }
  }, [streaming.phase, messages.length, isEphemeral, activeProjectId, conversationId, bumpSidebarRefresh, setActiveProjectId]);

  const closeDrawerOnPhone = useCallback(() => {
    if (window.innerWidth < 1024) setSidebarOpen(false);
  }, [setSidebarOpen]);

  // The three handlers below return false when the user kept an artifact draft,
  // so that callers leave whatever else is open alone.
  const handleSelectChat = useCallback((id: string): boolean => {
    if (id !== useChatStore.getState().conversationId && !mayDropArtifactDraft()) return false;
    setIsEphemeral(false);
    setActiveProjectId(null);
    setEditingProject(null);
    setShowAdmin(false);
    loadConversation(id);
    return true;
  }, [loadConversation, setActiveProjectId, setEditingProject, setIsEphemeral, setShowAdmin]);

  const handleNewChat = useCallback((): boolean => {
    if (!mayDropArtifactDraft()) return false;
    setIsEphemeral(false);
    setActiveProjectId(null);
    setEditingProject(null);
    setShowAdmin(false);
    setShowSettings(false);
    clearConversation();
    pushAppPath('/');
    return true;
  }, [clearConversation, setActiveProjectId, setEditingProject, setIsEphemeral, setShowAdmin, setShowSettings]);

  const handleNewChatInProject = useCallback((projectId: string): boolean => {
    if (!mayDropArtifactDraft()) return false;
    setIsEphemeral(false);
    setActiveProjectId(projectId);
    setEditingProject(null);
    setShowAdmin(false);
    setShowSettings(false);
    clearConversation();
    pushAppPath('/');
    return true;
  }, [clearConversation, setActiveProjectId, setEditingProject, setIsEphemeral, setShowAdmin, setShowSettings]);

  const handleOpenProjectSettings = useCallback((project: Project) => {
    setEditingProject(project);
    setShowSettings(false);
    setShowAdmin(false);
  }, [setEditingProject, setShowAdmin, setShowSettings]);

  const handleProjectUpdated = useCallback((updated: Project) => {
    setEditingProject(updated);
    bumpSidebarRefresh();
  }, [bumpSidebarRefresh, setEditingProject]);

  const handleToggleEphemeral = useCallback(() => {
    if (!mayDropArtifactDraft()) return;
    const nowEphemeral = toggleEphemeral();
    if (nowEphemeral) {
      // Entering ephemeral: clear current conversation. pushAppPath replaces any
      // overlay's history entry, so the overlay has to close with it.
      setShowSettings(false);
      setEditingProject(null);
      setShowAdmin(false);
      clearConversation();
      pushAppPath('/');
    }
  }, [clearConversation, setEditingProject, setShowAdmin, setShowSettings, toggleEphemeral]);

  const handleOpenKnowledgePage = useCallback(() => {
    if (!mayDropArtifactDraft()) return;
    setShowKnowledgePage(true);
    setShowSettings(false);
    setShowAdmin(false);
    setEditingProject(null);
    pushAppPath('/knowledge');
  }, [setEditingProject, setShowAdmin, setShowKnowledgePage, setShowSettings]);

  // Back to the chat still loaded behind the Knowledge page, under its own URL.
  const leaveKnowledgePage = useCallback(() => {
    setShowKnowledgePage(false);
    pushAppPath(conversationId ? conversationPath(conversationId) : '/');
  }, [conversationId, setShowKnowledgePage]);

  const handleProfileUpdate = useCallback((updated: UserProfile) => {
    setUserProfile(updated);
    setGreeting(getGreeting(updated.name));
  }, [setGreeting, setUserProfile]);

  // Only send project_id when creating a new conversation (no existing conversationId)
  const projectIdForNewChat = !conversationId && activeProjectId ? activeProjectId : undefined;

  const handleSend = useCallback((content: string) => {
    sendMessage(content, selectedPersona, isEphemeral, undefined, projectIdForNewChat, activeTags.length > 0 ? activeTags : undefined);
  }, [sendMessage, selectedPersona, isEphemeral, projectIdForNewChat, activeTags]);

  // Retry and Edit: send `content` in place of the saved user message
  // `messageId`; it and everything after it go, here and on the server.
  const handleResend = useCallback((messageId: string, content: string) => {
    sendMessage(content, selectedPersona, false, undefined, undefined, activeTags.length > 0 ? activeTags : undefined, undefined, messageId);
  }, [sendMessage, selectedPersona, activeTags]);

  // P2 #24 Phase 2: after the user clicks Approve / Approve-all on
  // the PlanCard, the REST call has already landed (approved_at is
  // set in the DB). To get the model out of "waiting" and back into
  // a turn, send a synthetic short user message. The model sees
  // APPROVAL STATUS: APPROVED in its next system-prompt plan block
  // and retries the gated tool call naturally.
  const lastUserMessage = [...messages].reverse().find(m => m.role === 'user');
  const lastMessage = messages[messages.length - 1];
  const chatLoadFailed = !!error && loadFailed;
  const replyInterrupted = !!error && !loadFailed && lastMessage?.role === 'assistant' && !!lastMessage.interrupted;
  // Sent attachments are not kept, and a message of only attachments is just a
  // placeholder such as [Image], which is nothing to send again.
  const canRetryReply = replyInterrupted && !!lastUserMessage && !/^\[.*\]$/.test(lastUserMessage.content);

  const handleRetryReply = () => {
    if (!lastUserMessage) return;
    // Replace the saved attempt on the server too, so the model doesn't see
    // the question twice. Only a message that never came back saved (no id
    // from `done`) falls back to trimming here alone.
    if (!isEphemeral && canResend(lastUserMessage)) {
      handleResend(lastUserMessage.id, lastUserMessage.content);
      return;
    }
    // sendMessage adds the user's message again, so take this attempt away first.
    useChatStore.setState({ messages: messages.slice(0, messages.lastIndexOf(lastUserMessage)) });
    handleSend(lastUserMessage.content);
  };

  const handlePlanApproved = useCallback(() => {
    sendMessage(
      "I've approved the plan, please continue.",
      selectedPersona, isEphemeral, undefined,
      projectIdForNewChat, activeTags.length > 0 ? activeTags : undefined,
    );
  }, [sendMessage, selectedPersona, isEphemeral, projectIdForNewChat, activeTags]);

  const handlePlanEdited = useCallback(() => {
    // Edit-with-implicit-approve uses the same resume shape as
    // Approve. The model sees the edited items + APPROVED status.
    sendMessage(
      "I've edited and approved the plan, please continue.",
      selectedPersona, isEphemeral, undefined,
      projectIdForNewChat, activeTags.length > 0 ? activeTags : undefined,
    );
  }, [sendMessage, selectedPersona, isEphemeral, projectIdForNewChat, activeTags]);

  // Continue a turn that stopped at its tool-use budget (rpt_20260702).
  // Same synthetic-message resume shape as the plan handlers: the model
  // picks the task back up from the tool results already gathered.
  const handleContinue = useCallback(() => {
    sendMessage(
      "Please continue where you left off.",
      selectedPersona, isEphemeral, undefined,
      projectIdForNewChat, activeTags.length > 0 ? activeTags : undefined,
    );
  }, [sendMessage, selectedPersona, isEphemeral, projectIdForNewChat, activeTags]);

  const handleSendMultimodal = useCallback((content: Array<{ type: string; text?: string; image_url?: { url: string }; document_id?: string; filename?: string }>) => {
    const doc = content.find(c => c.type === 'document');
    const textPart = content.find(c => c.type === 'text')?.text
      || (doc ? `[${doc.filename || 'Document'}]` : '[Image]');
    sendMessage(textPart, selectedPersona, isEphemeral, content, projectIdForNewChat, activeTags.length > 0 ? activeTags : undefined);
  }, [sendMessage, selectedPersona, isEphemeral, projectIdForNewChat, activeTags]);

  const runDemoCard = useCallback(async (card: DemoCard, projectId: string | null) => {
    const phase = useChatStore.getState().streaming.phase;
    if (phase === 'thinking' || phase === 'tool_call' || phase === 'generating') {
      showToast('The current answer is still running. Stop it or let it finish first.', 4000);
      return;
    }
    // The deck covers the chat, so a draft can still be open behind it.
    if (!mayDropArtifactDraft()) return;
    const cardProject = card.useProject ? projectId : null;
    setShowDemo(false);
    setEditingProject(null);
    setShowAdmin(false);
    setIsEphemeral(false);
    setActiveProjectId(cardProject);
    clearConversation();
    pushAppPath('/');
    try {
      if (card.kind === 'research') {
        const r = await startDeepResearch(null, card.prompt, card.budgets);
        useDeepResearchStore.getState().startJob(r.job_id, card.prompt, r.conversation_id, Date.now());
        await loadConversation(r.conversation_id);
      } else {
        await sendMessage(card.prompt, 'munin', false, undefined, cardProject ?? undefined);
      }
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'The demo card failed to start.', 6000);
    }
  }, [clearConversation, loadConversation, sendMessage, setActiveProjectId, setEditingProject, setIsEphemeral, setShowAdmin, showToast]);

  const openDemoChat = useCallback((id: string) => {
    if (id !== useChatStore.getState().conversationId && !mayDropArtifactDraft()) return;
    setShowDemo(false);
    setEditingProject(null);
    setShowAdmin(false);
    setIsEphemeral(false);
    setActiveProjectId(null);
    loadConversation(id);
  }, [loadConversation, setActiveProjectId, setEditingProject, setIsEphemeral, setShowAdmin]);

  // Reload, closing the tab, or leaving the site: the browser asks in its own words.
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (!useUiStore.getState().artifactDraftChanged) return;
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, []);

  // For the Back/Forward handler, which is set up once.
  const showDemoRef = useRef(showDemo);
  useEffect(() => { showDemoRef.current = showDemo; }, [showDemo]);

  // Handle browser back/forward
  useEffect(() => {
    // Whether arriving at this path takes the chat, and with it an artifact draft,
    // off the screen. The deck only covers the chat; the Knowledge page replaces it.
    const leavesChat = (path: string) => {
      if (path === '/demo') return false;
      const match = path.match(/^\/c\/(.+)$/);
      return !match || match[1] !== useChatStore.getState().conversationId;
    };
    const handler = () => {
      // Whoever called pushAppPath has already decided what is open.
      if (finishAppPath()) return;
      const closedOverlay = popClosedOverlay();
      if (!closedOverlay && leavesChat(window.location.pathname) && !mayDropArtifactDraft()) {
        // History cannot refuse a step, so the page the user stays on comes back
        // as a new entry. A draft only exists in a saved chat, which has an id.
        const chatId = useChatStore.getState().conversationId;
        window.history.pushState(null, '', showDemoRef.current ? '/demo' : chatId ? conversationPath(chatId) : '/');
        return;
      }
      const overlay = readOverlayEntry();
      setShowSettings(overlay.view === 'settings');
      setShowAdmin(overlay.view === 'admin');
      setEditingProject(overlay.view === 'project' ? overlay.project as Project : null);
      if (isBelowLg()) setSidebarOpen(overlay.drawer);

      const path = window.location.pathname;
      if (path === '/demo') {
        setShowDemo(true);
        return;
      }
      setShowDemo(false);
      const knowledgeMatch = path.match(/^\/knowledge(?:\/(.+))?$/);
      if (knowledgeMatch) {
        setShowKnowledgePage(knowledgeMatch[1] || true);
        return;
      }
      setShowKnowledgePage(false);
      // Closing an overlay lands on the same page, whose chat must stay as it is:
      // a temporary chat has no id and could not be read back.
      const chat = useChatStore.getState();
      const match = path.match(/^\/c\/(.+)$/);
      if (closedOverlay) {
        if (chat.conversationId && path !== conversationPath(chat.conversationId)) {
          correctChatPath(conversationPath(chat.conversationId));
        }
      } else if (match) {
        if (match[1] !== chat.conversationId) loadConversation(match[1]);
      } else if (chat.conversationId || (chat.error && chat.messages.length === 0)) {
        clearConversation();
      }
    };
    window.addEventListener('popstate', handler);
    return () => window.removeEventListener('popstate', handler);
  }, [loadConversation, clearConversation, setEditingProject, setShowAdmin, setShowKnowledgePage, setShowSettings, setSidebarOpen]);

  // PWA install prompt
  useEffect(() => {
    if (localStorage.getItem('munin_pwa_dismissed')) return;
    // Already installed as PWA
    if (window.matchMedia('(display-mode: standalone)').matches) return;
    // Only show on mobile/tablet
    const isMobile = window.innerWidth < 768 || 'ontouchstart' in window;
    if (!isMobile) return;

    // Android Chrome: capture the native install prompt
    const handler = (e: Event) => {
      e.preventDefault();
      deferredPromptRef.current = e as BeforeInstallPromptEvent;
      setCanInstall(true);
      setShowInstallBanner(true);
    };
    window.addEventListener('beforeinstallprompt', handler);

    // For browsers that don't fire beforeinstallprompt (iOS, Firefox),
    // show the banner after a short delay so it doesn't block first load
    const timeout = setTimeout(() => {
      if (!deferredPromptRef.current) {
        setShowInstallBanner(true);
      }
    }, 3000);

    return () => {
      window.removeEventListener('beforeinstallprompt', handler);
      clearTimeout(timeout);
    };
  }, [setShowInstallBanner]);

  const handleInstall = async () => {
    if (deferredPromptRef.current) {
      deferredPromptRef.current.prompt();
      const result = await deferredPromptRef.current.userChoice;
      if (result.outcome === 'accepted') {
        setShowInstallBanner(false);
      }
      deferredPromptRef.current = null;
      setCanInstall(false);
    }
  };

  const dismissInstallBanner = () => {
    setShowInstallBanner(false);
    localStorage.setItem('munin_pwa_dismissed', '1');
  };

  const isIOSDevice = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);

  const currentPersona = personas.find(p => p.id === selectedPersona) || null;
  const isStreaming = streaming.phase !== 'idle' && streaming.phase !== 'done' && streaming.phase !== 'error';
  const isEmpty = messages.length === 0 && !isStreaming;
  // Maintenance takes precedence over the nightly sleeping page.
  const isMaintenance = status?.maintenance?.active === true;
  const isOffline = status?.vllm?.status === 'offline';

  // ── Whole-window drag-and-drop file attach ────────────────────────────────
  // Files dropped anywhere over the chat view are routed into the
  // composer's existing upload/attach pipeline via chatInputRef. Gated to
  // chat states (not admin/settings/maintenance/offline). A drag-depth
  // counter keeps the overlay steady as the cursor moves over children.
  const chatInputRef = useRef<ChatInputHandle>(null);
  const [dragActive, setDragActive] = useState(false);
  const dragDepth = useRef(0);
  const chatActive =
    !showAdmin && !editingProject && !showSettings && !isMaintenance && !isOffline;

  // Stop the browser from navigating to a file dropped outside the zone.
  useEffect(() => {
    const prevent = (e: DragEvent) => {
      if (Array.from(e.dataTransfer?.types ?? []).includes('Files')) e.preventDefault();
    };
    window.addEventListener('dragover', prevent);
    window.addEventListener('drop', prevent);
    return () => {
      window.removeEventListener('dragover', prevent);
      window.removeEventListener('drop', prevent);
    };
  }, []);

  const dragHasFiles = (e: React.DragEvent) =>
    Array.from(e.dataTransfer?.types ?? []).includes('Files');
  const onChatDragEnter = (e: React.DragEvent) => {
    if (!chatActive || !dragHasFiles(e)) return;
    e.preventDefault();
    dragDepth.current += 1;
    setDragActive(true);
  };
  const onChatDragOver = (e: React.DragEvent) => {
    if (!chatActive || !dragHasFiles(e)) return;
    e.preventDefault();  // required for the drop event to fire
  };
  const onChatDragLeave = () => {
    if (!chatActive) return;
    dragDepth.current -= 1;
    if (dragDepth.current <= 0) {
      dragDepth.current = 0;
      setDragActive(false);
    }
  };
  const onChatDrop = (e: React.DragEvent) => {
    if (!chatActive) return;
    e.preventDefault();
    dragDepth.current = 0;
    setDragActive(false);
    const files = e.dataTransfer?.files;
    if (files && files.length > 0) chatInputRef.current?.handleDroppedFiles(files);
  };

  // ── Knowledge full-page view ──────────────────────────────────────────────
  if (showKnowledgePage) {
    return (
      <div className="flex flex-col h-dvh">
        {/* Standalone header for knowledge page */}
        {/* On a phone only the links that fit: the logo goes Home, and the account
            menu back in the chat has the rest. */}
        <header className="flex items-center gap-3 px-3 py-2 border-b border-border bg-bg-secondary">
          <a href={SITE_HOME} className="flex-shrink-0 flex items-center gap-2 px-1 no-underline whitespace-nowrap">
            <img src="/shared/munin_logo_horizon_moon.svg" alt="Munin" className={`h-7 ${LOGO_FILTER}`} />
            <span className="text-text-primary font-semibold text-[15px]">Munin</span>
          </a>
          <div className="flex-1" />
          <nav className="flex-shrink-0 flex gap-0.5">
            <a href={SITE_HOME} className={`hidden md:inline-flex ${NAV_ITEM} ${NAV_IDLE}`}>Home</a>
            <a href={SEARCH_URL} className={`hidden md:inline-flex ${NAV_ITEM} ${NAV_IDLE}`}>Search papers</a>
            <a href={DOCS_URL} className={`hidden md:inline-flex ${NAV_ITEM} ${NAV_IDLE}`}>Docs</a>
            <span className={`${NAV_ITEM} ${NAV_ON} cursor-default`} aria-current="page">Knowledge</span>
            <a
              href="/"
              onClick={(e) => {
                if (isModifiedClick(e)) return;
                e.preventDefault();
                leaveKnowledgePage();
              }}
              className={`${NAV_ITEM} ${NAV_IDLE}`}
            >Chat</a>
            <a href={UPLOAD_URL} className={`${NAV_ITEM} ${NAV_IDLE}`}>Upload</a>
          </nav>
          <span aria-hidden="true" className="hidden md:block w-px h-4 bg-border" />
          <ThemeToggle className="-ml-1.5" />
        </header>
        <KnowledgePage
          initialRoute={typeof showKnowledgePage === 'string' ? showKnowledgePage : undefined}
          tagCatalog={tagCatalog}
          onClose={leaveKnowledgePage}
          onChatWithTag={(tags) => {
            setActiveTags(tags);
            setShowKnowledgePage(false);
            clearConversation();
            pushAppPath('/');
          }}
        />
        <SessionNotice />
      </div>
    );
  }

  return (
    <div className="flex h-dvh">
      {/* Skip link: off-screen until it takes keyboard focus. Not a fragment
          navigation, because that fires popstate and the handler below would
          read the unchanged path as a reason to clear the conversation. */}
      {chatActive && !showDemo && (
        <a
          href="#composer-textarea"
          onClick={(e) => { e.preventDefault(); chatInputRef.current?.focus(); }}
          className="fixed left-2 -top-full focus:top-2 z-[60] px-3 py-2 rounded-lg bg-bg-tertiary border border-border text-sm text-text-primary no-underline"
        >
          Skip to message box
        </a>
      )}
      {showDemo && (
        <DemoDeck slide={demoSlide} onSlide={setDemoSlide} offline={isOffline || isMaintenance} onRun={runDemoCard} onOpenChat={openDemoChat} />
      )}
      {demoMode && !showDemo && (
        <button
          data-testid="demo-slides"
          onClick={() => { setShowDemo(true); pushAppPath('/demo'); }}
          className="fixed top-14 md:top-auto md:bottom-20 right-4 z-50 px-3 py-1.5 text-sm rounded-lg border border-border bg-bg-secondary text-text-primary shadow-lg cursor-pointer hover:bg-bg-tertiary"
        >
          Slides
        </button>
      )}
      {/* Sidebar: a drawer over the page below lg (phones and tablets), inline from lg */}
      {sidebarOpen && (
        <>
          {/* Backdrop on mobile */}
          <div
            className={`fixed inset-0 ${SCRIM} z-40 lg:hidden`}
            aria-hidden="true"
            onClick={() => setSidebarOpen(false)}
          />
          <div {...drawerModal.panelProps} className="fixed inset-y-0 left-0 z-50 lg:relative lg:z-auto outline-none">
            {drawerOpen && <h2 id={drawerModal.titleId} className="sr-only">Menu</h2>}
            <Sidebar
              currentId={conversationId}
              onSelect={(id) => { if (!handleSelectChat(id)) return; setShowSettings(false); setEditingProject(null); closeDrawerOnPhone(); }}
              onNewChat={() => { if (handleNewChat()) closeDrawerOnPhone(); }}
              onNewChatInProject={(pid) => { if (handleNewChatInProject(pid)) closeDrawerOnPhone(); }}
              onOpenProjectSettings={(project) => { handleOpenProjectSettings(project); closeDrawerOnPhone(); }}
              refreshKey={sidebarRefreshKey}
              onOpenSettings={() => { setShowSettings(true); setEditingProject(null); setShowAdmin(false); closeDrawerOnPhone(); }}
              onOpenAdmin={() => { setShowAdmin(true); setShowSettings(false); setEditingProject(null); closeDrawerOnPhone(); }}
              onOpenKnowledge={() => { handleOpenKnowledgePage(); closeDrawerOnPhone(); }}
              onCloseDrawer={drawerOpen ? () => setSidebarOpen(false) : undefined}
            />
          </div>
        </>
      )}

      {/* Main area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header — minimal, just sidebar toggle and nav links */}
        {/* Never wider than the main area, and the page itself never scrolls
            sideways. Clipped rather than scrolled sideways, so menus can still
            drop down out of it: on a phone it holds only the menu, the title, New
            chat and ⋯; from md up the pill text wraps and the header grows. */}
        <header className="@container flex items-center gap-1 px-3 py-2 border-b border-border bg-bg-secondary overflow-x-clip">
          {/* 44px to the finger on a phone, the same 32px as its neighbours from md up. */}
          <button
            onClick={() => setSidebarOpen(!sidebarOpen)}
            className={`${NAV_ITEM} ${NAV_IDLE} justify-center !px-0 w-11 h-11 -my-1.5 md:w-8 md:h-8 md:my-0 mr-1`}
            title={sidebarOpen ? 'Hide sidebar' : 'Show sidebar'}
            aria-label={sidebarOpen ? 'Hide sidebar' : 'Show sidebar'}
          >
            <MenuIcon size={18} />
          </button>

          {/* Phone: the chat's title, New chat and a labelled ⋯ menu. */}
          <PhoneHeader
            title={conversationTitle ?? (isEphemeral ? 'Temporary chat' : messages.length > 0 ? 'Untitled chat' : 'New chat')}
            onNewChat={() => { handleNewChat(); }}
            isEphemeral={isEphemeral}
            onToggleEphemeral={handleToggleEphemeral}
            artifacts={!isEphemeral ? { open: artifactPanelOpen, count: artifacts.length, onToggle: () => { if (mayDropArtifactDraft()) setArtifactPanelOpen(!artifactPanelOpen); } } : undefined}
            sources={tagCatalog ? { open: knowledgePanelOpen, count: activeTags.length, onToggle: () => { if (mayDropArtifactDraft()) setKnowledgePanelOpen(!knowledgePanelOpen); } } : undefined}
            report={conversationId && !isEphemeral && messages.length > 0 ? { reported: reportedChats.has(conversationId), onReport: () => setShowReportDialog(true) } : undefined}
          />

          {/* From md up, the toggles sit in the header itself. */}
          <div className="hidden md:contents">
          {/* Ephemeral toggle */}
          <button
            data-testid="ephemeral-toggle"
            onClick={handleToggleEphemeral}
            aria-pressed={isEphemeral}
            aria-label="Temporary chat"
            className={`${NAV_ITEM} ${isEphemeral ? NAV_WARN : NAV_IDLE}`}
            title={isEphemeral ? "Temporary chat is on. This chat won't be saved." : "Start a temporary chat that won't be saved"}
          >
            <GhostIcon />
            <span className={TOGGLE_LABEL}>Temporary chat</span>
          </button>

          {/* Artifacts panel toggle: there from the first screen, beside Sources.
              A temporary chat keeps no artifacts, so it has none to show. */}
          {!isEphemeral && (
            <button
              ref={artifactsToggleRef}
              data-testid="artifacts-button"
              onClick={() => { if (mayDropArtifactDraft()) setArtifactPanelOpen(!artifactPanelOpen); }}
              aria-pressed={artifactPanelOpen}
              aria-label={artifacts.length > 0 ? `Artifacts (${artifacts.length})` : 'Artifacts'}
              className={`${NAV_ITEM} ${artifactPanelOpen ? NAV_ON : NAV_IDLE}`}
              title={artifactPanelOpen ? 'Hide artifacts' : 'Show artifacts'}
            >
              <LayersIcon />
              <span className={TOGGLE_LABEL}>Artifacts</span>
              {artifacts.length > 0 && <span className={COUNT_BADGE}>{artifacts.length}</span>}
            </button>
          )}

          {/* Knowledge panel toggle */}
          {tagCatalog && (
            <button
              ref={sourcesToggleRef}
              onClick={() => { if (mayDropArtifactDraft()) setKnowledgePanelOpen(!knowledgePanelOpen); }}
              aria-pressed={knowledgePanelOpen}
              aria-label={activeTags.length > 0 ? `Sources (${activeTags.length})` : 'Sources'}
              className={`${NAV_ITEM} ${knowledgePanelOpen || activeTags.length > 0 ? NAV_ON : NAV_IDLE}`}
              title={knowledgePanelOpen ? 'Hide sources' : 'Choose which sources to search'}
            >
              <BookIcon />
              <span className={TOGGLE_LABEL}>Sources</span>
              {activeTags.length > 0 && <span className={COUNT_BADGE}>{activeTags.length}</span>}
            </button>
          )}

          {/* Report chat */}
          {conversationId && !isEphemeral && messages.length > 0 && (
            <button
              onClick={() => setShowReportDialog(true)}
              className={`${NAV_ITEM} ${reportedChats.has(conversationId) ? NAV_WARN : NAV_IDLE}`}
              title={reportedChats.has(conversationId) ? 'Chat reported' : 'Report this chat'}
              aria-label={reportedChats.has(conversationId) ? 'Chat reported' : 'Report this chat'}
            >
              <FlagIcon filled={reportedChats.has(conversationId)} />
              <span className={TOGGLE_LABEL}>{reportedChats.has(conversationId) ? 'Reported' : 'Report'}</span>
            </button>
          )}
          </div>

          <div className="hidden md:block flex-1 min-w-2" />

          <nav className={`hidden gap-0.5 ${sidebarOpen ? 'lg:flex' : 'md:flex'}`}>
            <a href={SITE_HOME} className={`${NAV_ITEM} ${NAV_IDLE}`}>Home</a>
            <a href={SEARCH_URL} className={`${NAV_ITEM} ${NAV_IDLE}`}>Search papers</a>
            <a href={DOCS_URL} className={`${NAV_ITEM} ${NAV_IDLE}`}>Docs</a>
            <a
              href="/knowledge"
              onClick={(e) => {
                if (isModifiedClick(e)) return;
                e.preventDefault();
                handleOpenKnowledgePage();
              }}
              className={`${NAV_ITEM} ${NAV_IDLE}`}
            >Knowledge</a>
            <a
              href={conversationId ? conversationPath(conversationId) : '/'}
              onClick={(e) => {
                if (isModifiedClick(e)) return;
                e.preventDefault();
                setShowAdmin(false);
                setShowSettings(false);
                setEditingProject(null);
              }}
              aria-current={overlayView ? undefined : 'page'}
              className={`${NAV_ITEM} ${overlayView ? NAV_IDLE : NAV_ON}`}
            >Chat</a>
            <a href={UPLOAD_URL} className={`${NAV_ITEM} ${NAV_IDLE}`}>Upload</a>
          </nav>
          {/* Shown at every width, with a hairline after the nav where there is one. */}
          <span aria-hidden="true" className={`hidden w-px h-4 mx-1 bg-border ${sidebarOpen ? 'lg:block' : 'md:block'}`} />
          {/* On a phone the theme is in the ⋯ menu. */}
          <span className="hidden md:contents"><ThemeToggle /></span>
        </header>

        {/* Degraded-service notice (LLM fallback or reduced mode) */}
        <DegradedBanner status={status} />

        {/* Announcement banner */}
        {announcement && !announcementDismissed && (
          <div className={`mx-4 mt-3 px-4 py-3 rounded-lg text-sm flex items-center gap-3 ${
            announcement.level === 'error' ? 'bg-error/10 border border-error text-error'
            : announcement.level === 'warning' ? 'bg-warning/10 border border-warning text-warning'
            : 'bg-accent/10 border border-accent text-accent'
          }`}>
            <span className="flex-1">{announcement.message}</span>
            <button
              onClick={() => setAnnouncementDismissed(true)}
              aria-label="Dismiss"
              className={DISMISS_BUTTON}
            >
              <span aria-hidden="true">&#10005;</span>
            </button>
          </div>
        )}

        {/* Ephemeral banner, as wide as the reading column. */}
        {isEphemeral && (
          <div className="px-4 mt-3">
            <div className={`${CHAT_COLUMN} px-4 py-2 bg-warning/10 border border-warning rounded-lg text-xs text-warning flex items-center gap-2`}>
              <span className="flex-1">This chat won't be saved. Messages are lost on refresh.</span>
              <button onClick={handleToggleEphemeral} className="flex-shrink-0 underline cursor-pointer">Turn off</button>
            </div>
          </div>
        )}

        {/* Project context banner */}
        {activeProjectId && !isEphemeral && !overlayView && (
          <div className="mx-4 mt-3 px-4 py-2 bg-accent/10 border border-accent rounded-lg text-xs text-accent flex items-center gap-2">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/>
            </svg>
            <span className="flex-1 min-w-0">This chat will be saved in {activeProjectName ?? 'this project'}</span>
            <button
              onClick={() => setActiveProjectId(null)}
              aria-label="Don't save in this project"
              className={DISMISS_BUTTON}
            ><span aria-hidden="true">&#10005;</span></button>
          </div>
        )}

        {/* PWA install banner: on the empty screen only, never pushing a
            conversation down mid-read. */}
        {showInstallBanner && isEmpty && (
          <div className="mx-4 mt-3 px-4 py-3 bg-accent/10 border border-accent rounded-lg text-sm flex items-center gap-3 text-accent">
            <span className="flex-1">
              {canInstall
                ? 'Install Munin as an app for quick access.'
                : isIOSDevice
                ? 'Install Munin: tap Share, then "Add to Home Screen".'
                : 'Install Munin: open browser menu, then "Add to Home Screen".'}
            </span>
            {canInstall && (
              <button
                onClick={handleInstall}
                className="flex-shrink-0 px-3 py-1 bg-accent text-bg-primary rounded-md text-xs font-medium cursor-pointer hover:bg-accent-hover transition-colors"
              >
                Install
              </button>
            )}
            <button
              onClick={dismissInstallBanner}
              aria-label="Dismiss"
              className={DISMISS_BUTTON}
            >
              <span aria-hidden="true">&#10005;</span>
            </button>
          </div>
        )}

        {/* Error banner */}
        {error && (
          <div title={error} className="mx-4 mt-3">
            {/* An interrupted reply already says why, in the reply itself. */}
            <ErrorNotice
              message={replyInterrupted ? 'The reply was interrupted.' : chatLoadFailed ? 'This chat could not be opened.' : friendlyError(error)}
              onRetry={canRetryReply ? handleRetryReply : undefined}
              onDismiss={() => useChatStore.setState({ error: null })}
            />
            {chatLoadFailed && (
              <button onClick={handleNewChat} className="mt-2 text-sm text-accent hover:text-accent-hover cursor-pointer">
                Start a new chat
              </button>
            )}
          </div>
        )}

        {/* Content + Artifact Panel wrapper */}
        <div className="flex-1 flex min-h-0">
          {/* Main content area */}
          <div
            className="flex-1 flex flex-col min-w-0 relative"
            onDragEnter={onChatDragEnter}
            onDragOver={onChatDragOver}
            onDragLeave={onChatDragLeave}
            onDrop={onChatDrop}
          >
            {/* Whole-window drop overlay (file drag only, chat states only) */}
            {dragActive && chatActive && (
              <div className="absolute inset-0 z-50 m-2 flex items-center justify-center rounded-2xl border-2 border-dashed border-accent bg-bg-primary/85 backdrop-blur-sm pointer-events-none">
                <div className="flex flex-col items-center gap-2 text-accent">
                  <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                    <polyline points="17 8 12 3 7 8" />
                    <line x1="12" y1="3" x2="12" y2="15" />
                  </svg>
                  <span className="text-sm font-medium">Drop files to attach</span>
                  <span className="text-xs text-text-secondary">PDF, TXT, MD, DOCX, or images</span>
                </div>
              </div>
            )}
            {/* Admin / Settings / ProjectSettings / Sleeping / Empty / Messages */}
            {showAdmin && isAdmin ? (
              <AdminPanel />
            ) : editingProject ? (
              <ProjectSettings
                project={editingProject}
                personas={personas}
                onClose={() => setEditingProject(null)}
                onUpdated={handleProjectUpdated}
              />
            ) : showSettings && userProfile ? (
              <Settings
                profile={userProfile}
                onUpdate={handleProfileUpdate}
              />
            ) : isMaintenance ? (
              <MaintenancePage
                message={status?.maintenance?.message}
                since={status?.maintenance?.since}
              />
            ) : isOffline ? (
              <SleepingPage nextStart={status?.vllm?.next_start} />
            ) : isEmpty ? (
              /* Sits a little above the middle, where the eye puts the centre. */
              <div className="flex-1 flex flex-col items-center justify-center gap-4 px-4 pb-[12dvh]">
                <div className="flex items-center gap-3 px-4">
                  <FeatherVortex size="idle" />
                  <h1 className="text-[1.75rem] md:text-3xl font-semibold tracking-tight text-text-primary text-center">
                    {greeting || 'How can I help you today?'}
                  </h1>
                </div>
                <ChatInput
                  ref={chatInputRef}
                  onSend={handleSend}
                  onSendMultimodal={handleSendMultimodal}
                  onStop={stopGenerating}
                  isStreaming={isStreaming}
                  persona={currentPersona}
                  suggestions={currentPersona?.prompt_suggestions}
                  showSuggestions={isEmpty}
                  conversationId={conversationId}
                />
              </div>
            ) : (
              <>
                <MessageList messages={messages} streaming={streaming} onSendClarification={handleSend} onDismissMemoryProposal={dismissMemoryProposal} conversationId={conversationId} onPlanApproved={handlePlanApproved} onPlanRejected={() => { /* user types follow-up themselves */ }} onPlanEdited={handlePlanEdited} onContinue={handleContinue} canReport={Boolean(conversationId) && !isEphemeral} onResend={conversationId && !isEphemeral ? handleResend : undefined} researchTimeline={drJob && drJob.conversationId === conversationId ? <ResearchTimeline job={drJob} /> : null} />
                <ChatInput
                  ref={chatInputRef}
                  onSend={handleSend}
                  onSendMultimodal={handleSendMultimodal}
                  onStop={stopGenerating}
                  isStreaming={isStreaming}
                  researchActive={drJobActive}
                  persona={currentPersona}
                  suggestions={currentPersona?.prompt_suggestions}
                  showSuggestions={false}
                  conversationId={conversationId}
                />
              </>
            )}
          </div>

          {/* Knowledge panel */}
          {knowledgePanelOpen && tagCatalog && (
            <KnowledgePanel
              catalog={tagCatalog}
              activeTags={activeTags}
              onTagsChange={setActiveTags}
            />
          )}

          {/* Artifact panel */}
          {/* Open on a new chat too: empty, and saying what will appear. */}
          {artifactPanelOpen && !isEphemeral && (
            <ArtifactPanel
              artifacts={artifacts}
              conversationId={conversationId ?? ''}
            />
          )}
        </div>
      </div>

      {/* Report dialog */}
      {showReportDialog && conversationId && (
        <ReportDialog
          conversationId={conversationId}
          onReported={() => {
            // workspaceStore's persist middleware writes the
            // updated set to localStorage (key: munin-workspace)
            // automatically. The old hand-written
            // setItem('munin_reported_chats', ...) is gone;
            // existing entries are migrated by the store's
            // onRehydrateStorage callback.
            const aboutReply = useUiStore.getState().reportReply !== null;
            setShowReportDialog(false);
            addReportedChat(conversationId);
            showToast(`${aboutReply ? 'Reply' : 'Chat'} reported. Thank you for helping us improve.`, 4000);
          }}
        />
      )}

      {/* Toast. The status region is always there, because a screen reader may
          not announce one that appears with its text already in it. It sits at
          the top, clear of the composer and its Send button. */}
      <div role="status" className="fixed top-16 left-1/2 -translate-x-1/2 z-50 max-w-[calc(100%-2rem)]">
        {toast && (
          <div className="px-4 py-2.5 bg-bg-secondary border border-border rounded-lg shadow-lg text-sm text-text-primary flex items-center gap-3">
            <span>{toast}</span>
            <button
              onClick={() => setToast(null)}
              aria-label="Dismiss"
              className="min-w-8 min-h-8 -my-1 flex items-center justify-center text-text-secondary hover:text-text-primary cursor-pointer text-xs"
            ><span aria-hidden="true">&#10005;</span></button>
          </div>
        )}
      </div>
      <SessionNotice />
    </div>
  );
}
