import { useState, useEffect, useCallback, useId, useLayoutEffect, useRef } from 'react';
import type { RefObject } from 'react';
import { fetchChat, fetchChats, deleteChat, renameChat, pinChat, unpinChat, fetchProjects, createProject, deleteProject, updateProject, fileConversation, unfileConversation } from '../lib/api';
import type { Conversation, ConversationSummary, Project } from '../lib/types';
import { useMenu } from '../hooks/useMenu';
import { useModal } from '../hooks/useModal';
import { useUiStore } from '../stores/uiStore';
import { useUserStore } from '../stores/userStore';
import { useWorkspaceStore } from '../stores/workspaceStore';
import { AUTH_BASE, DOCS_URL, SEARCH_URL, SITE_HOME, UPLOAD_URL } from '../lib/urls';
import { conversationPath, isModifiedClick, newChatPath } from '../lib/nav';
import { groupByTime } from '../lib/chatGrouping';
import { ErrorNotice } from './ui/ErrorNotice';
import { DIALOG_BACKDROP, LOGO_FILTER } from './ui/themeClasses';
import { BookIcon, ChatIcon, FolderIcon, PageIcon, PlusIcon, SearchIcon } from './ui/icons';

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
  onOpenKnowledge?: () => void;
  // Set when the sidebar is the phone drawer; shows its close button.
  onCloseDrawer?: () => void;
}

// Set by /shared/support-widget.js, if it has loaded.
type WindowWithSupport = Window & { MuninSupport?: { open: () => void } };

// The date groups list only chats with no project; a filed chat lives in its
// project, and if pinned, in the Pinned group as well. The backend treats this
// project_id as "no project".
const UNFILED = '__unfiled__';

const PAGE_SIZE = 50;
// How often the lists are read again while a chat in them is generating, so its
// dot goes out when the turn ends in the background.
const GENERATING_POLL_MS = 12_000;
// The API refuses a larger limit.
const API_MAX_LIMIT = 200;

type LoadState = 'loading' | 'loaded' | 'error';

function uniqueById(chats: ConversationSummary[]): ConversationSummary[] {
  const seen = new Set<string>();
  return chats.filter(c => !seen.has(c.id) && seen.add(c.id));
}

// Re-reads a list at the length it has reached, so a refresh does not undo Show more.
async function fetchFirstChats(projectId: string, count: number) {
  const wanted = Math.max(count, PAGE_SIZE);
  const conversations: ConversationSummary[] = [];
  let total = 0;
  for (let offset = 0; offset < wanted; offset += API_MAX_LIMIT) {
    const page = await fetchChats({ limit: Math.min(API_MAX_LIMIT, wanted - offset), offset, project_id: projectId });
    conversations.push(...page.conversations);
    total = page.total;
    if (page.conversations.length === 0) break;
  }
  return { conversations: uniqueById(conversations), total };
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

// ── Menus ───────────────────────────────────────────────────────────────────

const MENU_PANEL = 'fixed z-50 bg-bg-secondary border border-border rounded-lg shadow-lg overflow-y-auto';
const MENU_ITEM = 'block w-full text-left px-3 py-2 text-sm text-text-primary hover:bg-bg-tertiary focus-visible:bg-bg-tertiary cursor-pointer no-underline';
const ACCOUNT_ITEM = 'w-full flex items-center gap-3 px-3 py-2.5 text-left text-sm text-text-primary hover:bg-bg-tertiary focus-visible:bg-bg-tertiary transition-colors cursor-pointer no-underline';
// Every row in the sidebar (New chat, Search, a project, a chat) is one shape:
// 32px tall (40px on a phone), the same 8px inset, a 14px label. Hover is a
// lighter tint than the current row, so the two can be told apart.
const SIDEBAR_ROW = 'flex items-center gap-2.5 w-full h-8 max-md:h-10 px-2 rounded-md text-sm text-left no-underline transition-colors cursor-pointer';
const ROW_IDLE = 'text-text-secondary hover:bg-bg-tertiary/60 hover:text-text-primary';
const ROW_CURRENT = 'bg-bg-tertiary text-text-primary';
const SECTION_LABEL = 'px-2 pt-3 pb-1 text-xs font-medium text-text-tertiary';
// A 32px target below md, with negative margin so the row does not grow.
const ROW_ICON_TARGET = 'max-md:p-[9px] max-md:-my-[9px]';

/**
 * Puts a row menu next to its trigger with fixed positioning, which is what gets
 * it out of the scrolling list that would otherwise clip it. It opens upward when
 * there is more room there, and scrolls inside itself if it fits neither way.
 * `content` is whatever changes the menu's height.
 */
function useFixedMenuPosition(
  open: boolean,
  triggerRef: RefObject<HTMLElement | null>,
  menuRef: RefObject<HTMLElement | null>,
  content?: unknown,
) {
  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const trigger = triggerRef.current;
      const menu = menuRef.current;
      if (!trigger || !menu) return;
      const edge = 8;
      const gap = 4;
      const rect = trigger.getBoundingClientRect();
      const below = window.innerHeight - rect.bottom - gap - edge;
      const above = rect.top - gap - edge;
      menu.style.maxHeight = 'none';
      const height = menu.offsetHeight;
      const up = height > below && above > below;
      const room = up ? above : below;
      menu.style.maxHeight = `${room}px`;
      menu.style.top = up ? `${rect.top - gap - Math.min(height, room)}px` : `${rect.bottom + gap}px`;
      menu.style.right = `${Math.max(edge, window.innerWidth - rect.right)}px`;
    };
    place();
    window.addEventListener('scroll', place, true);
    window.addEventListener('resize', place);
    return () => {
      window.removeEventListener('scroll', place, true);
      window.removeEventListener('resize', place);
    };
  }, [open, triggerRef, menuRef, content]);
}

/**
 * Keyboard for a list of chat rows, which is one Tab stop (a roving tabindex):
 * only one row's link has tabIndex 0, and ArrowUp/ArrowDown, Home and End move
 * focus between the rows' links. Enter opens the focused chat, as the link does.
 * The focused row's ⋮ button is the next Tab stop after its link, and the
 * ContextMenu key or Shift+F10 on the link opens that menu directly (see
 * ChatRow). Keys from the rename input or an open menu are left alone.
 */
function moveAmongChats(e: React.KeyboardEvent<HTMLElement>) {
  if (e.defaultPrevented) return;
  const target = e.target as HTMLElement;
  if (target.tagName === 'INPUT') return;
  const row = target.closest('[data-testid="chat-row"]');
  if (!row || !e.currentTarget.contains(row)) return;
  const links = Array.from(e.currentTarget.querySelectorAll<HTMLElement>('[data-chat-link]'));
  const index = links.findIndex(link => row.contains(link));
  if (index === -1) return;
  const moves: Record<string, number> = {
    ArrowDown: Math.min(index + 1, links.length - 1),
    ArrowUp: Math.max(index - 1, 0),
    Home: 0,
    End: links.length - 1,
  };
  const next = moves[e.key];
  if (next === undefined) return;
  e.preventDefault();
  links[next].focus();
}

function KebabIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <circle cx="12" cy="5" r="1.6" />
      <circle cx="12" cy="12" r="1.6" />
      <circle cx="12" cy="19" r="1.6" />
    </svg>
  );
}

// ── New project ─────────────────────────────────────────────────────────────

function NewProjectInput({ value, onChange, onCreate, onCancel }: {
  value: string;
  onChange: (value: string) => void;
  onCreate: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="px-2 py-1">
      <input
        autoFocus
        aria-label="Project name"
        value={value}
        onChange={e => onChange(e.target.value)}
        onBlur={() => { if (!value.trim()) onCancel(); }}
        onKeyDown={e => {
          if (e.key === 'Enter') onCreate();
          if (e.key === 'Escape') { e.preventDefault(); onCancel(); }
        }}
        placeholder="Project name..."
        className="w-full bg-bg-primary border border-accent rounded px-2 py-1 text-xs text-text-primary placeholder-text-secondary outline-none"
      />
      <div className="flex justify-end gap-1 mt-1">
        <button
          type="button"
          onClick={onCancel}
          className="px-2 py-1 text-xs text-text-secondary hover:text-text-primary cursor-pointer"
        >Cancel</button>
        <button
          type="button"
          onClick={onCreate}
          disabled={!value.trim()}
          className="px-2 py-1 text-xs text-accent hover:text-accent-hover cursor-pointer disabled:opacity-50 disabled:cursor-default"
        >Create</button>
      </div>
    </div>
  );
}

// ── Sidebar ─────────────────────────────────────────────────────────────────

