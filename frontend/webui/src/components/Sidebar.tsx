import { useState, useEffect, useCallback, useRef } from 'react';
import { fetchChats, deleteChat, renameChat, pinChat, unpinChat, fetchProjects, createProject, deleteProject, fileConversation, unfileConversation } from '../lib/api';
import type { ConversationSummary, Project } from '../lib/types';
import { useUserStore } from '../stores/userStore';
import { useWorkspaceStore } from '../stores/workspaceStore';

// P2 #26 commit 3: user identity slots (userEmail / userName /
// userAvatar / isAdmin) and activeProjectId pulled from stores
// directly instead of forwarded through props. onOpenSettings +
// onOpenAdmin stay as props because their App.tsx implementations
// do composite multi-store work (close sidebar on mobile, clear
// editingProject, etc.) — wrapping that as a store action is more
// surgery than this commit's scope.
interface SidebarProps {
  currentId: string | null;
  onSelect: (id: string) => void;
  onNewChat: () => void;
  onNewChatInProject?: (projectId: string) => void;
  onOpenProjectSettings?: (project: Project) => void;
  refreshKey: number;
  onOpenSettings: () => void;
  onOpenAdmin?: () => void;
}

// ── One-time migration from localStorage starring to backend pinning ────────

const STARRED_KEY = 'munin_starred_chats';

async function migrateStarredToPinned() {
  try {
    const raw = localStorage.getItem(STARRED_KEY);
    if (!raw) return;
    const ids: string[] = JSON.parse(raw);
    if (ids.length === 0) { localStorage.removeItem(STARRED_KEY); return; }
    await Promise.allSettled(ids.map(id => pinChat(id)));
    localStorage.removeItem(STARRED_KEY);
  } catch {
    // If migration fails, leave localStorage for next attempt
  }
}

// ── Grouping ────────────────────────────────────────────────────────────────

export function groupByTime(chats: ConversationSummary[]) {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today.getTime() - 86400000);
  const weekAgo = new Date(today.getTime() - 7 * 86400000);
  const monthAgo = new Date(today.getTime() - 30 * 86400000);

  const groups: { label: string; chats: ConversationSummary[] }[] = [
    { label: 'Pinned', chats: [] },
    { label: 'Today', chats: [] },
    { label: 'Yesterday', chats: [] },
    { label: 'This week', chats: [] },
    { label: 'This month', chats: [] },
    { label: 'Older', chats: [] },
  ];

  for (const chat of chats) {
    if (chat.pinned) {
      groups[0].chats.push(chat);
      continue;
    }
    const d = new Date(chat.updated_at);
    if (d >= today) groups[1].chats.push(chat);
    else if (d >= yesterday) groups[2].chats.push(chat);
    else if (d >= weekAgo) groups[3].chats.push(chat);
    else if (d >= monthAgo) groups[4].chats.push(chat);
    else groups[5].chats.push(chat);
  }

  return groups.filter(g => g.chats.length > 0);
}

