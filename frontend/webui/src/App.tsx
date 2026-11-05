import { useEffect, useCallback, useRef, useState, useSyncExternalStore } from 'react';
import { useChatLifecycle } from './hooks/useChatLifecycle';
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
import { isWidget } from './lib/artifactDisplay';
import { ProjectSettings } from './components/ProjectSettings';
import { AdminPanel } from './components/AdminPanel';
import { ReportDialog } from './components/ReportDialog';
import { KnowledgePanel } from './components/KnowledgePanel';
import { KnowledgePage } from './components/KnowledgePage';
import { ErrorNotice } from './components/ui/ErrorNotice';
import { useModal } from './hooks/useModal';
import type { Project } from './lib/types';
import { DOCS_URL, SEARCH_URL, SITE_HOME, UPLOAD_URL } from './lib/urls';
import { conversationPath, dropStaleOverlayEntry, isModifiedClick, pushAppPath, readOverlayEntry, syncOverlayHistory } from './lib/nav';

// Tailwind's md breakpoint, below which the sidebar is a drawer over the page.
const BELOW_MD = '(width < 48rem)';

function subscribeBelowMd(onChange: () => void) {
  if (typeof window.matchMedia !== 'function') return () => {};
  const query = window.matchMedia(BELOW_MD);
  query.addEventListener('change', onChange);
  return () => query.removeEventListener('change', onChange);
}

function isBelowMd() {
  return typeof window.matchMedia === 'function' && window.matchMedia(BELOW_MD).matches;
}

// Set once per browser tab session; see the isEphemeral reset below.
const SESSION_STARTED_KEY = 'munin_session_started';