export function Sidebar({ currentId, onSelect, onNewChat, onNewChatInProject, onOpenProjectSettings, refreshKey, onOpenSettings, onOpenAdmin, onOpenKnowledge, onCloseDrawer }: SidebarProps) {
  const userProfile = useUserStore(s => s.userProfile);
  const userEmail = userProfile?.email || '';
  const userName = userProfile?.name || '';
  const userAvatar = userProfile?.avatar || '';
  const isAdmin = useUserStore(s => s.isAdmin);
  const activeProjectId = useWorkspaceStore(s => s.activeProjectId);
  const showToast = useUiStore(s => s.showToast);
  const [chats, setChats] = useState<ConversationSummary[]>([]);
  const [chatsTotal, setChatsTotal] = useState(0);
  const [chatsState, setChatsState] = useState<LoadState>('loading');
  // Pinned chats that are filed in a project, which `chats` leaves out.
  const [pinnedFiled, setPinnedFiled] = useState<ConversationSummary[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  // The results and the query they answer, so a query still waiting for its
  // results is never shown someone else's.
  const [searched, setSearched] = useState<{ query: string; results: ConversationSummary[] }>({ query: '', results: [] });
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectsState, setProjectsState] = useState<LoadState>('loading');
  const [archivedProjects, setArchivedProjects] = useState<Project[]>([]);
  const [archivedOpen, setArchivedOpen] = useState(false);
  // What the search overlay lists before anything is typed: every chat, filed or
  // not, unlike `chats`. Empty until it has loaded, when `chats` stands in.
  const [recentChats, setRecentChats] = useState<ConversationSummary[]>([]);
  // Which project folder is open. `undefined` means the user has not touched
  // it, in which case the project of what is being worked on (if any) is shown
  // expanded; `null` means they collapsed it deliberately and we must not re-open
  // it. That sentinel is what replaces the old "auto-expand" effect, which could
  // not tell "never opened" from "closed on purpose".
  const [userExpandedProject, setUserExpandedProject] = useState<string | null | undefined>(undefined);
  const [projectChats, setProjectChats] = useState<Record<string, ConversationSummary[]>>({});
  const [projectTotals, setProjectTotals] = useState<Record<string, number>>({});
  const [projectChatsFailed, setProjectChatsFailed] = useState<Record<string, boolean>>({});
  // A list being extended by Show more, keyed by project id or UNFILED.
  const [loadingMore, setLoadingMore] = useState<string | null>(null);
  // How long each list has become, for the refresh effects to re-read.
  const listLengths = useRef<Record<string, number>>({});
  const [showNewProject, setShowNewProject] = useState(false);
  const [newProjectName, setNewProjectName] = useState('');
  const [moveMenuId, setMoveMenuId] = useState<string | null>(null);
  // Claude-style three-dots menu on chat rows. Mutually exclusive with
  // moveMenuId: opening one closes the other so we never stack popovers
  // on the same anchor.
  const [actionMenuId, setActionMenuId] = useState<string | null>(null);
  // The row that last held focus, which keeps the Tab stop of its chat list.
  const [rovingRowId, setRovingRowId] = useState<string | null>(null);
  const groupIdPrefix = useId();
  // Pin and Unpin move a chat to another group, which remounts its row and drops
  // the focus its menu gave back to the ⋮ button. Once the lists re-read, focus
  // goes to the chat's link in its new place, so the keyboard keeps its spot.
  const refocusChat = useRef<string | null>(null);
  // Same shape as actionMenuId but for the project rows. Distinct
  // state so chat-id and project-id namespaces don't accidentally
  // collide on the rare same-string-id edge case.
  const [projectActionMenuId, setProjectActionMenuId] = useState<string | null>(null);
  // The project of the open chat, when it is not in a list we hold.
  const [currentChatProject, setCurrentChatProject] = useState<{ chatId: string; projectId: string } | null>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const profileMenu = useMenu();

  const closeSearch = useCallback(() => {
    setSearchOpen(false);
    setSearchQuery('');
  }, []);
  const searchModal = useModal({ onClose: closeSearch, active: searchOpen, initialFocusRef: searchInputRef });

  const currentListedIn = currentId
    ? Object.keys(projectChats).find(pid => projectChats[pid].some(c => c.id === currentId))
    : undefined;
  const currentIsListed = !!currentListedIn || chats.some(c => c.id === currentId);
  const currentProject = currentListedIn
    ?? (currentChatProject?.chatId === currentId ? currentChatProject?.projectId : undefined)
    ?? null;
  const expandedProject = userExpandedProject === undefined ? (activeProjectId ?? currentProject) : userExpandedProject;

  // Reads live in the effects that own them rather than in callbacks the
  // effects call, so nothing sets state synchronously when an effect fires
  // and a response arriving after unmount is dropped. `load` is what the
  // mutation handlers below call; it asks for another read instead of
  // performing one, and `refreshKey` (the prop App bumps) feeds the same
  // effects.
  const [reloadKey, setReloadKey] = useState(0);
  const load = useCallback(() => setReloadKey(k => k + 1), []);

  useEffect(() => {
    let cancelled = false;
    const wanted = listLengths.current[UNFILED] ?? 0;
    fetchFirstChats(UNFILED, wanted)
      .then(r => {
        // Shorter than the list has become: Show more landed and is re-reading it.
        if (cancelled || (listLengths.current[UNFILED] ?? 0) > wanted) return;
        listLengths.current[UNFILED] = r.conversations.length;
        setChats(r.conversations);
        setChatsTotal(r.total);
        setChatsState('loaded');
      })
      .catch(() => { if (!cancelled) setChatsState('error'); });
    fetchChats({ pinned_only: true, limit: API_MAX_LIMIT })
      .then(r => { if (!cancelled) setPinnedFiled(r.conversations.filter(c => c.pinned && c.project_id)); })
      .catch(() => { /* they are still in their projects */ });
    // archived=true returns every project, so one request fills both lists.
    fetchProjects(true)
      .then(r => {
        if (cancelled) return;
        setProjects(r.projects.filter(p => !p.archived));
        setArchivedProjects(r.projects.filter(p => p.archived));
        setProjectsState('loaded');
      })
      .catch(() => { if (!cancelled) setProjectsState('error'); });
    return () => { cancelled = true; };
  }, [reloadKey, refreshKey]);

  useEffect(() => {
    const id = refocusChat.current;
    // Still on the ⋮ button: this re-read has not moved the row yet.
    if (!id || (document.activeElement && document.activeElement !== document.body)) return;
    refocusChat.current = null;
    document.querySelector<HTMLElement>(`[data-chat-link][href="${CSS.escape(conversationPath(id))}"]`)?.focus();
  }, [chats, pinnedFiled, projectChats]);

  // Nothing else tells the sidebar that a background turn has ended. Only the
  // expanded project's list is re-read by load(), so a collapsed project's
  // cached list is left out: its stale flag would otherwise poll forever.
  const visibleProjectChats = expandedProject ? projectChats[expandedProject] ?? [] : [];
  const anyGenerating = [...chats, ...pinnedFiled, ...visibleProjectChats].some(c => c.generating);
  useEffect(() => {
    if (!anyGenerating) return;
    const timer = setInterval(load, GENERATING_POLL_MS);
    return () => clearInterval(timer);
  }, [anyGenerating, load]);

  // One-time migration from localStorage starring to backend pinning
  useEffect(() => { migrateStarredToPinned().then(load); }, [load]);

  // The expanded project's chats. Keyed on the project itself, so expanding
  // one always fetches a fresh list: the previous version only fetched when
  // the project had no cached entry, which meant a list could go stale after
  // a chat was filed elsewhere and never be re-read.
  useEffect(() => {
    if (!expandedProject) return;
    const projectId = expandedProject;
    let cancelled = false;
    const wanted = listLengths.current[projectId] ?? 0;
    fetchFirstChats(projectId, wanted)
      .then(r => {
        if (cancelled || (listLengths.current[projectId] ?? 0) > wanted) return;
        listLengths.current[projectId] = r.conversations.length;
        setProjectChats(prev => ({ ...prev, [projectId]: r.conversations }));
        setProjectTotals(prev => ({ ...prev, [projectId]: r.total }));
        setProjectChatsFailed(prev => ({ ...prev, [projectId]: false }));
      })
      .catch(() => {
        if (!cancelled) setProjectChatsFailed(prev => ({ ...prev, [projectId]: true }));
      });
    return () => { cancelled = true; };
  }, [expandedProject, reloadKey, refreshKey]);

  // An open chat that no list holds may belong to a project: a link, a search hit,
  // or the chat just started in a project. Show its folder, since a filed chat
  // appears nowhere else.
  useEffect(() => {
    if (!currentId || currentIsListed || chatsState !== 'loaded') return;
    let cancelled = false;
    fetchChat(currentId)
      .then(chat => {
        const projectId = (chat as Conversation & { project_id?: string | null }).project_id;
        if (cancelled || !projectId) return;
        setCurrentChatProject({ chatId: currentId, projectId });
        setUserExpandedProject(undefined);
      })
      .catch(() => { /* the folder just stays as it is */ });
    return () => { cancelled = true; };
  }, [currentId, currentIsListed, chatsState]);

  useEffect(() => {
    if (!searchOpen) return;
    let cancelled = false;
    fetchChats({ limit: 10 })
      .then(r => { if (!cancelled) setRecentChats(r.conversations); })
      .catch(() => { /* `chats` stands in */ });
    return () => { cancelled = true; };
  }, [searchOpen]);

  // Search when query changes — server-side with FTS5 prefix matching. That
  // matches message text only, so titles of the chats we hold are merged in, or a
  // renamed chat could not be found by its new name.
  // An empty query is not a search: the overlay's "Recent" list is derived at the
  // bottom of this component instead of being copied into state here.
  useEffect(() => {
    if (!searchOpen) return;
    const q = searchQuery.trim();
    if (!q) return;
    let cancelled = false;
    const timer = setTimeout(async () => {
      const lower = q.toLowerCase();
      const held = [...chats, ...pinnedFiled, ...Object.values(projectChats).flat()];
      const titleHits = held.filter(c => (c.title || '').toLowerCase().includes(lower));
      let hits: ConversationSummary[];
      try {
        const ftsQuery = q.endsWith('*') ? q : `${q}*`;
        hits = (await fetchChats({ limit: 20, search: ftsQuery })).conversations;
      } catch {
        // Fallback to client-side filtering
        hits = held.filter(c => (c.preview || '').toLowerCase().includes(lower));
      }
      if (!cancelled) setSearched({ query: q, results: uniqueById([...hits, ...titleHits]) });
    }, 300);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [searchQuery, searchOpen, chats, pinnedFiled, projectChats]);

  const retryLists = () => {
    setChatsState('loading');
    setProjectsState('loading');
    load();
  };

  const showMore = async (listKey: string, offset: number) => {
    setLoadingMore(listKey);
    try {
      const page = await fetchChats({ limit: PAGE_SIZE, offset, project_id: listKey });
      // A refresh may have landed during the wait, so this joins the list as it is
      // now. One may also still be on its way at the old length, or the rows may
      // have moved under this offset, so the list is then read again at its new one.
      listLengths.current[listKey] = offset + page.conversations.length;
      if (listKey === UNFILED) {
        setChats(prev => uniqueById([...prev, ...page.conversations]));
        setChatsTotal(page.total);
      } else {
        setProjectChats(prev => ({ ...prev, [listKey]: uniqueById([...(prev[listKey] ?? []), ...page.conversations]) }));
        setProjectTotals(prev => ({ ...prev, [listKey]: page.total }));
      }
      load();
    } catch {
      showToast('Could not load more chats');
    } finally {
      setLoadingMore(null);
    }
  };

  const handleDelete = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm('Delete this conversation?')) return;
    try {
      await deleteChat(id);
      load();
      if (id === currentId) onNewChat();
    } catch {
      showToast('Could not delete the chat');
    }
  };

  const handleRename = async (id: string) => {
    try {
      if (editTitle.trim()) {
        await renameChat(id, editTitle.trim());
        load();
      }
    } catch {
      showToast('Could not rename the chat');
    } finally {
      setEditingId(null);
    }
  };

  const handleSearchSelect = (id: string) => {
    onSelect(id);
    closeSearch();
  };

  const cancelNewProject = () => {
    setNewProjectName('');
    setShowNewProject(false);
  };

  const handleCreateProject = async () => {
    if (!newProjectName.trim()) return;
    try {
      await createProject({ name: newProjectName.trim() });
      setNewProjectName('');
      setShowNewProject(false);
      load();
    } catch {
      showToast('Could not create the project');
    }
  };

  const handleDeleteProject = async (id: string) => {
    if (!confirm('Delete this project? Chats in this project will stay in your chat list.')) return;
    try {
      await deleteProject(id);
      // Nothing may go on pointing at it: the next message would be filed into a
      // project that no longer exists, and the backend answers that with a 404.
      const workspace = useWorkspaceStore.getState();
      if (workspace.activeProjectId === id) workspace.setActiveProjectId(null);
      if (workspace.editingProject?.id === id) workspace.setEditingProject(null);
      load(); // chats too: they become unfiled
    } catch {
      showToast('Could not delete the project');
    }
  };

  const handleUnarchive = async (id: string) => {
    try {
      await updateProject(id, { archived: false });
      load();
    } catch {
      showToast('Could not unarchive the project');
    }
  };

  const handleMoveToProject = async (chatId: string, projectId: string) => {
    try {
      await fileConversation(projectId, chatId);
      load();
    } catch {
      showToast('Could not move the chat');
    }
  };

  const handleUnfile = async (chatId: string, projectId: string) => {
    try {
      await unfileConversation(projectId, chatId);
      load();
    } catch {
      showToast('Could not remove the chat from the project');
    }
  };

  const togglePin = async (chat: ConversationSummary) => {
    try {
      if (chat.pinned) {
        await unpinChat(chat.id);
      } else {
        await pinChat(chat.id);
      }
      refocusChat.current = chat.id;
      // Focus the user moves on their own cancels the hand-back.
      document.addEventListener('focusin', () => { refocusChat.current = null; }, { once: true });
      load();
    } catch {
      showToast(chat.pinned ? 'Could not unpin the chat' : 'Could not pin the chat');
    }
  };

  const openHelp = () => {
    profileMenu.close();
    onCloseDrawer?.();
    const support = (window as WindowWithSupport).MuninSupport;
    if (support) {
      support.open();
      return;
    }
    showToast('The help window could not load, so the docs opened instead.');
    window.open(DOCS_URL, '_blank', 'noopener');
  };

  // A pinned filed chat can show twice, so its row in the Pinned group has an id of
  // its own for the menus and the rename, which open on one row only.
  const chatRowProps = (chat: ConversationSummary, rowId = chat.id) => ({
    chat,
    isCurrent: chat.id === currentId,
    onSelect,
    onDelete: handleDelete,
    onRename: () => { setEditingId(rowId); setEditTitle(chat.title || ''); },
    onPin: togglePin,
    onRowFocus: () => setRovingRowId(rowId),
    editing: editingId === rowId,
    editTitle,
    onEditTitleChange: setEditTitle,
    onEditComplete: handleRename,
    onEditCancel: () => setEditingId(null),
    onMoveMenu: () => {
      setActionMenuId(null);
      setMoveMenuId(rowId);
    },
    onMoveBack: () => {
      setMoveMenuId(null);
      setActionMenuId(rowId);
    },
    showMoveMenu: moveMenuId === rowId,
    onMenuOpenChange: (open: boolean) => {
      setMoveMenuId(id => (id === rowId ? null : id));
      setActionMenuId(id => (open ? rowId : id === rowId ? null : id));
    },
    showActionMenu: actionMenuId === rowId,
    projects,
    onMoveToProject: (pid: string) => handleMoveToProject(chat.id, pid),
  });

  // The row of a chat list that takes Tab: the one focused last, else the open
  // chat, else the first.
  const tabStopOf = (rows: { rowId: string; chatId: string }[]) =>
    (rows.find(r => r.rowId === rovingRowId) ?? rows.find(r => r.chatId === currentId) ?? rows[0])?.rowId;

  const groups = groupByTime(uniqueById([...chats, ...pinnedFiled]));
  // A pinned filed chat's row in the Pinned group has an id of its own.
  const unfiledRowId = (chat: ConversationSummary) => (chat.project_id ? `pinned:${chat.id}` : chat.id);
  const unfiledTabStop = tabStopOf(groups.flatMap(g => g.chats).map(c => ({ rowId: unfiledRowId(c), chatId: c.id })));
  const query = searchQuery.trim();
  const searching = query !== '' && searched.query !== query;
  // With no query typed the overlay lists recent conversations; with one it
  // lists what the last search returned.
  const searchList = query
    ? (searching ? [] : searched.results)
    : (recentChats.length > 0 ? recentChats : chats.slice(0, 10));
  const searchGroups = groupByTime(searchList);

  const projectChatList = (projectId: string, archived = false) => {
    const list = projectChats[projectId];
    const failed = projectChatsFailed[projectId] && !list;
    const tabStop = tabStopOf((list ?? []).map(c => ({ rowId: c.id, chatId: c.id })));
    const name = [...projects, ...archivedProjects].find(p => p.id === projectId)?.name;
    return (
      <div className="ml-[15px] pl-1 border-l border-border" onKeyDown={moveAmongChats}>
        {list && list.length > 0 && (
          <ul aria-label={name ? `Chats in ${name}` : 'Chats in this project'}>
            {list.map(chat => (
              <ChatRow
                key={chat.id}
                {...chatRowProps(chat)}
                tabbable={chat.id === tabStop}
                onUnfile={() => handleUnfile(chat.id, projectId)}
                isInProject={true}
                currentProjectId={projectId}
              />
            ))}
          </ul>
        )}
        {failed && (
          <div className="text-[11px] text-error py-2 pl-3">
            Could not load these chats.{' '}
            <button type="button" onClick={load} className="underline cursor-pointer">Retry</button>
          </div>
        )}
        {!list && !failed && (
          <div className="text-[11px] text-text-secondary py-2 pl-3">Loading...</div>
        )}
        {list?.length === 0 && (
          <div className="text-xs text-text-secondary py-2 pl-3 pr-2 space-y-1">
            <p>Projects keep chats together and can share instructions.</p>
            {!archived && onNewChatInProject && (
              <a
                href={newChatPath(projectId)}
                onClick={(e) => {
                  if (isModifiedClick(e)) return;
                  e.preventDefault();
                  onNewChatInProject(projectId);
                }}
                className="inline-block text-accent hover:text-accent-hover no-underline"
              >New chat in this project</a>
            )}
          </div>
        )}
        {list && list.length < (projectTotals[projectId] ?? 0) && (
          <ShowMoreRow busy={loadingMore === projectId} onClick={() => showMore(projectId, list.length)} />
        )}
      </div>
    );
  };

  return (
    <aside className="w-64 flex-shrink-0 bg-bg-secondary border-r border-border flex flex-col h-full relative">
      {/* Logo */}
      {/* As tall as the chat header beside it, so their bottom borders meet. */}
      <div className="h-[49px] flex-shrink-0 px-4 flex items-center justify-between border-b border-border">
        <a href={SITE_HOME} className="flex items-center gap-2 no-underline">
          <img
            src="/shared/munin_logo_without_script.webp"
            alt="Munin"
            className={`h-6 ${LOGO_FILTER}`}
          />
          <span className="text-[15px] font-semibold text-text-primary">Munin</span>
        </a>
        {onCloseDrawer && (
          <button
            type="button"
            onClick={onCloseDrawer}
            aria-label="Close menu"
            className="md:hidden min-w-8 min-h-8 flex items-center justify-center -mr-2 text-text-secondary hover:text-text-primary cursor-pointer"
          >
            <span aria-hidden="true">&#10005;</span>
          </button>
        )}
      </div>

      {/* New chat + Search row */}
      <div className="px-2 pt-2 pb-1 flex flex-col gap-0.5">
        {/* New chat */}
        <a
          href={newChatPath()}
          onClick={(e) => {
            // Ctrl/cmd/shift-click and middle-click belong to the
            // browser: it opens "/" in a new tab or window and this
            // one stays where it is. Right-click gets the same two
            // options from the context menu, for free, because this
            // is a real link rather than a button.
            if (isModifiedClick(e)) return;
            e.preventDefault();
            onNewChat();
          }}
          className={`${SIDEBAR_ROW} text-text-primary font-medium hover:bg-bg-tertiary/60`}
        >
          <PlusIcon size={16} className="flex-shrink-0" />
          <span>New chat</span>
        </a>

        {/* Search */}
        <button
          onClick={() => setSearchOpen(true)}
          className={`${SIDEBAR_ROW} ${ROW_IDLE}`}
        >
          <SearchIcon size={16} className="flex-shrink-0" />
          <span>Search chats</span>
        </button>
      </div>

      {/* Projects + Conversation list */}
      <div className="flex-1 overflow-y-auto px-2">
        {projectsState === 'error' && projects.length === 0 && (
          <ErrorNotice message="Could not load your projects" onRetry={retryLists} className="mb-3" />
        )}

        {/* Projects section */}
        {projects.length > 0 && (
          <div className="mb-1">
            <div className={`${SECTION_LABEL} flex items-center`}>
              <span className="flex-1">Projects</span>
              <button
                // Keeps focus in an empty input, whose blur would close it
                // just before this click opened it again.
                onMouseDown={e => e.preventDefault()}
                onClick={() => setShowNewProject(!showNewProject)}
                className="-my-1 -mr-1 p-1 rounded text-text-tertiary hover:text-text-primary hover:bg-bg-tertiary/60 cursor-pointer max-md:p-2.5 max-md:-my-2.5 max-md:-mr-2.5"
                title="New project"
                aria-label="New project"
              ><PlusIcon size={14} /></button>
            </div>

            {/* New project input */}
            {showNewProject && (
              <NewProjectInput value={newProjectName} onChange={setNewProjectName} onCreate={handleCreateProject} onCancel={cancelNewProject} />
            )}

            {projects.map(proj => (
              <div key={proj.id}>
                <ProjectRow
                  project={proj}
                  expanded={expandedProject === proj.id}
                  active={activeProjectId === proj.id}
                  onToggle={() => setUserExpandedProject(expandedProject === proj.id ? null : proj.id)}
                  menuOpen={projectActionMenuId === proj.id}
                  onMenuOpenChange={(open) => setProjectActionMenuId(id => (open ? proj.id : id === proj.id ? null : id))}
                  onNewChatInProject={onNewChatInProject}
                  onOpenProjectSettings={onOpenProjectSettings}
                  onDelete={() => handleDeleteProject(proj.id)}
                />

                {/* Expanded project chats */}
                {expandedProject === proj.id && projectChatList(proj.id)}
              </div>
            ))}
          </div>
        )}

        {/* Create project button (when no projects exist) */}
        {projects.length === 0 && (
          <div className="mb-3">
            {showNewProject ? (
              <NewProjectInput value={newProjectName} onChange={setNewProjectName} onCreate={handleCreateProject} onCancel={cancelNewProject} />
            ) : (
              <button
                onClick={() => setShowNewProject(true)}
                className={`${SIDEBAR_ROW} ${ROW_IDLE}`}
              >
                <FolderIcon size={16} className="flex-shrink-0" />
                New project
              </button>
            )}
          </div>
        )}

        {/* Archived projects: the only way back to one, because project settings
            opens from a sidebar row. */}
        {archivedProjects.length > 0 && (
          <div className="mb-3">
            <button
              type="button"
              aria-expanded={archivedOpen}
              onClick={() => setArchivedOpen(!archivedOpen)}
              className={`${SECTION_LABEL} w-full flex items-center hover:text-text-primary cursor-pointer text-left`}
            >
              <span className="flex-1">Archived ({archivedProjects.length})</span>
              <Chevron open={archivedOpen} />
            </button>
            {archivedOpen && archivedProjects.map(proj => (
              <div key={proj.id}>
                <div data-testid="archived-project" className="flex items-center gap-2 px-2 py-1.5 text-sm text-text-secondary">
                  {onOpenProjectSettings ? (
                    <button
                      type="button"
                      onClick={() => onOpenProjectSettings(proj)}
                      className="flex-1 min-w-0 truncate text-left hover:text-text-primary cursor-pointer"
                      title="Project settings"
                    >{proj.name}</button>
                  ) : (
                    <span className="flex-1 min-w-0 truncate">{proj.name}</span>
                  )}
                  <button
                    type="button"
                    onClick={() => handleUnarchive(proj.id)}
                    aria-label={`Unarchive ${proj.name}`}
                    className="flex-shrink-0 text-xs text-accent hover:text-accent-hover cursor-pointer"
                  >Unarchive</button>
                  <button
                    type="button"
                    onClick={() => setUserExpandedProject(expandedProject === proj.id ? null : proj.id)}
                    aria-expanded={expandedProject === proj.id}
                    aria-label={`Chats in ${proj.name}`}
                    className={`flex-shrink-0 ${ROW_ICON_TARGET} hover:text-text-primary cursor-pointer`}
                  ><Chevron open={expandedProject === proj.id} /></button>
                </div>
                {expandedProject === proj.id && projectChatList(proj.id, true)}
              </div>
            ))}
          </div>
        )}

        {/* Unfiled conversations */}
        {chatsState === 'loading' && chats.length === 0 && (
          <div role="status" aria-label="Loading your chats" className="px-2 py-1 space-y-3" data-testid="chats-loading">
            {[0, 1, 2, 3].map(i => (
              <div key={i} className="h-3 rounded bg-bg-tertiary animate-pulse" />
            ))}
          </div>
        )}
        {chatsState === 'error' && chats.length === 0 && (
          <ErrorNotice message="Could not load your chats" onRetry={retryLists} className="mb-3" />
        )}
        {/* The date groups are one list for the keyboard: one Tab stop, and the
            arrow keys run on from one group into the next. */}
        <div onKeyDown={moveAmongChats}>
          {groups.map(group => {
            const labelId = `${groupIdPrefix}-${group.label.replace(/\s+/g, '-').toLowerCase()}`;
            return (
              <div key={group.label} className="mb-1">
                <div id={labelId} className={SECTION_LABEL}>
                  {group.label}
                </div>
                <ul aria-labelledby={labelId}>
                  {group.chats.map(chat => {
                    const filedIn = chat.project_id;
                    const rowId = unfiledRowId(chat);
                    return (
                      <ChatRow
                        key={chat.id}
                        {...chatRowProps(chat, rowId)}
                        tabbable={rowId === unfiledTabStop}
                        onUnfile={filedIn ? () => handleUnfile(chat.id, filedIn) : undefined}
                        isInProject={!!filedIn}
                        currentProjectId={filedIn ?? undefined}
                      />
                    );
                  })}
                </ul>
              </div>
            );
          })}
        </div>
        {chats.length > 0 && chats.length < chatsTotal && (
          <ShowMoreRow busy={loadingMore === UNFILED} onClick={() => showMore(UNFILED, chats.length)} />
        )}
        {chatsState === 'loaded' && projectsState === 'loaded' && groups.length === 0 && projects.length === 0 && (
          <div className="text-center text-text-secondary text-xs py-8">
            No conversations yet
          </div>
        )}
      </div>

      {/* User profile */}
      {userEmail && (
        <div className="relative border-t border-border px-2 py-2">
          {/* Profile menu popup */}
          {profileMenu.open && (
            <div
              {...profileMenu.menuProps}
              aria-label="Account"
              className="absolute bottom-full left-2 right-2 mb-2 max-h-[70dvh] overflow-y-auto bg-bg-secondary border border-border rounded-xl shadow-lg z-50"
            >
              <button
                role="menuitem"
                onClick={() => { profileMenu.close(); onOpenSettings(); }}
                className={ACCOUNT_ITEM}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-text-secondary">
                  <circle cx="12" cy="12" r="3" />
                  <path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" />
                </svg>
                Settings
              </button>
              {isAdmin && onOpenAdmin && (
                <button
                  role="menuitem"
                  onClick={() => { profileMenu.close(); onOpenAdmin(); }}
                  className={ACCOUNT_ITEM}
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
              <div className="border-t border-border" role="none">
                {/* The header nav only appears from lg while the sidebar is open,
                    so below that these are the way to these pages. */}
                <a
                  role="menuitem"
                  href="/knowledge"
                  onClick={(e) => {
                    profileMenu.close();
                    if (isModifiedClick(e) || !onOpenKnowledge) return;
                    e.preventDefault();
                    onOpenKnowledge();
                  }}
                  className={`lg:hidden ${ACCOUNT_ITEM}`}
                >
                  <BookIcon size={16} className="text-text-secondary" />
                  Knowledge
                </a>
                <a role="menuitem" href={SEARCH_URL} className={`lg:hidden ${ACCOUNT_ITEM}`}>
                  <SearchIcon size={16} className="text-text-secondary" />
                  Search papers
                </a>
                <a role="menuitem" href={UPLOAD_URL} className={`lg:hidden ${ACCOUNT_ITEM}`}>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-text-secondary">
                    <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" />
                    <polyline points="17 8 12 3 7 8" />
                    <line x1="12" y1="3" x2="12" y2="15" />
                  </svg>
                  Upload
                </a>
                <a role="menuitem" href={DOCS_URL} className={ACCOUNT_ITEM}>
                  <PageIcon size={16} className="text-text-secondary" />
                  Docs
                </a>
              </div>
              <button role="menuitem" onClick={openHelp} className={`border-t border-border ${ACCOUNT_ITEM}`}>
                <ChatIcon size={16} className="text-text-secondary" />
                Help
              </button>
              {/* A real form post, not fetch: it navigates the whole page to the
                  auth service, which clears the cookie and answers with its login
                  page. A top-level post involves no CORS, and the Lax session
                  cookie is sent because the chat and auth hosts are same-site. */}
              <form method="post" action={`${AUTH_BASE}/logout`} role="none">
                <button type="submit" role="menuitem" className={ACCOUNT_ITEM}>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-text-secondary">
                    <path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4" />
                    <polyline points="16 17 21 12 16 7" />
                    <line x1="21" y1="12" x2="9" y2="12" />
                  </svg>
                  Sign out
                </button>
              </form>
            </div>
          )}

          <button
            {...profileMenu.triggerProps}
            className="w-full flex items-center gap-2.5 px-2 py-2 rounded-md text-left hover:bg-bg-tertiary/60 transition-colors cursor-pointer"
          >
            <div className="w-7 h-7 rounded-full bg-accent/20 text-accent-hover flex items-center justify-center text-xs font-semibold flex-shrink-0 overflow-hidden">
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
          <div className={`absolute inset-0 ${DIALOG_BACKDROP}`} onClick={() => closeSearch()} />

          {/* Search panel */}
          <div {...searchModal.panelProps} className="relative w-full max-w-xl mx-4 bg-bg-secondary border border-border rounded-xl shadow-2xl overflow-hidden max-h-[60vh] flex flex-col outline-none">
            <h2 id={searchModal.titleId} className="sr-only">Search your chats</h2>
            {/* Search input */}
            <div className="flex items-center gap-3 px-4 py-3 border-b border-border">
              <SearchIcon size={16} className="text-text-secondary flex-shrink-0" />
              <input
                ref={searchInputRef}
                type="text"
                aria-label="Search conversations"
                placeholder="Search conversations..."
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
                className="flex-1 bg-transparent text-sm text-text-primary placeholder-text-secondary outline-none"
              />
              <button
                onClick={() => closeSearch()}
                aria-label="Close search"
                className="text-text-secondary hover:text-text-primary text-sm cursor-pointer"
              >
                {/* A phone has no Esc key to be reminded of. */}
                <span className="md:hidden">Close</span>
                <span className="hidden md:inline">Esc</span>
              </button>
            </div>

            {/* Results */}
            <div className="flex-1 overflow-y-auto">
              {query === '' && (
                <div className="px-4 py-2 text-[11px] font-semibold text-text-secondary uppercase tracking-wider">
                  Recent
                </div>
              )}
              {searching ? (
                <div role="status" className="text-center text-text-secondary text-sm py-8">
                  Searching...
                </div>
              ) : searchGroups.length > 0 ? (
                searchGroups.map(group => (
                  <div key={group.label}>
                    {query !== '' && (
                      <div className="px-4 py-2 text-[11px] font-semibold text-text-secondary uppercase tracking-wider">
                        {group.label}
                      </div>
                    )}
                    {group.chats.map(chat => (
                      <a
                        key={chat.id}
                        href={conversationPath(chat.id)}
                        onClick={(e) => {
                          // Modified click opens the conversation in a
                          // new tab and leaves this overlay up, so a
                          // search can spawn several tabs in one pass.
                          if (isModifiedClick(e)) return;
                          e.preventDefault();
                          handleSearchSelect(chat.id);
                        }}
                        className={`block w-full text-left px-4 py-3 hover:bg-bg-tertiary transition-colors cursor-pointer no-underline ${
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
                      </a>
                    ))}
                  </div>
                ))
              ) : (
                <div className="text-center text-text-secondary text-sm py-8">
                  {query ? 'No matching conversations' : 'No conversations yet'}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </aside>
  );
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={`flex-shrink-0 transition-transform ${open ? 'rotate-180' : ''}`}>
      <polyline points="6 9 12 15 18 9" />
    </svg>
  );
}

function ShowMoreRow({ busy, onClick }: { busy: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      className="w-full text-left px-2 py-1.5 mb-3 text-xs text-text-secondary hover:text-text-primary cursor-pointer disabled:cursor-default"
    >
      {busy ? 'Loading...' : 'Show more'}
    </button>
  );
}

interface ProjectRowProps {
  project: Project;
  expanded: boolean;
  active: boolean;
  onToggle: () => void;
  menuOpen: boolean;
  onMenuOpenChange: (open: boolean) => void;
  onNewChatInProject?: (projectId: string) => void;
  onOpenProjectSettings?: (project: Project) => void;
  onDelete: () => void;
}

function ProjectRow({
  project, expanded, active, onToggle, menuOpen, onMenuOpenChange,
  onNewChatInProject, onOpenProjectSettings, onDelete,
}: ProjectRowProps) {
  const menu = useMenu({ open: menuOpen, onOpenChange: onMenuOpenChange });
  useFixedMenuPosition(menu.open, menu.triggerProps.ref, menu.menuProps.ref);
  const count = project.conversation_count ?? 0;

  return (
    <div className="relative">
      <div
        className={`group flex items-center pr-1 rounded-md text-sm transition-colors ${active ? ROW_CURRENT : ROW_IDLE}`}
      >
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={expanded}
          aria-label={`${project.name}, ${count} ${count === 1 ? 'chat' : 'chats'}`}
          className="flex-1 min-w-0 flex items-center gap-2.5 pl-2 pr-1 h-8 max-md:h-10 text-left cursor-pointer"
        >
          <FolderIcon size={16} className="flex-shrink-0" />
          <span className="flex-1 truncate" title={project.name}>{project.name}</span>
          <span className="text-xs text-text-tertiary tabular-nums">{count}</span>
          <Chevron open={expanded} />
        </button>

        {/* Three-dots project action menu trigger. Fades in on row hover; same
            opacity-only transition as the chat-row kebab so the layout doesn't
            shift. */}
        <button
          {...menu.triggerProps}
          aria-label={`More actions for ${project.name}`}
          className={`flex-shrink-0 p-1 ${ROW_ICON_TARGET} rounded cursor-pointer transition-opacity hover:bg-bg-primary hover:text-text-primary ${
            menu.open
              ? 'opacity-100 text-text-primary'
              : 'opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus-visible:opacity-100 [@media(hover:none)]:opacity-100 text-text-secondary'
          }`}
          title="More"
        >
          <KebabIcon />
        </button>
      </div>

      {/* Project action dropdown. Same shape as the chat kebab menu so they read
          as one consistent affordance. */}
      {menu.open && (
        <div {...menu.menuProps} aria-label={`Actions for ${project.name}`} className={`${MENU_PANEL} w-48`}>
          {onNewChatInProject && (
            <a
              role="menuitem"
              href={newChatPath(project.id)}
              onClick={(e) => {
                // Modified click: let the browser open the link, which carries
                // ?project so the new tab files its first message into this
                // project too. Close the menu either way.
                menu.close();
                if (isModifiedClick(e)) return;
                e.preventDefault();
                onNewChatInProject(project.id);
              }}
              className={MENU_ITEM}
            >
              New chat in project
            </a>
          )}
          {onOpenProjectSettings && (
            <button
              role="menuitem"
              onClick={() => {
                menu.close();
                onOpenProjectSettings(project);
              }}
              className={MENU_ITEM}
            >
              Project settings
            </button>
          )}
          <button
            role="menuitem"
            onClick={() => {
              menu.close();
              onDelete();
            }}
            className={`${MENU_ITEM} !text-error`}
          >
            Delete project
          </button>
        </div>
      )}
    </div>
  );
}

// Saves on Enter or on losing focus, and Escape leaves the title as it was. Only
// the first of these counts, because removing a focused input can also fire blur.
function RenameInput({ initial, value, onChange, onSave, onCancel }: {
  initial: string;
  value: string;
  onChange: (value: string) => void;
  onSave: () => void;
  onCancel: () => void;
}) {
  const finished = useRef(false);
  const finish = (save: boolean) => {
    if (finished.current) return;
    finished.current = true;
    if (save && value.trim() !== initial) onSave();
    else onCancel();
  };
  return (
    <input
      autoFocus
      aria-label="Rename chat"
      value={value}
      onChange={e => onChange(e.target.value)}
      onBlur={() => finish(true)}
      onKeyDown={e => {
        if (e.key === 'Enter') finish(true);
        if (e.key === 'Escape') { e.preventDefault(); finish(false); }
      }}
      onClick={e => e.stopPropagation()}
      className="flex-1 min-w-0 bg-bg-primary border border-accent rounded px-1 py-0.5 text-xs text-text-primary outline-none"
    />
  );
}

interface ChatRowProps {
  chat: ConversationSummary;
  isCurrent: boolean;
  onSelect: (id: string) => void;
  onDelete: (id: string, e: React.MouseEvent) => void;
  onRename: () => void;
  onPin: (chat: ConversationSummary) => void;
  // Whether this row is its list's one Tab stop (see moveAmongChats).
  tabbable: boolean;
  onRowFocus: () => void;
  editing: boolean;
  editTitle: string;
  onEditTitleChange: (v: string) => void;
  onEditComplete: (id: string) => void;
  onEditCancel: () => void;
  onMoveMenu: () => void;
  // Back from the list of projects to the row's actions.
  onMoveBack: () => void;
  showMoveMenu: boolean;
  // Claude-style three-dots action menu. Move to project replaces its items, so
  // only one popover is open at a time per row.
  onMenuOpenChange: (open: boolean) => void;
  showActionMenu: boolean;
  projects: Project[];
  onMoveToProject: (projectId: string) => void;
  onUnfile?: () => void;
  isInProject: boolean;
  // The project the chat is filed in, which Move to leaves out.
  currentProjectId?: string;
}

function ChatRow({
  chat, isCurrent, onSelect, onDelete, onRename, onPin, tabbable, onRowFocus,
  editing, editTitle, onEditTitleChange, onEditComplete, onEditCancel,
  onMoveMenu, onMoveBack, showMoveMenu,
  onMenuOpenChange, showActionMenu,
  projects, onMoveToProject, onUnfile, isInProject, currentProjectId,
}: ChatRowProps) {
  const title = chat.title || 'Untitled';
  const menu = useMenu({ open: showActionMenu || showMoveMenu, onOpenChange: onMenuOpenChange });
  useFixedMenuPosition(menu.open, menu.triggerProps.ref, menu.menuProps.ref, showMoveMenu);
  const moveTargets = projects.filter(p => p.id !== currentProjectId);

  // Switching between the actions and the move list removes the focused item,
  // so focus goes to the first project, or back to Move to project.
  const menuElement = menu.menuProps.ref;
  const wasMoving = useRef(false);
  useEffect(() => {
    if (showMoveMenu) {
      menuElement.current?.querySelector<HTMLElement>('[data-move-target]')?.focus();
    } else if (wasMoving.current && showActionMenu) {
      menuElement.current?.querySelector<HTMLElement>('[data-move-menu]')?.focus();
    }
    wasMoving.current = showMoveMenu;
  }, [showMoveMenu, showActionMenu, menuElement]);

  return (
    <li className="relative">
      <div
        data-testid="chat-row"
        onFocus={onRowFocus}
        onClick={(e) => {
          // Hand modified clicks to the browser so it can open the
          // title link in a new tab or window WITHOUT this tab also
          // switching conversations underneath.
          if (isModifiedClick(e)) return;
          // Cancels the title link's navigation as the click bubbles
          // up from it; a no-op for clicks elsewhere on the row.
          e.preventDefault();
          onSelect(chat.id);
        }}
        className={`group flex items-center gap-2 max-md:gap-0 pl-2 pr-1 h-8 max-md:h-10 rounded-md cursor-pointer text-sm transition-colors ${isCurrent ? ROW_CURRENT : ROW_IDLE}`}
      >
        {/* Background turns: a live stream is generating in this
            conversation (server-derived, refreshes with the list). */}
        {chat.generating && (
          <span
            data-testid="chat-generating"
            className="flex-shrink-0 w-2 h-2 max-md:mr-2 rounded-full bg-accent animate-pulse"
            title="Generating"
          ><span className="sr-only">Generating</span></span>
        )}
        {editing ? (
          <RenameInput
            initial={chat.title || ''}
            value={editTitle}
            onChange={onEditTitleChange}
            onSave={() => onEditComplete(chat.id)}
            onCancel={onEditCancel}
          />
        ) : (
          <a
            href={conversationPath(chat.id)}
            data-chat-link
            tabIndex={tabbable ? 0 : -1}
            aria-current={isCurrent ? 'page' : undefined}
            onKeyDown={(e) => {
              // The ContextMenu key or Shift+F10 opens the row's ⋮ menu, as it
              // would a context menu, so the actions are a key away from the link.
              if (e.key === 'ContextMenu' || (e.shiftKey && e.key === 'F10')) {
                e.preventDefault();
                if (!menu.open) menu.triggerProps.onClick();
              }
            }}
            className="flex-1 min-w-0 truncate no-underline text-inherit"
            title={title}
          >{title}</a>
        )}

        {/* The row's actions take no room until the row is hovered or holds
            focus (or always, on a touch screen), so the title gets the width. */}
        <div className={`flex-shrink-0 items-center gap-0.5 ${menu.open ? 'flex' : 'hidden group-hover:flex group-focus-within:flex [@media(hover:none)]:flex'}`}>
        {/* Three-dots action menu trigger. Fades in on row hover.
            Stays visible when the menu is open so the user can click
            the toggle a second time to dismiss. */}
        <button
          {...menu.triggerProps}
          onClick={(e) => { e.stopPropagation(); menu.triggerProps.onClick(); }}
          // Reached by Tab from its own row's link only; the other rows' buttons
          // stay out of the Tab order, which keeps the list one stop.
          tabIndex={tabbable ? 0 : -1}
          aria-label={`More actions for ${title}`}
          className={`flex-shrink-0 p-1 ${ROW_ICON_TARGET} rounded cursor-pointer hover:bg-bg-primary hover:text-text-primary ${
            menu.open ? 'text-text-primary' : 'text-text-secondary'
          }`}
          title="More"
        >
          <KebabIcon />
        </button>
        </div>
      </div>

      {/* Three-dots action menu (Pin / Rename / Move / Delete), or the list of
          projects after Move to project. */}
      {menu.open && (
        <div {...menu.menuProps} aria-label={`Actions for ${title}`} className={`${MENU_PANEL} w-44`}>
          {showMoveMenu ? (
            <>
              {/* The list replaces the actions in place, so it says what it is
                  and has a way back. */}
              <div role="none" className="flex items-center gap-1 px-1 py-1 border-b border-border">
                <button
                  role="menuitem"
                  onClick={onMoveBack}
                  aria-label="Back"
                  title="Back"
                  className="min-w-6 min-h-6 flex items-center justify-center rounded text-text-secondary hover:text-text-primary hover:bg-bg-tertiary focus-visible:bg-bg-tertiary cursor-pointer"
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <polyline points="15 18 9 12 15 6" />
                  </svg>
                </button>
                <span className="text-[11px] font-semibold text-text-secondary uppercase tracking-wider">Move to</span>
              </div>
              {moveTargets.map(p => (
                <button
                  key={p.id}
                  role="menuitem"
                  data-move-target
                  onClick={() => { menu.close(); onMoveToProject(p.id); }}
                  className={`${MENU_ITEM} truncate`}
                  title={p.name}
                >
                  {p.name}
                </button>
              ))}
            </>
          ) : (
            <>
              <button role="menuitem" onClick={() => { menu.close(); onPin(chat); }} className={MENU_ITEM}>
                {chat.pinned ? 'Unpin' : 'Pin'}
              </button>
              <button role="menuitem" onClick={() => { menu.close(); onRename(); }} className={MENU_ITEM}>
                Rename
              </button>
              {moveTargets.length > 0 && (
                <button
                  role="menuitem"
                  data-move-menu
                  onClick={onMoveMenu}
                  className={`${MENU_ITEM} !flex items-center justify-between`}
                >
                  <span>Move to project</span>
                  <span className="text-text-secondary text-[10px]" aria-hidden="true">&#9656;</span>
                </button>
              )}
              {isInProject && onUnfile && (
                <button role="menuitem" onClick={() => { menu.close(); onUnfile(); }} className={MENU_ITEM}>
                  Remove from project
                </button>
              )}
              <button
                role="menuitem"
                onClick={(e) => { menu.close(); onDelete(chat.id, e); }}
                className={`${MENU_ITEM} !text-error`}
              >
                Delete
              </button>
            </>
          )}
        </div>
      )}
    </li>
  );
}