export function Sidebar({ currentId, onSelect, onNewChat, onNewChatInProject, onOpenProjectSettings, refreshKey, onOpenSettings, onOpenAdmin }: SidebarProps) {
  const userProfile = useUserStore(s => s.userProfile);
  const userEmail = userProfile?.email || '';
  const userName = userProfile?.name || '';
  const userAvatar = userProfile?.avatar || '';
  const isAdmin = useUserStore(s => s.isAdmin);
  const activeProjectId = useWorkspaceStore(s => s.activeProjectId);
  const [chats, setChats] = useState<ConversationSummary[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<ConversationSummary[]>([]);
  const [profileMenuOpen, setProfileMenuOpen] = useState(false);
  const [projects, setProjects] = useState<Project[]>([]);
  const [expandedProject, setExpandedProject] = useState<string | null>(null);
  const [projectChats, setProjectChats] = useState<Record<string, ConversationSummary[]>>({});
  const [showNewProject, setShowNewProject] = useState(false);
  const [newProjectName, setNewProjectName] = useState('');
  const [moveMenuId, setMoveMenuId] = useState<string | null>(null);
  // Claude-style three-dots menu on chat rows. Mutually exclusive with
  // moveMenuId: opening one closes the other so we never stack popovers
  // on the same anchor.
  const [actionMenuId, setActionMenuId] = useState<string | null>(null);
  // Same shape as actionMenuId but for the project rows. Distinct
  // state so chat-id and project-id namespaces don't accidentally
  // collide on the rare same-string-id edge case.
  const [projectActionMenuId, setProjectActionMenuId] = useState<string | null>(null);
  const profileMenuRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const searchOverlayRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      const result = await fetchChats({ limit: 50 });
      setChats(result.conversations);
    } catch {
      // silently fail
    }
  }, []);

  const loadProjects = useCallback(async () => {
    try {
      const result = await fetchProjects();
      setProjects(result.projects);
    } catch {
      // silently fail
    }
  }, []);

  // Load project chats when expanding
  const loadProjectChats = useCallback(async (projectId: string) => {
    try {
      const result = await fetchChats({ limit: 50, project_id: projectId });
      setProjectChats(prev => ({ ...prev, [projectId]: result.conversations }));
    } catch {
      // silently fail
    }
  }, []);

  useEffect(() => {
    load();
    loadProjects();
    // Refresh expanded project's chat list too
    if (expandedProject) loadProjectChats(expandedProject);
  }, [load, loadProjects, loadProjectChats, refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // One-time migration from localStorage starring to backend pinning
  useEffect(() => { migrateStarredToPinned().then(load); }, [load]);

  useEffect(() => {
    if (expandedProject && !projectChats[expandedProject]) {
      loadProjectChats(expandedProject);
    }
  }, [expandedProject, projectChats, loadProjectChats]);

  // Auto-expand project if active conversation is in one
  useEffect(() => {
    if (activeProjectId && !expandedProject) {
      setExpandedProject(activeProjectId);
    }
  }, [activeProjectId, expandedProject]);

  // Close the row-action menu + move-to menu on any click outside a
  // `.sidebar-row-menu` element. The toggle buttons live inside the
  // same parent .relative wrapper, so mousedown handlers on the
  // buttons themselves still fire first and toggle correctly.
  useEffect(() => {
    if (!actionMenuId && !moveMenuId && !projectActionMenuId) return;
    const onMouseDown = (e: MouseEvent) => {
      const target = e.target as Element | null;
      if (target && target.closest && target.closest('.sidebar-row-menu')) {
        return;
      }
      setActionMenuId(null);
      setMoveMenuId(null);
      setProjectActionMenuId(null);
    };
    document.addEventListener('mousedown', onMouseDown);
    return () => document.removeEventListener('mousedown', onMouseDown);
  }, [actionMenuId, moveMenuId, projectActionMenuId]);

  // Search when query changes — server-side with FTS5 prefix matching
  useEffect(() => {
    if (!searchOpen) return;
    if (!searchQuery.trim()) {
      setSearchResults(chats.slice(0, 10));
      return;
    }
    const q = searchQuery.trim();
    const timer = setTimeout(async () => {
      try {
        const ftsQuery = q.endsWith('*') ? q : `${q}*`;
        const result = await fetchChats({ limit: 20, search: ftsQuery });
        setSearchResults(result.conversations);
      } catch {
        // Fallback to client-side filtering
        const lower = q.toLowerCase();
        setSearchResults(
          chats.filter(c =>
            (c.title || '').toLowerCase().includes(lower) ||
            (c.preview || '').toLowerCase().includes(lower)
          ).slice(0, 20)
        );
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [searchQuery, searchOpen, chats]);

  // Focus search input when overlay opens
  useEffect(() => {
    if (searchOpen) {
      setTimeout(() => searchInputRef.current?.focus(), 50);
    } else {
      setSearchQuery('');
    }
  }, [searchOpen]);

  // Close search on outside click
  useEffect(() => {
    if (!searchOpen) return;
    const handler = (e: MouseEvent) => {
      if (searchOverlayRef.current && !searchOverlayRef.current.contains(e.target as Node)) {
        setSearchOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [searchOpen]);

  // Close search on Escape
  useEffect(() => {
    if (!searchOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setSearchOpen(false);
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [searchOpen]);

  const handleDelete = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm('Delete this conversation?')) return;
    await deleteChat(id);
    load();
    if (id === currentId) onNewChat();
  };

  const handleRename = async (id: string) => {
    if (editTitle.trim()) {
      await renameChat(id, editTitle.trim());
      load();
    }
    setEditingId(null);
  };

  const handleSearchSelect = (id: string) => {
    onSelect(id);
    setSearchOpen(false);
  };

  const handleCreateProject = async () => {
    if (!newProjectName.trim()) return;
    try {
      await createProject({ name: newProjectName.trim() });
      setNewProjectName('');
      setShowNewProject(false);
      loadProjects();
    } catch {
      // silently fail
    }
  };

  const handleDeleteProject = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm('Delete this project? Conversations will be moved to Unfiled.')) return;
    try {
      await deleteProject(id);
      loadProjects();
      load(); // refresh chats as they become unfiled
    } catch {
      // silently fail
    }
  };

  const handleMoveToProject = async (chatId: string, projectId: string) => {
    try {
      await fileConversation(projectId, chatId);
      setMoveMenuId(null);
      load();
      loadProjects();
      if (projectChats[projectId]) loadProjectChats(projectId);
    } catch {
      // silently fail
    }
  };

  const handleUnfile = async (chatId: string, projectId: string) => {
    try {
      await unfileConversation(projectId, chatId);
      setMoveMenuId(null);
      load();
      loadProjects();
      if (projectChats[projectId]) loadProjectChats(projectId);
    } catch {
      // silently fail
    }
  };

  const togglePin = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    const chat = chats.find(c => c.id === id);
    if (!chat) return;
    try {
      if (chat.pinned) {
        await unpinChat(id);
      } else {
        await pinChat(id);
      }
      load();
    } catch {
      // silently fail
    }
  };

  // Close profile menu on outside click
  useEffect(() => {
    if (!profileMenuOpen) return;
    const handler = (e: MouseEvent) => {
      if (profileMenuRef.current && !profileMenuRef.current.contains(e.target as Node)) {
        setProfileMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [profileMenuOpen]);

  const groups = groupByTime(chats);
  const searchGroups = groupByTime(searchResults);

  return (
    <aside className="w-64 flex-shrink-0 bg-bg-secondary border-r border-border flex flex-col h-full relative">
      {/* Logo */}
      <div className="px-4 pt-4 pb-3">
        <a href="https://muninai.org" className="flex items-center gap-2.5 no-underline">
          <img
            src="/shared/munin_logo_without_script.webp"
            alt="Munin"
            className="h-7 brightness-0 invert"
          />
          <span className="text-lg font-semibold text-text-primary">Munin</span>
        </a>
      </div>

      {/* New chat + Search row */}
      <div className="px-3 pb-3 flex flex-col gap-1">
        {/* New chat */}
        <button
          onClick={onNewChat}
          className="flex items-center gap-3 px-2 py-2 rounded-lg text-text-secondary hover:bg-bg-tertiary hover:text-text-primary transition-colors cursor-pointer w-full text-left"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="flex-shrink-0">
            <line x1="12" y1="5" x2="12" y2="19" />
            <line x1="5" y1="12" x2="19" y2="12" />
          </svg>
          <span className="text-sm">New chat</span>
        </button>

        {/* Search */}
        <button
          onClick={() => setSearchOpen(true)}
          className="flex items-center gap-3 px-2 py-2 rounded-lg text-text-secondary hover:bg-bg-tertiary hover:text-text-primary transition-colors cursor-pointer w-full text-left"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="flex-shrink-0">
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <span className="text-sm">Search</span>
        </button>
      </div>

      {/* Projects + Conversation list */}
      <div className="flex-1 overflow-y-auto px-2">
        {/* Projects section */}
        {projects.length > 0 && (
          <div className="mb-3">
            <div className="px-2 py-1 text-[11px] font-semibold text-text-secondary uppercase tracking-wider flex items-center">
              <span className="flex-1">Projects</span>
              <button
                onClick={() => setShowNewProject(!showNewProject)}
                className="text-text-secondary hover:text-accent cursor-pointer text-sm"
                title="New project"
              >+</button>
            </div>

            {/* New project input */}
            {showNewProject && (
              <div className="px-2 py-1">
                <input
                  autoFocus
                  value={newProjectName}
                  onChange={e => setNewProjectName(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') handleCreateProject(); if (e.key === 'Escape') setShowNewProject(false); }}
                  placeholder="Project name..."
                  className="w-full bg-bg-primary border border-accent rounded px-2 py-1 text-xs text-text-primary placeholder-text-secondary outline-none"
                />
              </div>
            )}

            {projects.map(proj => (
              <div key={proj.id}>
                {/* Project row + its action-menu dropdown live inside
                    the same `relative` wrapper so the absolute popover
                    is positioned against the row, not the expanded
                    children below. */}
                <div className="relative">
                  <div
                    onClick={() => setExpandedProject(expandedProject === proj.id ? null : proj.id)}
                    className={`group flex items-center gap-2 px-2 py-2 rounded-md cursor-pointer text-sm transition-colors ${
                      activeProjectId === proj.id
                        ? 'bg-bg-tertiary text-text-primary'
                        : 'text-text-secondary hover:bg-bg-tertiary hover:text-text-primary'
                    }`}
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="flex-shrink-0">
                      <path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/>
                    </svg>
                    <span className="flex-1 truncate font-medium">{proj.name}</span>
                    <span className="text-xs text-text-secondary">{proj.conversation_count}</span>
                    <span className="text-[10px] text-text-secondary">{expandedProject === proj.id ? '\u25BE' : '\u25B8'}</span>

                    {/* Three-dots project action menu trigger. Fades
                        in on row hover; same opacity-only transition
                        as the chat-row kebab so the layout doesn't
                        shift. */}
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setActionMenuId(null);
                        setMoveMenuId(null);
                        setProjectActionMenuId(projectActionMenuId === proj.id ? null : proj.id);
                      }}
                      className={`sidebar-row-menu flex-shrink-0 p-1 rounded cursor-pointer transition-opacity hover:bg-bg-primary hover:text-text-primary ${
                        projectActionMenuId === proj.id
                          ? 'opacity-100 text-text-primary'
                          : 'opacity-0 group-hover:opacity-100 text-text-secondary'
                      }`}
                      title="More"
                    >
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                        <circle cx="12" cy="5" r="1.6" />
                        <circle cx="12" cy="12" r="1.6" />
                        <circle cx="12" cy="19" r="1.6" />
                      </svg>
                    </button>
                  </div>

                  {/* Project action dropdown. Same shape as the chat
                      kebab menu so they read as one consistent
                      affordance. */}
                  {projectActionMenuId === proj.id && (
                    <div className="sidebar-row-menu absolute right-0 top-full z-50 w-48 bg-bg-secondary border border-border rounded-lg shadow-lg overflow-hidden mt-1">
                      {onNewChatInProject && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            setProjectActionMenuId(null);
                            onNewChatInProject(proj.id);
                          }}
                          className="w-full text-left px-3 py-2 text-xs text-text-primary hover:bg-bg-tertiary cursor-pointer"
                        >
                          New chat in project
                        </button>
                      )}
                      {onOpenProjectSettings && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            setProjectActionMenuId(null);
                            onOpenProjectSettings(proj);
                          }}
                          className="w-full text-left px-3 py-2 text-xs text-text-primary hover:bg-bg-tertiary cursor-pointer"
                        >
                          Project settings
                        </button>
                      )}
                      <button
                        onClick={(e) => {
                          setProjectActionMenuId(null);
                          handleDeleteProject(proj.id, e);
                        }}
                        className="w-full text-left px-3 py-2 text-xs text-error hover:bg-bg-tertiary cursor-pointer"
                      >
                        Delete project
                      </button>
                    </div>
                  )}
                </div>

                {/* Expanded project chats */}
                {expandedProject === proj.id && (
                  <div className="ml-4 border-l border-border">
                    {(projectChats[proj.id] || []).map(chat => (
                      <ChatRow
                        key={chat.id}
                        chat={chat}
                        isCurrent={chat.id === currentId}
                        onSelect={onSelect}
                        onDelete={handleDelete}
                        onRename={(id) => { setEditingId(id); setEditTitle(chats.find(c => c.id === id)?.title || ''); }}
                        onPin={togglePin}
                        editingId={editingId}
                        editTitle={editTitle}
                        onEditTitleChange={setEditTitle}
                        onEditComplete={handleRename}
                        onMoveMenu={() => {
                          setActionMenuId(null);
                          setMoveMenuId(chat.id === moveMenuId ? null : chat.id);
                        }}
                        showMoveMenu={moveMenuId === chat.id}
                        onActionMenu={() => {
                          setMoveMenuId(null);
                          setActionMenuId(chat.id === actionMenuId ? null : chat.id);
                        }}
                        showActionMenu={actionMenuId === chat.id}
                        onCloseMenus={() => { setActionMenuId(null); setMoveMenuId(null); }}
                        projects={projects}
                        onMoveToProject={(pid) => handleMoveToProject(chat.id, pid)}
                        onUnfile={() => handleUnfile(chat.id, proj.id)}
                        isInProject={true}
                      />
                    ))}
                    {(projectChats[proj.id] || []).length === 0 && (
                      <div className="text-[11px] text-text-secondary py-2 pl-3">No chats</div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Create project button (when no projects exist) */}
        {projects.length === 0 && (
          <div className="mb-3">
            {showNewProject ? (
              <div className="px-2 py-1">
                <input
                  autoFocus
                  value={newProjectName}
                  onChange={e => setNewProjectName(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') handleCreateProject(); if (e.key === 'Escape') setShowNewProject(false); }}
                  placeholder="Project name..."
                  className="w-full bg-bg-primary border border-accent rounded px-2 py-1 text-xs text-text-primary placeholder-text-secondary outline-none"
                />
              </div>
            ) : (
              <button
                onClick={() => setShowNewProject(true)}
                className="flex items-center gap-2 px-2 py-1.5 text-[11px] text-text-secondary hover:text-text-primary transition-colors cursor-pointer"
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/>
                </svg>
                New project
              </button>
            )}
          </div>
        )}

        {/* Unfiled conversations */}
        {groups.map(group => (
          <div key={group.label} className="mb-3">
            <div className="px-2 py-1 text-[11px] font-semibold text-text-secondary uppercase tracking-wider">
              {group.label}
            </div>
            {group.chats.map(chat => (
              <ChatRow
                key={chat.id}
                chat={chat}
                isCurrent={chat.id === currentId}
                onSelect={onSelect}
                onDelete={handleDelete}
                onRename={(id) => { setEditingId(id); setEditTitle(chat.title || ''); }}
                onPin={togglePin}
                editingId={editingId}
                editTitle={editTitle}
                onEditTitleChange={setEditTitle}
                onEditComplete={handleRename}
                onMoveMenu={() => {
                  setActionMenuId(null);
                  setMoveMenuId(chat.id === moveMenuId ? null : chat.id);
                }}
                showMoveMenu={moveMenuId === chat.id}
                onActionMenu={() => {
                  setMoveMenuId(null);
                  setActionMenuId(chat.id === actionMenuId ? null : chat.id);
                }}
                showActionMenu={actionMenuId === chat.id}
                onCloseMenus={() => { setActionMenuId(null); setMoveMenuId(null); }}
                projects={projects}
                onMoveToProject={(pid) => handleMoveToProject(chat.id, pid)}
                isInProject={false}
              />
            ))}
          </div>
        ))}
        {chats.length === 0 && projects.length === 0 && (
          <div className="text-center text-text-secondary text-xs py-8">
            No conversations yet
          </div>
        )}
      </div>

      {/* User profile */}
      {userEmail && (
        <div ref={profileMenuRef} className="relative border-t border-border px-3 py-3">
          {/* Profile menu popup */}
          {profileMenuOpen && (
            <div className="absolute bottom-full left-2 right-2 mb-2 bg-bg-secondary border border-border rounded-xl shadow-lg z-50 overflow-hidden">
              <button
                onClick={() => { setProfileMenuOpen(false); onOpenSettings(); }}
                className="w-full flex items-center gap-3 px-3 py-2.5 text-left text-sm text-text-primary hover:bg-bg-tertiary transition-colors cursor-pointer"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-text-secondary">
                  <circle cx="12" cy="12" r="3" />
                  <path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" />
                </svg>
                Settings
              </button>
              {isAdmin && onOpenAdmin && (
                <button
                  onClick={() => { setProfileMenuOpen(false); onOpenAdmin(); }}
                  className="w-full flex items-center gap-3 px-3 py-2.5 text-left text-sm text-text-primary hover:bg-bg-tertiary transition-colors cursor-pointer"
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-text-secondary">
                    <rect x="3" y="3" width="7" height="7" />
                    <rect x="14" y="3" width="7" height="7" />
                    <rect x="14" y="14" width="7" height="7" />
                    <rect x="3" y="14" width="7" height="7" />
                  </svg>
                  Admin
                </button>
              )}
              <a
                href="https://docs.muninai.org"
                className="w-full flex items-center gap-3 px-3 py-2.5 text-left text-sm text-text-primary hover:bg-bg-tertiary transition-colors cursor-pointer no-underline"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-text-secondary">
                  <circle cx="12" cy="12" r="10" />
                  <path d="M9.09 9a3 3 0 015.83 1c0 2-3 3-3 3M12 17h.01" />
                </svg>
                Learn more
              </a>
              <div className="flex items-center gap-3 px-3 py-2.5 text-sm text-text-secondary">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="12" r="10" />
                  <line x1="2" y1="12" x2="22" y2="12" />
                  <path d="M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z" />
                </svg>
                <span>English</span>
                <span className="ml-auto text-[11px] text-text-secondary">Coming soon</span>
              </div>
            </div>
          )}

          <button
            onClick={() => setProfileMenuOpen(!profileMenuOpen)}
            className="w-full flex items-center gap-3 px-2 py-2 rounded-lg text-left hover:bg-bg-tertiary transition-colors cursor-pointer"
          >
            <div className="w-7 h-7 rounded-full bg-accent/20 text-accent flex items-center justify-center text-xs font-semibold flex-shrink-0 overflow-hidden">
              {userAvatar ? (
                <img src={userAvatar} alt="" className="w-full h-full object-cover" />
              ) : (
                (userName || userEmail)[0].toUpperCase()
              )}
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-sm text-text-primary truncate">{userName || 'Set your name'}</div>
              <div className="text-[11px] text-text-secondary truncate">{userEmail}</div>
            </div>
          </button>
        </div>
      )}

      {/* Search overlay — centered on screen */}
      {searchOpen && (
        <div className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh]">
          {/* Backdrop */}
          <div className="absolute inset-0 bg-bg-primary/70 backdrop-blur-sm" onClick={() => setSearchOpen(false)} />

          {/* Search panel */}
          <div ref={searchOverlayRef} className="relative w-full max-w-xl mx-4 bg-bg-secondary border border-border rounded-xl shadow-2xl overflow-hidden max-h-[60vh] flex flex-col">
            {/* Search input */}
            <div className="flex items-center gap-3 px-4 py-3 border-b border-border">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-text-secondary flex-shrink-0">
                <circle cx="11" cy="11" r="8" />
                <line x1="21" y1="21" x2="16.65" y2="16.65" />
              </svg>
              <input
                ref={searchInputRef}
                type="text"
                placeholder="Search conversations..."
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
                className="flex-1 bg-transparent text-sm text-text-primary placeholder-text-secondary outline-none"
              />
              <button
                onClick={() => setSearchOpen(false)}
                className="text-text-secondary hover:text-text-primary text-xs cursor-pointer"
              >
                Esc
              </button>
            </div>

            {/* Results */}
            <div className="flex-1 overflow-y-auto">
              {searchQuery.trim() === '' && (
                <div className="px-4 py-2 text-[11px] font-semibold text-text-secondary uppercase tracking-wider">
                  Recent
                </div>
              )}
              {searchGroups.length > 0 ? (
                searchGroups.map(group => (
                  <div key={group.label}>
                    {searchQuery.trim() !== '' && (
                      <div className="px-4 py-2 text-[11px] font-semibold text-text-secondary uppercase tracking-wider">
                        {group.label}
                      </div>
                    )}
                    {group.chats.map(chat => (
                      <button
                        key={chat.id}
                        onClick={() => handleSearchSelect(chat.id)}
                        className={`w-full text-left px-4 py-3 hover:bg-bg-tertiary transition-colors cursor-pointer ${
                          chat.id === currentId ? 'bg-bg-tertiary' : ''
                        }`}
                      >
                        <div className="text-sm text-text-primary truncate">
                          {chat.title || 'Untitled'}
                        </div>
                        {chat.preview && (
                          <div className="text-xs text-text-secondary mt-0.5 truncate">
                            {chat.preview}
                          </div>
                        )}
                      </button>
                    ))}
                  </div>
                ))
              ) : (
                <div className="text-center text-text-secondary text-xs py-8">
                  No matching conversations
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </aside>
  );
}

interface ChatRowProps {
  chat: ConversationSummary;
  isCurrent: boolean;
  onSelect: (id: string) => void;
  onDelete: (id: string, e: React.MouseEvent) => void;
  onRename: (id: string) => void;
  onPin: (id: string, e: React.MouseEvent) => void;
  editingId: string | null;
  editTitle: string;
  onEditTitleChange: (v: string) => void;
  onEditComplete: (id: string) => void;
  onMoveMenu: () => void;
  showMoveMenu: boolean;
  // Claude-style three-dots action menu. Mutually exclusive with the
  // move menu (only one popover open at a time per row).
  onActionMenu: () => void;
  showActionMenu: boolean;
  onCloseMenus: () => void;
  projects: Project[];
  onMoveToProject: (projectId: string) => void;
  onUnfile?: () => void;
  isInProject: boolean;
}

function ChatRow({
  chat, isCurrent, onSelect, onDelete, onRename, onPin,
  editingId, editTitle, onEditTitleChange, onEditComplete,
  onMoveMenu, showMoveMenu,
  onActionMenu, showActionMenu, onCloseMenus,
  projects, onMoveToProject, onUnfile, isInProject,
}: ChatRowProps) {
  return (
    <div className="relative">
      <div
        data-testid="chat-row"
        onClick={() => onSelect(chat.id)}
        className={`group flex items-center gap-2 px-2 py-2 rounded-md cursor-pointer text-sm transition-colors ${
          isCurrent
            ? 'bg-bg-tertiary text-text-primary'
            : 'text-text-secondary hover:bg-bg-tertiary hover:text-text-primary'
        }`}
      >
        {editingId === chat.id ? (
          <input
            autoFocus
            value={editTitle}
            onChange={e => onEditTitleChange(e.target.value)}
            onBlur={() => onEditComplete(chat.id)}
            onKeyDown={e => e.key === 'Enter' && onEditComplete(chat.id)}
            onClick={e => e.stopPropagation()}
            className="flex-1 bg-bg-primary border border-accent rounded px-1 py-0.5 text-xs text-text-primary outline-none"
          />
        ) : (
          <span className="flex-1 truncate">{chat.title || 'Untitled'}</span>
        )}

        {/* Pin */}
        <button
          onClick={(e) => onPin(chat.id, e)}
          className={`flex-shrink-0 cursor-pointer transition-colors ${
            chat.pinned
              ? 'text-accent'
              : 'text-transparent group-hover:text-text-secondary hover:!text-accent'
          }`}
          title={chat.pinned ? 'Unpin' : 'Pin'}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill={chat.pinned ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
          </svg>
        </button>

        {/* Three-dots action menu trigger. Fades in on row hover.
            Stays visible when the menu is open so the user can click
            the toggle a second time to dismiss. */}
        <button
          onClick={(e) => { e.stopPropagation(); onActionMenu(); }}
          className={`sidebar-row-menu flex-shrink-0 p-1 rounded cursor-pointer transition-opacity hover:bg-bg-primary hover:text-text-primary ${
            showActionMenu
              ? 'opacity-100 text-text-primary'
              : 'opacity-0 group-hover:opacity-100 text-text-secondary'
          }`}
          title="More"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
            <circle cx="12" cy="5" r="1.6" />
            <circle cx="12" cy="12" r="1.6" />
            <circle cx="12" cy="19" r="1.6" />
          </svg>
        </button>
      </div>

      {/* Three-dots action menu (Rename / Move / Delete). Right-
          anchored under the row, same dropdown shape as the move
          menu. `.sidebar-row-menu` keeps the click-outside listener
          in Sidebar from closing it when the user clicks inside. */}
      {showActionMenu && (
        <div className="sidebar-row-menu absolute right-0 top-full z-50 w-44 bg-bg-secondary border border-border rounded-lg shadow-lg overflow-hidden mt-1">
          <button
            onClick={(e) => { e.stopPropagation(); onCloseMenus(); onRename(chat.id); }}
            className="w-full text-left px-3 py-2 text-xs text-text-primary hover:bg-bg-tertiary cursor-pointer"
          >
            Rename
          </button>
          {projects.length > 0 && (
            <button
              onClick={(e) => { e.stopPropagation(); onMoveMenu(); }}
              className="w-full text-left px-3 py-2 text-xs text-text-primary hover:bg-bg-tertiary cursor-pointer flex items-center justify-between"
            >
              <span>Move to project</span>
              <span className="text-text-secondary text-[10px]">&#9656;</span>
            </button>
          )}
          {isInProject && onUnfile && (
            <button
              onClick={(e) => { e.stopPropagation(); onCloseMenus(); onUnfile(); }}
              className="w-full text-left px-3 py-2 text-xs text-text-primary hover:bg-bg-tertiary cursor-pointer"
            >
              Remove from project
            </button>
          )}
          <button
            onClick={(e) => { e.stopPropagation(); onCloseMenus(); onDelete(chat.id, e); }}
            className="w-full text-left px-3 py-2 text-xs text-error hover:bg-bg-tertiary cursor-pointer"
          >
            Delete
          </button>
        </div>
      )}

      {/* Move to project dropdown */}
      {showMoveMenu && (
        <div className="sidebar-row-menu absolute right-0 top-full z-50 w-44 bg-bg-secondary border border-border rounded-lg shadow-lg overflow-hidden mt-1">
          {isInProject && onUnfile && (
            <button
              onClick={(e) => { e.stopPropagation(); onCloseMenus(); onUnfile(); }}
              className="w-full text-left px-3 py-2 text-xs text-text-primary hover:bg-bg-tertiary cursor-pointer"
            >
              Remove from project
            </button>
          )}
          {projects.map(p => (
            <button
              key={p.id}
              onClick={(e) => { e.stopPropagation(); onCloseMenus(); onMoveToProject(p.id); }}
              className="w-full text-left px-3 py-2 text-xs text-text-primary hover:bg-bg-tertiary cursor-pointer truncate"
            >
              {p.name}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