const PILL_BASE = 'text-xs px-2.5 py-1 rounded-full whitespace-nowrap md:whitespace-normal transition-colors cursor-pointer border';
const PILL_OFF = 'bg-bg-tertiary border-border text-text-secondary hover:text-text-primary';
const PILL_ACCENT = 'bg-accent/15 border-accent text-accent';
const DISMISS_BUTTON = 'flex-shrink-0 min-w-8 min-h-8 -my-1 flex items-center justify-center opacity-80 hover:opacity-100 transition-opacity cursor-pointer text-xs';

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
      .catch(() => {
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
            icon_url: '/shared/munin_logo_without_script.webp',
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
      pushAppPath(expectedPath);
    }
  }, [conversationId, showDemo]);

  // Overlays and the phone drawer in the browser history (see lib/nav.ts).
  const belowMd = useSyncExternalStore(subscribeBelowMd, isBelowMd);
  const drawerOpen = sidebarOpen && belowMd && !showKnowledgePage;
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
    (knowledgePanelOpen && !!tagCatalog) || (artifactPanelOpen && !!conversationId && !isEphemeral)
  );
  useEffect(() => {
    document.body.classList.toggle('munin-side-panel-open', sidePanelShowing);
    return () => document.body.classList.remove('munin-side-panel-open');
  }, [sidePanelShowing]);

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
    if (window.innerWidth < 768) setSidebarOpen(false);
  }, [setSidebarOpen]);

  const handleSelectChat = useCallback((id: string) => {
    setIsEphemeral(false);
    setActiveProjectId(null);
    setEditingProject(null);
    setShowAdmin(false);
    loadConversation(id);
  }, [loadConversation, setActiveProjectId, setEditingProject, setIsEphemeral, setShowAdmin]);

  const handleNewChat = useCallback(() => {
    setIsEphemeral(false);
    setActiveProjectId(null);
    setEditingProject(null);
    setShowAdmin(false);
    clearConversation();
    pushAppPath('/');
  }, [clearConversation, setActiveProjectId, setEditingProject, setIsEphemeral, setShowAdmin]);

  const handleNewChatInProject = useCallback((projectId: string) => {
    setIsEphemeral(false);
    setActiveProjectId(projectId);
    setEditingProject(null);
    setShowAdmin(false);
    clearConversation();
    pushAppPath('/');
  }, [clearConversation, setActiveProjectId, setEditingProject, setIsEphemeral, setShowAdmin]);

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
    const nowEphemeral = toggleEphemeral();
    if (nowEphemeral) {
      // Entering ephemeral: clear current conversation
      clearConversation();
      pushAppPath('/');
    }
  }, [clearConversation, toggleEphemeral]);

  const handleOpenKnowledgePage = useCallback(() => {
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

  // P2 #24 Phase 2: after the user clicks Approve / Approve-all on
  // the PlanCard, the REST call has already landed (approved_at is
  // set in the DB). To get the model out of "waiting" and back into
  // a turn, send a synthetic short user message. The model sees
  // APPROVAL STATUS: APPROVED in its next system-prompt plan block
  // and retries the gated tool call naturally.
  const lastUserMessage = [...messages].reverse().find(m => m.role === 'user');
  const lastMessage = messages[messages.length - 1];
  const replyInterrupted = !!error && lastMessage?.role === 'assistant' && !!lastMessage.interrupted;
  // Sent attachments are not kept, and a message of only attachments is just a
  // placeholder such as [Image], which is nothing to send again.
  const canRetryReply = replyInterrupted && !!lastUserMessage && !/^\[.*\]$/.test(lastUserMessage.content);

  const handleRetryReply = () => {
    if (!lastUserMessage) return;
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
      setToast('The current answer is still running. Stop it or let it finish first.');
      setTimeout(() => setToast(null), 4000);
      return;
    }
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
      setToast(err instanceof Error ? err.message : 'The demo card failed to start.');
      setTimeout(() => setToast(null), 6000);
    }
  }, [clearConversation, loadConversation, sendMessage, setActiveProjectId, setEditingProject, setIsEphemeral, setShowAdmin, setToast]);

  const openDemoChat = useCallback((id: string) => {
    setShowDemo(false);
    setEditingProject(null);
    setShowAdmin(false);
    setIsEphemeral(false);
    setActiveProjectId(null);
    loadConversation(id);
  }, [loadConversation, setActiveProjectId, setEditingProject, setIsEphemeral, setShowAdmin]);

  // Handle browser back/forward
  useEffect(() => {
    const handler = () => {
      const overlay = readOverlayEntry();
      setShowSettings(overlay.view === 'settings');
      setShowAdmin(overlay.view === 'admin');
      setEditingProject(overlay.view === 'project' ? overlay.project as Project : null);
      if (isBelowMd()) setSidebarOpen(overlay.drawer);

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
      if (match) {
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

  // What fetchChat reports, whether the server refused or could not be reached.
  const chatLoadFailed = !!error && !replyInterrupted && /failed to fetch/i.test(error);

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
        <header className="flex items-center gap-3 px-4 py-2.5 border-b border-border bg-bg-secondary">
          <a href={SITE_HOME} className="flex-shrink-0 flex items-center gap-2 no-underline whitespace-nowrap">
            <img src="/shared/munin_logo_without_script.webp" alt="Munin" className="w-7 h-7 brightness-0 invert" />
            <span className="text-text-primary font-semibold text-sm">Munin</span>
          </a>
          <div className="flex-1" />
          <nav className="flex-shrink-0 flex gap-4 text-sm whitespace-nowrap">
            <a href={SITE_HOME} className="hidden md:inline text-text-secondary hover:text-accent no-underline transition-colors">Home</a>
            <a href={SEARCH_URL} className="hidden md:inline text-text-secondary hover:text-accent no-underline transition-colors">Search papers</a>
            <a href={DOCS_URL} className="hidden md:inline text-text-secondary hover:text-accent no-underline transition-colors">Docs</a>
            <span className="text-accent" aria-current="page">Knowledge</span>
            <a
              href="/"
              onClick={(e) => {
                if (isModifiedClick(e)) return;
                e.preventDefault();
                leaveKnowledgePage();
              }}
              className="text-text-secondary hover:text-accent no-underline transition-colors cursor-pointer text-sm"
            >Chat</a>
            <a href={UPLOAD_URL} className="text-text-secondary hover:text-accent no-underline transition-colors">Upload</a>
          </nav>
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
      {/* Sidebar — overlay on mobile, inline on desktop */}
      {sidebarOpen && (
        <>
          {/* Backdrop on mobile */}
          <div
            className="fixed inset-0 bg-black/50 z-40 md:hidden"
            aria-hidden="true"
            onClick={() => setSidebarOpen(false)}
          />
          <div {...drawerModal.panelProps} className="fixed inset-y-0 left-0 z-50 md:relative md:z-auto outline-none">
            {drawerOpen && <h2 id={drawerModal.titleId} className="sr-only">Menu</h2>}
            <Sidebar
              currentId={conversationId}
              onSelect={(id) => { handleSelectChat(id); setShowSettings(false); setEditingProject(null); closeDrawerOnPhone(); }}
              onNewChat={() => { handleNewChat(); setShowSettings(false); setEditingProject(null); closeDrawerOnPhone(); }}
              onNewChatInProject={(pid) => { handleNewChatInProject(pid); setShowSettings(false); closeDrawerOnPhone(); }}
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
        {/* Never wider than the main area: on a phone the pills can be swiped when
            they do not fit, and the page itself never scrolls sideways. From md up
            nothing can scroll, so the pill text wraps and the header grows. */}
        <header className="flex items-center gap-2 md:gap-3 px-4 py-2.5 border-b border-border bg-bg-secondary overflow-x-auto md:overflow-x-clip [scrollbar-width:none]">
          <button
            onClick={() => setSidebarOpen(!sidebarOpen)}
            className="p-2.5 -m-2.5 text-text-secondary hover:text-text-primary text-lg cursor-pointer"
            title={sidebarOpen ? 'Hide sidebar' : 'Show sidebar'}
            aria-label={sidebarOpen ? 'Hide sidebar' : 'Show sidebar'}
          >
            {'\u2630'}
          </button>

          {/* Ephemeral toggle */}
          <button
            data-testid="ephemeral-toggle"
            onClick={handleToggleEphemeral}
            aria-pressed={isEphemeral}
            className={`${PILL_BASE} ${isEphemeral ? 'bg-warning/15 border-warning text-warning' : PILL_OFF}`}
            title={isEphemeral ? "Temporary chat is on. This chat won't be saved." : "Start a temporary chat that won't be saved"}
          >
            Temporary chat
          </button>

          {/* Artifacts panel toggle */}
          {conversationId && !isEphemeral && (
            <button
              data-testid="artifacts-button"
              onClick={() => setArtifactPanelOpen(!artifactPanelOpen)}
              aria-pressed={artifactPanelOpen}
              className={`${PILL_BASE} ${artifactPanelOpen ? PILL_ACCENT : PILL_OFF}`}
              title={artifactPanelOpen ? 'Hide artifacts' : 'Show artifacts'}
            >
              {artifacts.length > 0 ? `Artifacts (${artifacts.length})` : 'Artifacts'}
            </button>
          )}

          {/* Knowledge panel toggle */}
          {tagCatalog && (
            <button
              onClick={() => setKnowledgePanelOpen(!knowledgePanelOpen)}
              aria-pressed={knowledgePanelOpen}
              className={`${PILL_BASE} ${knowledgePanelOpen || activeTags.length > 0 ? PILL_ACCENT : PILL_OFF}`}
              title={knowledgePanelOpen ? 'Hide sources' : 'Choose which sources to search'}
            >
              {activeTags.length > 0
                ? `Sources (${activeTags.length})`
                : 'Sources'}
            </button>
          )}

          {/* Report chat */}
          {conversationId && !isEphemeral && messages.length > 0 && (
            <button
              onClick={() => setShowReportDialog(true)}
              className={`text-xs px-2.5 py-1 rounded-full whitespace-nowrap md:whitespace-normal transition-colors cursor-pointer border ${
                reportedChats.has(conversationId)
                  ? 'bg-warning/15 border-warning text-warning'
                  : 'bg-bg-tertiary border-border text-text-secondary hover:text-text-primary'
              }`}
              title={reportedChats.has(conversationId) ? 'Chat reported' : 'Report this chat'}
              aria-label={reportedChats.has(conversationId) ? 'Chat reported' : 'Report this chat'}
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill={reportedChats.has(conversationId) ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="inline-block -mt-0.5 md:mr-1">
                <path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z" />
                <line x1="4" y1="22" x2="4" y2="15" />
              </svg>
              <span className="hidden md:inline">{reportedChats.has(conversationId) ? 'Reported' : 'Report'}</span>
            </button>
          )}

          <div className="flex-1" />

          <nav className={`hidden gap-4 text-sm ${sidebarOpen ? 'lg:flex' : 'md:flex'}`}>
            <a href={SITE_HOME} className="text-text-secondary hover:text-accent no-underline transition-colors">Home</a>
            <a href={SEARCH_URL} className="text-text-secondary hover:text-accent no-underline transition-colors">Search papers</a>
            <a href={DOCS_URL} className="text-text-secondary hover:text-accent no-underline transition-colors">Docs</a>
            <a
              href="/knowledge"
              onClick={(e) => {
                if (isModifiedClick(e)) return;
                e.preventDefault();
                handleOpenKnowledgePage();
              }}
              className="text-text-secondary hover:text-accent no-underline transition-colors cursor-pointer text-sm"
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
              className={`${overlayView ? 'text-text-secondary hover:text-accent' : 'text-accent'} no-underline transition-colors cursor-pointer`}
            >Chat</a>
            <a href={UPLOAD_URL} className="text-text-secondary hover:text-accent no-underline transition-colors">Upload</a>
          </nav>
        </header>

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

        {/* Ephemeral banner */}
        {isEphemeral && (
          <div className="mx-4 mt-3 px-4 py-2 bg-warning/10 border border-warning rounded-lg text-xs text-warning flex items-center gap-2">
            <span className="flex-1">This chat won't be saved. Messages are lost on refresh.</span>
            <button onClick={handleToggleEphemeral} className="flex-shrink-0 underline cursor-pointer">Turn off</button>
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

        {/* PWA install banner */}
        {showInstallBanner && (
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
          <div className="mx-4 mt-3">
            {/* An interrupted reply already says why, in the reply itself. */}
            <ErrorNotice
              message={replyInterrupted ? 'The reply was interrupted.' : chatLoadFailed ? 'This chat could not be opened.' : error}
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
              <div className="flex-1 flex flex-col items-center justify-center gap-6 px-4 pb-8">
                <div className="flex items-center gap-3">
                  <FeatherVortex size="idle" />
                  <h1 className="text-2xl font-semibold text-text-primary">
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
                <MessageList messages={messages} streaming={streaming} onSendClarification={handleSend} onDismissMemoryProposal={dismissMemoryProposal} conversationId={conversationId} onPlanApproved={handlePlanApproved} onPlanRejected={() => { /* user types follow-up themselves */ }} onPlanEdited={handlePlanEdited} onContinue={handleContinue} researchTimeline={drJob && drJob.conversationId === conversationId ? <ResearchTimeline job={drJob} /> : null} />
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
          {artifactPanelOpen && conversationId && !isEphemeral && (
            <ArtifactPanel
              artifacts={artifacts}
              conversationId={conversationId}
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
            setShowReportDialog(false);
            addReportedChat(conversationId);
            setToast('Chat reported — thank you for helping us improve.');
            setTimeout(() => setToast(null), 4000);
          }}
        />
      )}

      {/* Toast */}
      {toast && (
        <div role="status" className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 px-4 py-2.5 bg-bg-secondary border border-border rounded-lg shadow-lg text-sm text-text-primary flex items-center gap-3">
          <span>{toast}</span>
          <button
            onClick={() => setToast(null)}
            aria-label="Dismiss"
            className="min-w-8 min-h-8 -my-1 flex items-center justify-center text-text-secondary hover:text-text-primary cursor-pointer text-xs"
          ><span aria-hidden="true">&#10005;</span></button>
        </div>
      )}
    </div>
  );
}
