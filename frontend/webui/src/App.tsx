import { useEffect, useCallback, useRef } from 'react';
import { useChatLifecycle } from './hooks/useChatLifecycle';
import { useChatStore } from './stores/chatStore';
import { useUiStore } from './stores/uiStore';
import { useUserStore } from './stores/userStore';
import { useWorkspaceStore } from './stores/workspaceStore';
import { useStatus } from './hooks/useStatus';
import { fetchPersonas, fetchMe, fetchAnnouncement, fetchUsageStats, fetchArtifacts, fetchTags } from './lib/api';
import type { UserProfile } from './lib/api';
import { getGreeting } from './lib/greetings';
import { Sidebar } from './components/Sidebar';
import { MessageList } from './components/MessageList';
import { ChatInput } from './components/ChatInput';
import { FeatherVortex } from './components/FeatherVortex';
import { SleepingPage } from './components/SleepingPage';
import { MaintenancePage } from './components/MaintenancePage';
import { Settings } from './components/Settings';
import { ArtifactPanel } from './components/ArtifactPanel';
import { ProjectSettings } from './components/ProjectSettings';
import { AdminPanel } from './components/AdminPanel';
import { ReportDialog } from './components/ReportDialog';
import { KnowledgePanel } from './components/KnowledgePanel';
import { KnowledgePage } from './components/KnowledgePage';
import type { Project } from './lib/types';

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
  // incognito after F5. The previous useState always defaulted to
  // false on reload.
  const isEphemeral = useWorkspaceStore(s => s.isEphemeral);
  const setIsEphemeral = useWorkspaceStore(s => s.setIsEphemeral);
  const toggleEphemeral = useWorkspaceStore(s => s.toggleEphemeral);
  const deferredPromptRef = useRef<BeforeInstallPromptEvent | null>(null);
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
  }, []);

  // Load announcement and admin status on mount
  useEffect(() => {
    fetchAnnouncement()
      .then(a => setAnnouncement(a))
      .catch(() => {});
    fetchUsageStats()
      .then(stats => { if (stats.is_admin) setIsAdmin(true); })
      .catch(() => {});
  }, []);

  // Load tag catalog on mount
  useEffect(() => {
    fetchTags()
      .then(setTagCatalog)
      .catch(() => {});
  }, []);

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
        // Fallback personas if API is down
        setPersonas([
          {
            id: 'chat',
            name: 'Meitner - Chat',
            description: 'General-purpose assistant',
            icon_url: '/shared/meitner-chat-inverted.svg',
            tags: [],
            capabilities: {},
            prompt_suggestions: [],
          },
          {
            id: 'code',
            name: 'Turing - Code',
            description: 'Programming and technical tasks',
            icon_url: '/shared/turing-code-inverted.svg',
            tags: [],
            capabilities: {},
            prompt_suggestions: [],
          },
          {
            id: 'research',
            name: 'Curie - Research',
            description: 'Academic research and analysis',
            icon_url: '/shared/curie-research-inverted.svg',
            tags: [],
            capabilities: {},
            prompt_suggestions: [],
          },
        ]);
      });
  }, []);

  // URL routing: read conversation ID, persona, and knowledge route from URL on mount
  useEffect(() => {
    const path = window.location.pathname;
    const knowledgeMatch = path.match(/^\/knowledge(?:\/(.+))?$/);
    if (knowledgeMatch) {
      setShowKnowledgePage(knowledgeMatch[1] || true);
      return;
    }
    const match = path.match(/^\/c\/(.+)$/);
    if (match) {
      loadConversation(match[1]);
    }
    const params = new URLSearchParams(window.location.search);
    const persona = params.get('persona');
    if (persona) {
      setSelectedPersona(persona);
      // Clean the URL
      window.history.replaceState(null, '', window.location.pathname);
    }
  }, [loadConversation]);

  // Update URL when conversation changes
  useEffect(() => {
    const currentPath = window.location.pathname;
    const expectedPath = conversationId ? `/c/${conversationId}` : '/';
    if (currentPath !== expectedPath) {
      window.history.pushState(null, '', expectedPath);
    }
  }, [conversationId]);

  // Load artifacts when conversation changes
  useEffect(() => {
    if (conversationId && !isEphemeral) {
      fetchArtifacts(conversationId)
        .then(res => setArtifacts(res.artifacts))
        .catch(() => {}); // may not have artifacts
    }
  }, [conversationId, isEphemeral, setArtifacts]);

  // Auto-open panel and select the latest artifact whenever one is created or updated
  useEffect(() => {
    if (lastArtifactEvent) {
      setArtifactPanelOpen(true);
      setSelectedArtifactId(lastArtifactEvent.id);
    }
  }, [lastArtifactEvent]);

  // Sync the persona indicator to the conversation's persona. Triggers on:
  //  - conversation load (persisted persona is authoritative)
  //  - persona_changed SSE event after a delegation
  useEffect(() => {
    if (conversationPersona && conversationPersona !== selectedPersona) {
      setSelectedPersona(conversationPersona);
    }
  }, [conversationPersona, selectedPersona]);

  // Refresh sidebar when a stream completes (new messages) — skip for ephemeral
  // Also clear activeProjectId once the conversation is created (filed by backend)
  useEffect(() => {
    if (streaming.phase === 'idle' && messages.length > 0 && !isEphemeral) {
      bumpSidebarRefresh();
      if (activeProjectId && conversationId) {
        setActiveProjectId(null);
      }
    }
  }, [streaming.phase, messages.length, isEphemeral, activeProjectId, conversationId]);

  const handleSelectChat = useCallback((id: string) => {
    setIsEphemeral(false);
    setActiveProjectId(null);
    setEditingProject(null);
    setShowAdmin(false);
    loadConversation(id);
  }, [loadConversation]);

  const handleNewChat = useCallback(() => {
    setIsEphemeral(false);
    setActiveProjectId(null);
    setEditingProject(null);
    setShowAdmin(false);
    clearConversation();
    window.history.pushState(null, '', '/');
  }, [clearConversation]);

  const handleNewChatInProject = useCallback((projectId: string) => {
    setIsEphemeral(false);
    setActiveProjectId(projectId);
    setEditingProject(null);
    clearConversation();
    window.history.pushState(null, '', '/');
  }, [clearConversation]);

  const handleOpenProjectSettings = useCallback((project: Project) => {
    setEditingProject(project);
    setShowSettings(false);
  }, []);

  const handleProjectUpdated = useCallback((updated: Project) => {
    setEditingProject(updated);
    bumpSidebarRefresh();
  }, []);

  const handleToggleEphemeral = useCallback(() => {
    const nowEphemeral = toggleEphemeral();
    if (nowEphemeral) {
      // Entering ephemeral: clear current conversation
      clearConversation();
      window.history.pushState(null, '', '/');
    }
  }, [clearConversation, toggleEphemeral]);

  const handleProfileUpdate = useCallback((updated: UserProfile) => {
    setUserProfile(updated);
    setGreeting(getGreeting(updated.name));
  }, []);

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

  const handleSendMultimodal = useCallback((content: Array<{ type: string; text?: string; image_url?: { url: string } }>) => {
    const textPart = content.find(c => c.type === 'text')?.text || '[Image]';
    sendMessage(textPart, selectedPersona, isEphemeral, content, projectIdForNewChat, activeTags.length > 0 ? activeTags : undefined);
  }, [sendMessage, selectedPersona, isEphemeral, projectIdForNewChat, activeTags]);

  // Handle browser back/forward
  useEffect(() => {
    const handler = () => {
      const path = window.location.pathname;
      const knowledgeMatch = path.match(/^\/knowledge(?:\/(.+))?$/);
      if (knowledgeMatch) {
        setShowKnowledgePage(knowledgeMatch[1] || true);
        return;
      }
      setShowKnowledgePage(false);
      const match = path.match(/^\/c\/(.+)$/);
      if (match) {
        loadConversation(match[1]);
      } else {
        clearConversation();
      }
    };
    window.addEventListener('popstate', handler);
    return () => window.removeEventListener('popstate', handler);
  }, [loadConversation, clearConversation]);

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
  }, []);

  const handleInstall = async () => {
    if (deferredPromptRef.current) {
      deferredPromptRef.current.prompt();
      const result = await deferredPromptRef.current.userChoice;
      if (result.outcome === 'accepted') {
        setShowInstallBanner(false);
      }
      deferredPromptRef.current = null;
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

  // ── Knowledge full-page view ──────────────────────────────────────────────
  if (showKnowledgePage) {
    return (
      <div className="flex flex-col h-screen">
        {/* Standalone header for knowledge page */}
        <header className="flex items-center gap-3 px-4 py-2.5 border-b border-border bg-bg-secondary">
          <a href="https://muninai.org" className="flex items-center gap-2 no-underline">
            <img src="/shared/munin_logo_without_script.webp" alt="Munin" className="w-7 h-7" />
            <span className="text-text-primary font-semibold text-sm">Munin</span>
          </a>
          <div className="flex-1" />
          <nav className="flex gap-4 text-sm">
            <a href="https://muninai.org" className="text-text-secondary hover:text-accent no-underline transition-colors">Home</a>
            <a href="https://search.muninai.org" className="text-text-secondary hover:text-accent no-underline transition-colors">Search</a>
            <a href="https://research.muninai.org" className="text-text-secondary hover:text-accent no-underline transition-colors">Research</a>
            <a href="https://docs.muninai.org" className="text-text-secondary hover:text-accent no-underline transition-colors">Docs</a>
            <span className="text-accent">Knowledge</span>
            <button
              onClick={() => { setShowKnowledgePage(false); window.history.pushState(null, '', '/'); }}
              className="text-text-secondary hover:text-accent no-underline transition-colors cursor-pointer bg-transparent border-none p-0 text-sm"
            >Chat</button>
            <a href="https://upload.muninai.org" className="text-text-secondary hover:text-accent no-underline transition-colors">Upload</a>
          </nav>
        </header>
        <KnowledgePage
          initialRoute={typeof showKnowledgePage === 'string' ? showKnowledgePage : undefined}
          tagCatalog={tagCatalog}
          onClose={() => {
            setShowKnowledgePage(false);
            window.history.pushState(null, '', '/');
          }}
          onChatWithTag={(tags) => {
            setActiveTags(tags);
            setShowKnowledgePage(false);
            clearConversation();
            window.history.pushState(null, '', '/');
          }}
        />
      </div>
    );
  }

  return (
    <div className="flex h-screen">
      {/* Sidebar — overlay on mobile, inline on desktop */}
      {sidebarOpen && (
        <>
          {/* Backdrop on mobile */}
          <div
            className="fixed inset-0 bg-black/50 z-40 md:hidden"
            onClick={() => setSidebarOpen(false)}
          />
          <div className="fixed inset-y-0 left-0 z-50 md:relative md:z-auto">
            <Sidebar
              currentId={conversationId}
              onSelect={(id) => { handleSelectChat(id); setShowSettings(false); setEditingProject(null); if (window.innerWidth < 768) setSidebarOpen(false); }}
              onNewChat={() => { handleNewChat(); setShowSettings(false); setEditingProject(null); if (window.innerWidth < 768) setSidebarOpen(false); }}
              onNewChatInProject={(pid) => { handleNewChatInProject(pid); setShowSettings(false); if (window.innerWidth < 768) setSidebarOpen(false); }}
              onOpenProjectSettings={handleOpenProjectSettings}
              refreshKey={sidebarRefreshKey}
              onOpenSettings={() => { setShowSettings(true); setEditingProject(null); setShowAdmin(false); if (window.innerWidth < 768) setSidebarOpen(false); }}
              onOpenAdmin={() => { setShowAdmin(true); setShowSettings(false); setEditingProject(null); if (window.innerWidth < 768) setSidebarOpen(false); }}
            />
          </div>
        </>
      )}

      {/* Main area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header — minimal, just sidebar toggle and nav links */}
        <header className="flex items-center gap-3 px-4 py-2.5 border-b border-border bg-bg-secondary">
          <button
            onClick={() => setSidebarOpen(!sidebarOpen)}
            className="text-text-secondary hover:text-text-primary text-lg cursor-pointer"
            title={sidebarOpen ? 'Hide sidebar' : 'Show sidebar'}
          >
            {'\u2630'}
          </button>

          {/* Ephemeral toggle */}
          <button
            onClick={handleToggleEphemeral}
            className={`text-xs px-2.5 py-1 rounded-full transition-colors cursor-pointer border ${
              isEphemeral
                ? 'bg-warning/15 border-warning text-warning'
                : 'bg-bg-tertiary border-border text-text-secondary hover:text-text-primary'
            }`}
            title={isEphemeral ? 'Ephemeral mode ON — chats won\'t be saved' : 'Enable ephemeral mode'}
          >
            {isEphemeral ? 'Ephemeral' : 'Ephemeral'}
          </button>

          {/* Artifacts panel toggle */}
          {conversationId && !isEphemeral && (
            <button
              onClick={() => setArtifactPanelOpen(!artifactPanelOpen)}
              className={`text-xs px-2.5 py-1 rounded-full transition-colors cursor-pointer border ${
                artifactPanelOpen
                  ? 'bg-accent/15 border-accent text-accent'
                  : 'bg-bg-tertiary border-border text-text-secondary hover:text-text-primary'
              }`}
              title={artifactPanelOpen ? 'Hide artifacts' : 'Show artifacts'}
            >
              {artifacts.length > 0 ? `Artifacts (${artifacts.length})` : 'Artifacts'}
            </button>
          )}

          {/* Knowledge panel toggle */}
          {tagCatalog && (
            <button
              onClick={() => setKnowledgePanelOpen(!knowledgePanelOpen)}
              className={`text-xs px-2.5 py-1 rounded-full transition-colors cursor-pointer border ${
                knowledgePanelOpen || activeTags.length > 0
                  ? 'bg-accent/15 border-accent text-accent'
                  : 'bg-bg-tertiary border-border text-text-secondary hover:text-text-primary'
              }`}
              title={knowledgePanelOpen ? 'Hide knowledge panel' : 'Browse available knowledge'}
            >
              {activeTags.length > 0
                ? `Knowledge (${activeTags.length})`
                : 'Knowledge'}
            </button>
          )}

          {/* Report chat */}
          {conversationId && !isEphemeral && messages.length > 0 && (
            <button
              onClick={() => setShowReportDialog(true)}
              className={`text-xs px-2.5 py-1 rounded-full transition-colors cursor-pointer border ${
                reportedChats.has(conversationId)
                  ? 'bg-warning/15 border-warning text-warning'
                  : 'bg-bg-tertiary border-border text-text-secondary hover:text-text-primary'
              }`}
              title={reportedChats.has(conversationId) ? 'Chat reported' : 'Report this chat'}
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill={reportedChats.has(conversationId) ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="inline-block -mt-0.5 mr-1">
                <path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z" />
                <line x1="4" y1="22" x2="4" y2="15" />
              </svg>
              {reportedChats.has(conversationId) ? 'Reported' : 'Report'}
            </button>
          )}

          <div className="flex-1" />

          <nav className="hidden md:flex gap-4 text-sm">
            <a href="https://muninai.org" className="text-text-secondary hover:text-accent no-underline transition-colors">Home</a>
            <a href="https://search.muninai.org" className="text-text-secondary hover:text-accent no-underline transition-colors">Search</a>
            <a href="https://research.muninai.org" className="text-text-secondary hover:text-accent no-underline transition-colors">Research</a>
            <a href="https://docs.muninai.org" className="text-text-secondary hover:text-accent no-underline transition-colors">Docs</a>
            <button
              onClick={() => {
                setShowKnowledgePage(true);
                setShowSettings(false);
                setShowAdmin(false);
                setEditingProject(null);
                window.history.pushState(null, '', '/knowledge');
              }}
              className={`${showKnowledgePage ? 'text-accent' : 'text-text-secondary hover:text-accent'} no-underline transition-colors cursor-pointer bg-transparent border-none p-0 text-sm`}
            >Knowledge</button>
            <a href="https://chat.muninai.org" className={`${!showKnowledgePage ? 'text-accent' : 'text-text-secondary hover:text-accent'} no-underline transition-colors`}>Chat</a>
            <a href="https://upload.muninai.org" className="text-text-secondary hover:text-accent no-underline transition-colors">Upload</a>
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
              className="flex-shrink-0 opacity-60 hover:opacity-100 transition-opacity cursor-pointer text-xs"
            >
              &#10005;
            </button>
          </div>
        )}

        {/* Ephemeral banner */}
        {isEphemeral && (
          <div className="mx-4 mt-3 px-4 py-2 bg-warning/10 border border-warning rounded-lg text-xs text-warning flex items-center gap-2">
            <span>This chat won't be saved. Messages are lost on refresh.</span>
          </div>
        )}

        {/* Project context banner */}
        {activeProjectId && !isEphemeral && !showSettings && !editingProject && (
          <div className="mx-4 mt-3 px-4 py-2 bg-accent/10 border border-accent rounded-lg text-xs text-accent flex items-center gap-2">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/>
            </svg>
            <span>New chats will be filed into this project</span>
            <button
              onClick={() => setActiveProjectId(null)}
              className="ml-auto text-accent/60 hover:text-accent cursor-pointer"
            >&#10005;</button>
          </div>
        )}

        {/* PWA install banner */}
        {showInstallBanner && (
          <div className="mx-4 mt-3 px-4 py-3 bg-accent/10 border border-accent rounded-lg text-sm flex items-center gap-3 text-accent">
            <span className="flex-1">
              {deferredPromptRef.current
                ? 'Install Munin as an app for quick access.'
                : isIOSDevice
                ? 'Install Munin: tap Share, then "Add to Home Screen".'
                : 'Install Munin: open browser menu, then "Add to Home Screen".'}
            </span>
            {deferredPromptRef.current && (
              <button
                onClick={handleInstall}
                className="flex-shrink-0 px-3 py-1 bg-accent text-bg-primary rounded-md text-xs font-medium cursor-pointer hover:bg-accent-hover transition-colors"
              >
                Install
              </button>
            )}
            <button
              onClick={dismissInstallBanner}
              className="flex-shrink-0 opacity-60 hover:opacity-100 transition-opacity cursor-pointer text-xs"
            >
              &#10005;
            </button>
          </div>
        )}

        {/* Error banner */}
        {error && (
          <div className="mx-4 mt-3 px-4 py-3 bg-error/10 border border-error rounded-lg text-error text-sm">
            {error}
          </div>
        )}

        {/* Content + Artifact Panel wrapper */}
        <div className="flex-1 flex min-h-0">
          {/* Main content area */}
          <div className="flex-1 flex flex-col min-w-0">
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
                <MessageList messages={messages} streaming={streaming} personas={personas} onSendClarification={handleSend} onDismissMemoryProposal={dismissMemoryProposal} conversationId={conversationId} onPlanApproved={handlePlanApproved} onPlanRejected={() => { /* user types follow-up themselves */ }} onPlanEdited={handlePlanEdited} />
                <ChatInput
                  onSend={handleSend}
                  onSendMultimodal={handleSendMultimodal}
                  onStop={stopGenerating}
                  isStreaming={isStreaming}
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
          {artifactPanelOpen && conversationId && !isEphemeral && !knowledgePanelOpen && (
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
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 px-4 py-2.5 bg-bg-secondary border border-border rounded-lg shadow-lg text-sm text-text-primary flex items-center gap-3">
          <span>{toast}</span>
          <button
            onClick={() => setToast(null)}
            className="text-text-secondary hover:text-text-primary cursor-pointer text-xs"
          >&#10005;</button>
        </div>
      )}
    </div>
  );
}
