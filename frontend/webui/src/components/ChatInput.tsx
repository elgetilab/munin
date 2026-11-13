import { useState, useRef, useEffect, useMemo, forwardRef, useImperativeHandle } from 'react';
import type { ReactNode } from 'react';
import type { Persona, PromptSuggestion, TagCatalog, TagChip } from '../lib/types';
import { uploadDocument, cancelUpload, startDeepResearch } from '../lib/api';
import type { UploadedDocument } from '../lib/api';
import { useWorkspaceStore } from '../stores/workspaceStore';
import { useChatStore } from '../stores/chatStore';
import { useDeepResearchStore } from '../stores/deepResearchStore';
import { useMenu } from '../hooks/useMenu';
import { lastInputWasKeyboard, trackFocusModality } from '../lib/focusModality';
import { ICON } from './ui/cardButton';
import { CHAT_COLUMN } from './ui/chatColumn';
import { ModeChip } from './ModeChip';
import { typedMode, withMode, type Mode } from '../lib/modes';
import { BookIcon, ChatIcon, FlaskIcon, PageIcon, SearchIcon, SpinnerIcon } from './ui/icons';

// Router-recognized per-turn overrides (backend parse_slash: research|code|chat).
// These force Munin's routing for one turn; otherwise it auto-routes.
// Icons are line SVGs in the TaskLog style (14x14, stroke) rather than emoji.
const SLASH_COMMANDS = [
  { command: '/research', description: 'Treat this message as research', icon: <SearchIcon /> },
  { command: '/code', description: 'Treat this message as a coding task', icon: <svg {...ICON}><polyline points="4 17 10 11 4 5" /><line x1="12" y1="19" x2="20" y2="19" /></svg> },
  { command: '/chat', description: 'Treat this message as ordinary chat', icon: <ChatIcon /> },
] as const;

// Display order of the tag menu and the knowledge picker. Results are built in
// this order too, so arrow keys walk the rows from top to bottom.
const TAG_KIND_ORDER = ['group', 'contributor', 'topic'] as const;

// Capped per kind so a long list of groups can't push the others out.
const MAX_RESULTS_PER_KIND = 8;

// A `#` only opens the tag menu at the start of a word, so `C#` stays text.
const TAG_TRIGGER = /(?:^|\s)#([@\w-]*)$/;

interface PendingImage {
  id: string;
  dataUrl: string;
  name: string;
}

interface TagAutocompleteItem {
  kind: 'topic' | 'group' | 'contributor';
  value: string;
  label: string;
  paperCount: number;
}

interface TagSearchResult {
  items: TagAutocompleteItem[];
  total: number;   // matches before the cap
}

function plural(count: number, noun: string): string {
  return `${count.toLocaleString()} ${noun}${count === 1 ? '' : 's'}`;
}

function searchTagCatalog(
  catalog: TagCatalog,
  search: string,
  activeTags: TagChip[],
  contributorsOnly = false,
): TagSearchResult {
  const activeKeys = new Set(activeTags.map(t => `${t.kind}:${t.value}`));
  const candidates: Record<TagAutocompleteItem['kind'], TagAutocompleteItem[]> = {
    group: contributorsOnly ? [] : catalog.groups.map(g => (
      { kind: 'group', value: g.slug, label: g.display_name, paperCount: g.paper_count }
    )),
    contributor: catalog.contributors.map(c => (
      { kind: 'contributor', value: c.username, label: c.display_name, paperCount: c.paper_count }
    )),
    topic: contributorsOnly ? [] : catalog.topics.map(t => (
      { kind: 'topic', value: t.slug, label: t.label, paperCount: t.paper_count }
    )),
  };
  const items: TagAutocompleteItem[] = [];
  let total = 0;
  for (const kind of TAG_KIND_ORDER) {
    const matches = candidates[kind].filter(item =>
      !activeKeys.has(`${item.kind}:${item.value}`)
      && (!search || item.value.includes(search) || item.label.toLowerCase().includes(search)));
    total += matches.length;
    items.push(...matches.slice(0, MAX_RESULTS_PER_KIND));
  }
  return { items, total };
}

// The name a chip shows. Tags stay valid when the catalog hasn't loaded or no
// longer lists them, so this falls back to the slug.
function tagChipLabel(catalog: TagCatalog | null, tag: TagChip): string {
  const fallback = `#${tag.kind === 'contributor' ? '@' : ''}${tag.value}`;
  if (!catalog) return fallback;
  if (tag.kind === 'group') return catalog.groups.find(g => g.slug === tag.value)?.display_name ?? fallback;
  if (tag.kind === 'topic') return catalog.topics.find(t => t.slug === tag.value)?.label ?? fallback;
  return catalog.contributors.find(c => c.username === tag.value)?.display_name ?? fallback;
}

function nextHighlight(current: number, key: string, length: number): number {
  return key === 'ArrowDown' ? (current + 1) % length : (current - 1 + length) % length;
}

// The result list shared by the tag menu and the knowledge picker.
function TagResultList({ result, highlight, onSelect, gutter, empty }: {
  result: TagSearchResult;
  highlight: number;
  onSelect: (item: TagAutocompleteItem) => void;
  gutter: 'px-3' | 'px-4';
  empty: ReactNode;
}) {
  const { items, total } = result;
  const highlightedRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    highlightedRef.current?.scrollIntoView({ block: 'nearest' });
  }, [highlight]);

  return (
    <>
      <div className="max-h-64 overflow-y-auto">
        {TAG_KIND_ORDER.map(kind => {
          const rows = items.filter(m => m.kind === kind);
          if (rows.length === 0) return null;
          return (
            <div key={kind}>
              <div className={`${gutter} py-1.5 text-[10px] uppercase tracking-wider text-text-secondary bg-bg-tertiary font-medium`}>
                {kind === 'group' ? 'Research Groups' : kind === 'contributor' ? 'Contributors' : 'Topics'}
              </div>
              {rows.map(item => {
                const isHighlighted = items.indexOf(item) === highlight;
                return (
                  <button
                    key={`${item.kind}:${item.value}`}
                    ref={isHighlighted ? highlightedRef : undefined}
                    onClick={() => onSelect(item)}
                    className={`w-full flex items-center gap-3 ${gutter} py-2 text-left text-sm transition-colors cursor-pointer ${
                      isHighlighted ? 'bg-bg-tertiary text-text-primary' : 'text-text-secondary hover:bg-bg-tertiary hover:text-text-primary'
                    }`}
                  >
                    <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${
                      item.kind === 'group' ? 'bg-accent/15 text-accent' :
                      item.kind === 'contributor' ? 'bg-success/15 text-success' :
                      'bg-warning/15 text-warning'
                    }`}>
                      {item.kind === 'group' ? 'group' : item.kind === 'contributor' ? 'person' : 'topic'}
                    </span>
                    <span className="flex-1 truncate">{item.label}</span>
                    <span className="text-xs whitespace-nowrap text-text-secondary">
                      {plural(item.paperCount, 'paper')}
                    </span>
                  </button>
                );
              })}
            </div>
          );
        })}
        {items.length === 0 && empty}
      </div>
      {total > items.length && (
        <p className={`${gutter} py-1.5 border-t border-border text-[11px] text-text-secondary`}>
          Showing {items.length} of {total}, keep typing to narrow
        </p>
      )}
    </>
  );
}

// P2 #26 commit 3: store-backed props dropped. The 7 fields below
// used to be passed in by App.tsx purely to forward state ownership
// downstream; they're now sourced directly from userStore /
// workspaceStore inside the component. Removed:
//   personas, selectedPersona, onSelectPersona (userStore)
//   isEphemeral, tagCatalog, activeTags, onTagsChange (workspaceStore)
// `persona: Persona | null` kept because it's a derived value
// (the persona object matching selectedPersona) computed in App.tsx
// from personas + conversationPersona resolution — moving that
// derivation here would duplicate logic.
interface ChatInputProps {
  onSend: (content: string) => void;
  onSendMultimodal?: (content: Array<{ type: string; text?: string; image_url?: { url: string }; document_id?: string; filename?: string }>) => void;
  onStop: () => void;
  isStreaming: boolean;
  researchActive?: boolean;   // a Deep Research job is running for this chat
  persona: Persona | null;
  suggestions?: PromptSuggestion[];
  showSuggestions?: boolean;
  conversationId?: string | null;
  onFileUploaded?: (doc: UploadedDocument) => void;
}

// Imperative handle so a parent-level drop zone (App's whole-chat-window
// drag-and-drop) can route dropped files into the same upload/attach
// pipeline the composer uses, without lifting that state out of here.
export interface ChatInputHandle {
  handleDroppedFiles: (files: FileList | File[]) => void;
  focus: () => void;
}

const ACCEPTED_TYPES = '.pdf,.txt,.md,.docx';
const ACCEPTED_IMAGE_TYPES = '.png,.jpg,.jpeg,.webp';
const MAX_FILE_SIZE = 50 * 1024 * 1024; // 50 MB

interface FileUploadState {
  file: File;
  progress: number;
  status: 'uploading' | 'done' | 'error';
  result?: UploadedDocument;
  error?: string;
}

const MAX_IMAGES = 3;
// Ids for pending images. Date.now() could repeat within one drop, and a
// repeated id would make removing one image remove both.
let imageSeq = 0;
const MAX_IMAGE_SIZE = 5 * 1024 * 1024; // 5 MB

function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(new Error('Failed to read file'));
    reader.readAsDataURL(file);
  });
}

export const ChatInput = forwardRef<ChatInputHandle, ChatInputProps>(function ChatInput({
  onSend,
  onSendMultimodal,
  onStop,
  isStreaming,
  researchActive = false,
  suggestions: _suggestions,
  showSuggestions,
  conversationId,
  onFileUploaded,
}: ChatInputProps, ref) {
  const isEphemeral = useWorkspaceStore(s => s.isEphemeral);
  const tagCatalog = useWorkspaceStore(s => s.tagCatalog);
  const activeTags = useWorkspaceStore(s => s.activeTags);
  const onTagsChange = useWorkspaceStore(s => s.setActiveTags);
  const startDrJob = useDeepResearchStore(s => s.startJob);
  const loadConversation = useChatStore(s => s.loadConversation);
  // A running job blocks starting another and (via researchActive) the composer.
  const drActive = researchActive;
  const drQueued = useDeepResearchStore(s => s.job?.status === 'queued');
  const [drStarting, setDrStarting] = useState(false);
  const [deepResearchMode, setDeepResearchMode] = useState(false);
  // The mode chip's choice. It holds for this chat and goes back to Auto in
  // another one (adjusted during render, React's pattern for state derived
  // from a prop, so the chip never shows the old chat's mode for a frame).
  const [mode, setMode] = useState<Mode>('auto');
  const [modeChat, setModeChat] = useState(conversationId);
  if (modeChat !== conversationId) {
    setModeChat(conversationId);
    if (conversationId == null || modeChat != null) setMode('auto');
  }
  const [input, setInput] = useState('');
  const attachMenu = useMenu();
  const [knowledgePickerOpen, setKnowledgePickerOpen] = useState(false);
  const knowledgePicker = useMenu({ kind: 'popover', open: knowledgePickerOpen, onOpenChange: setKnowledgePickerOpen });
  const [knowledgeSearch, setKnowledgeSearch] = useState('');
  const [knowledgeHighlight, setKnowledgeHighlight] = useState(0);
  const [uploadState, setUploadState] = useState<FileUploadState | null>(null);
  const [slashHighlight, setSlashHighlight] = useState(0);
  const [pendingImages, setPendingImages] = useState<PendingImage[]>([]);
  // Uploaded text documents (.pdf/.txt/.md/.docx) awaiting send. On submit each
  // becomes a { type:'document', document_id } content block so the backend can
  // inline the file's text into the turn. Distinct from pendingImages (images
  // are sent inline as data URLs).
  const [pendingDocs, setPendingDocs] = useState<UploadedDocument[]>([]);
  const [imageError, setImageError] = useState<string | null>(null);
  const [tagHighlight, setTagHighlight] = useState(0);
  // Caret position while it is known; null means the end of the input.
  const [cursor, setCursor] = useState<number | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // Reached from the keyboard: the bubble draws the full ring (index.css).
  const [keyboardFocus, setKeyboardFocus] = useState(false);
  useEffect(trackFocusModality, []);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const uploadAbortRef = useRef<AbortController | null>(null);
  const uploadIdRef = useRef<string | null>(null);

  // Slash command matching
  const slashMatches = useMemo(() => {
    if (!input.startsWith('/')) return [];
    const typed = input.split(' ')[0].toLowerCase();
    // Only show menu while typing the command (no space yet)
    if (input.includes(' ')) return [];
    return SLASH_COMMANDS.filter(c => c.command.startsWith(typed));
  }, [input]);

  // Tag autocomplete: detect `#` followed by typed text
  const tagQuery = useMemo(() => {
    const before = cursor === null ? input : input.slice(0, cursor);
    const match = before.match(TAG_TRIGGER);
    return match ? match[1] : null;
  }, [input, cursor]);

  const tagResult = useMemo((): TagSearchResult => {
    if (tagQuery === null || !tagCatalog) return { items: [], total: 0 };
    const q = tagQuery.toLowerCase();
    const isContributorForced = q.startsWith('@');
    return searchTagCatalog(tagCatalog, isContributorForced ? q.slice(1) : q, activeTags, isContributorForced);
  }, [tagQuery, tagCatalog, activeTags]);
  const tagMatches = tagResult.items;

  // The highlight can outlive a shorter list, for example after a tag is added.
  const activeTagIndex = Math.min(tagHighlight, tagMatches.length - 1);

  // Remove the `#query` before the caret, keeping the space in front of it.
  const removeTagTrigger = () => {
    const caret = textareaRef.current?.selectionStart ?? input.length;
    const cleaned = input.slice(0, caret).replace(/(^|\s)#[@\w-]*$/, '$1');
    setInput(cleaned + input.slice(caret));
    setCursor(cleaned.length);
  };

  const selectTag = (item: TagAutocompleteItem) => {
    onTagsChange([...activeTags, { kind: item.kind, value: item.value }]);
    removeTagTrigger();
    setTagHighlight(0);
    textareaRef.current?.focus();
  };

  const removeTag = (index: number) => {
    onTagsChange(activeTags.filter((_, i) => i !== index));
  };

  // Active command badge (after command is complete with space)
  const activeCommand = useMemo(() => {
    if (!input.includes(' ')) return null;
    const cmd = input.split(' ')[0].toLowerCase();
    return SLASH_COMMANDS.find(c => c.command === cmd) || null;
  }, [input]);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 200) + 'px';
    }
    setSlashHighlight(0);
  }, [input]);

  // Deep Research mode can't survive an incognito chat - disarm it so a stale
  // toggle can't fire. (A brand-new non-incognito chat IS allowed: the backend
  // creates a conversation for the run.)
  useEffect(() => {
    if (deepResearchMode && isEphemeral) {
      setDeepResearchMode(false);
    }
  }, [deepResearchMode, isEphemeral]);

  // Knowledge picker search results
  const knowledgeResult = useMemo((): TagSearchResult => {
    if (!tagCatalog) return { items: [], total: 0 };
    return searchTagCatalog(tagCatalog, knowledgeSearch.toLowerCase().trim(), activeTags);
  }, [tagCatalog, knowledgeSearch, activeTags]);
  const activeKnowledgeIndex = Math.min(knowledgeHighlight, knowledgeResult.items.length - 1);

  const selectKnowledge = (item: TagAutocompleteItem) => {
    onTagsChange([...activeTags, { kind: item.kind, value: item.value }]);
    setKnowledgePickerOpen(false);
    textareaRef.current?.focus();
  };

  const handleKnowledgeSearchKeyDown = (e: React.KeyboardEvent) => {
    const { items } = knowledgeResult;
    if (items.length === 0) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      setKnowledgeHighlight(h => nextHighlight(h, e.key, items.length));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      selectKnowledge(items[activeKnowledgeIndex]);
    }
  };

  const addImage = async (file: File) => {
    setImageError(null);
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) {
      setImageError('Only PNG, JPG, and WEBP images are supported');
      return;
    }
    if (file.size > MAX_IMAGE_SIZE) {
      setImageError('Image must be under 5 MB');
      return;
    }
    if (pendingImages.length >= MAX_IMAGES) {
      setImageError(`Maximum ${MAX_IMAGES} images`);
      return;
    }
    try {
      const dataUrl = await fileToDataUrl(file);
      // The check above reads this render's list, which a drop of several
      // images does not update between files, so the cap is enforced here too.
      setPendingImages(prev => {
        if (prev.length >= MAX_IMAGES) {
          setImageError(`Maximum ${MAX_IMAGES} images`);
          return prev;
        }
        return [...prev, { id: `img-${++imageSeq}`, dataUrl, name: file.name }];
      });
    } catch {
      setImageError('Failed to read image');
    }
  };

  const removeImage = (id: string) => {
    setPendingImages(prev => prev.filter(img => img.id !== id));
    setImageError(null);
  };

  const removeDoc = (documentId: string) => {
    setPendingDocs(prev => prev.filter(d => d.document_id !== documentId));
  };

  // Paste handler for images
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    const handler = (e: ClipboardEvent) => {
      const items = e.clipboardData?.items;
      if (!items) return;
      for (const item of items) {
        if (item.type.startsWith('image/')) {
          e.preventDefault();
          const file = item.getAsFile();
          if (file) addImage(file);
          break;
        }
      }
    };
    el.addEventListener('paste', handler);
    return () => el.removeEventListener('paste', handler);
  });

  const hasAttachments = pendingImages.length > 0 || pendingDocs.length > 0;
  // A research job only takes a question, so it can't carry attachments.
  const deepResearchBlocked = deepResearchMode && hasAttachments;
  // A document only joins the message once its upload has finished.
  const uploading = uploadState?.status === 'uploading';

  const handleSubmit = () => {
    const trimmed = input.trim();
    if ((!trimmed && !hasAttachments) || isStreaming || researchActive || deepResearchBlocked || uploading) return;

    // Deep Research mode: the composer's send starts a background research job
    // instead of a chat turn (Claude-style toggle in the "+" menu).
    if (deepResearchMode && trimmed) {
      handleDeepResearch();
      return;
    }

    if (hasAttachments && onSendMultimodal) {
      const content: Array<{ type: string; text?: string; image_url?: { url: string }; document_id?: string; filename?: string }> = [];
      if (trimmed) {
        content.push({ type: 'text', text: withMode(trimmed, mode) });
      }
      for (const img of pendingImages) {
        content.push({ type: 'image_url', image_url: { url: img.dataUrl } });
      }
      for (const doc of pendingDocs) {
        content.push({ type: 'document', document_id: doc.document_id, filename: doc.filename });
      }
      onSendMultimodal(content);
      setPendingImages([]);
      setPendingDocs([]);
    } else if (trimmed) {
      onSend(withMode(trimmed, mode));
    }
    setInput('');
    setImageError(null);
  };

  // Deep Research: kick off a long-running detached backend job on the typed
  // question. Not a chat turn - it runs in the background (minutes) and delivers
  // a markdown report as an artifact. Works in a brand-new chat (the backend
  // creates the conversation). Only blocked in incognito or while a job runs
  // (backend cap = 1).
  const drResearchUnavailable = isEphemeral || drActive;

  const handleDeepResearch = async () => {
    const question = input.trim();
    if (!question || drActive || drStarting || isEphemeral) return;
    setDrStarting(true);
    try {
      const r = await startDeepResearch(conversationId ?? null, question);
      startDrJob(r.job_id, question, r.conversation_id, Date.now());
      setInput('');
      setDeepResearchMode(false); // one-shot: mode resets after starting a job
      // Started from a fresh chat: switch to the conversation the backend made
      // so progress and the delivered report show up in the right place.
      if (r.created_conversation && r.conversation_id && r.conversation_id !== conversationId) {
        loadConversation(r.conversation_id).catch(() => {});
      }
    } catch (err) {
      setImageError(err instanceof Error ? err.message : 'Failed to start deep research');
    } finally {
      setDrStarting(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (slashMatches.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSlashHighlight(h => (h + 1) % slashMatches.length);
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSlashHighlight(h => (h - 1 + slashMatches.length) % slashMatches.length);
        return;
      }
      if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) {
        e.preventDefault();
        selectSlashCommand(slashMatches[slashHighlight].command);
        return;
      }
      if (e.key === 'Escape') {
        // Kept here, so it closes only this list and not a side panel too.
        e.preventDefault();
        setInput(input + ' ');
        return;
      }
    }
    if (tagMatches.length > 0) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        setTagHighlight(h => nextHighlight(h, e.key, tagMatches.length));
        return;
      }
      if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) {
        e.preventDefault();
        selectTag(tagMatches[activeTagIndex]);
        return;
      }
    }
    // Also with no matches, since the "No matches" panel is still open.
    if (e.key === 'Escape' && tagQuery !== null && tagCatalog) {
      e.preventDefault();
      removeTagTrigger();
      return;
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const selectSlashCommand = (command: string) => {
    setInput(command + ' ');
    setSlashHighlight(0);
    textareaRef.current?.focus();
  };

  const handleFileSelect = async (file: File) => {
    if (file.size > MAX_FILE_SIZE) {
      setUploadState({ file, progress: 0, status: 'error', error: 'File too large (max 50 MB)' });
      return;
    }

    setUploadState({ file, progress: 0, status: 'uploading' });
    const abort = new AbortController();
    uploadAbortRef.current = abort;
    const uploadId = crypto.randomUUID();
    uploadIdRef.current = uploadId;

    try {
      const doc = await uploadDocument(file, conversationId, (pct) => {
        setUploadState(s => s ? { ...s, progress: pct } : null);
      }, abort.signal, uploadId);
      setUploadState({ file, progress: 100, status: 'done', result: doc });
      // Hold the doc as a pending attachment so it's sent with the next message
      // (deduped by document_id in case of a re-upload).
      setPendingDocs(prev =>
        prev.some(d => d.document_id === doc.document_id) ? prev : [...prev, doc]
      );
      onFileUploaded?.(doc);
      // Auto-dismiss the progress chip after 5 seconds (the pending-doc chip stays).
      setTimeout(() => setUploadState(s => s?.status === 'done' ? null : s), 5000);
    } catch (e) {
      if (abort.signal.aborted) return;
      setUploadState({
        file,
        progress: 0,
        status: 'error',
        error: e instanceof Error ? e.message : 'Upload failed',
      });
    }
  };

  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) handleFileSelect(file);
    e.target.value = '';
  };

  const handleImageInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) addImage(file);
    e.target.value = '';
  };

  // Route dropped (or otherwise externally supplied) files into the same
  // image/document pipelines the attach menu uses. Reused by App's
  // whole-window drag-and-drop via the imperative handle below.
  const handleDroppedFiles = async (files: FileList | File[]) => {
    const list = Array.from(files);
    if (list.length === 0) return;
    const ext = (f: File) => f.name.split('.').pop()?.toLowerCase() ?? '';
    const IMAGE_EXTS = ['png', 'jpg', 'jpeg', 'webp'];
    const DOC_EXTS = ['pdf', 'txt', 'md', 'docx'];

    const docs: File[] = [];
    let skipped = 0;
    for (const f of list) {
      if (f.type.startsWith('image/') || IMAGE_EXTS.includes(ext(f))) {
        await addImage(f);  // enforces type/size/count, sets imageError on reject
      } else if (DOC_EXTS.includes(ext(f))) {
        docs.push(f);
      } else {
        skipped++;
      }
    }
    // Documents upload one at a time (the banner tracks a single upload).
    // Document upload is disabled in temporary chats, mirroring the menu.
    for (const f of docs) {
      if (isEphemeral) {
        setUploadState({ file: f, progress: 0, status: 'error', error: 'File upload is disabled in temporary chats' });
        break;
      }
      await handleFileSelect(f);
    }
    if (skipped > 0) {
      setImageError('Some files were skipped. Supported: PDF, TXT, MD, DOCX, PNG, JPG, WEBP');
    }
  };

  useImperativeHandle(ref, () => ({ handleDroppedFiles, focus: () => textareaRef.current?.focus() }));

  return (
    <div className="px-4 py-4 bg-bg-primary w-full">
      {/* Prompt suggestions — hidden for now, pending design review */}
      {/* {showSuggestions && suggestions && suggestions.length > 0 && (
        <div className={`flex flex-wrap gap-2 mb-3 justify-center ${CHAT_COLUMN}`}>
          {suggestions.map((s, i) => (
            <button
              key={i}
              onClick={() => { setInput(s.content); textareaRef.current?.focus(); }}
              className="px-3 py-2 bg-bg-secondary border border-border rounded-lg text-xs text-text-secondary hover:border-accent hover:text-text-primary transition-colors cursor-pointer text-left"
            >
              <span className="font-medium text-text-primary">{s.title}</span>
              <br />
              <span className="text-[11px]">{s.subtitle}</span>
            </button>
          ))}
        </div>
      )} */}

      {/* Upload status banner */}
      {uploadState && (
        <div className={`${CHAT_COLUMN} mb-2`}>
          <div className={`flex items-center gap-3 px-4 py-2.5 rounded-xl text-sm ${
            uploadState.status === 'error'
              ? 'bg-error/10 border border-error text-error'
              : uploadState.status === 'done'
              ? 'bg-accent/10 border border-accent text-accent'
              : 'bg-bg-secondary border border-border text-text-secondary'
          }`}>
            {uploadState.status === 'uploading' && (
              <>
                <svg className="animate-spin h-4 w-4 flex-shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="12" cy="12" r="10" strokeOpacity="0.25" />
                  <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
                </svg>
                <span className="flex-1 truncate">Uploading {uploadState.file.name}... {uploadState.progress}%</span>
              </>
            )}
            {uploadState.status === 'done' && uploadState.result && (
              <>
                <span className="flex-shrink-0">
                  <svg {...ICON}><path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48" /></svg>
                </span>
                <span className="flex-1 truncate">
                  {uploadState.result.filename}{' '}
                  {uploadState.result.status === 'embedded'
                    ? '(ready to send)'
                    : uploadState.result.reason === 'no_text_layer'
                    ? '(attached, searchable shortly)'
                    : '(attached, not searchable)'}
                </span>
              </>
            )}
            {uploadState.status === 'error' && (
              <>
                <span className="flex-1 truncate">{uploadState.error || 'Upload failed'}</span>
              </>
            )}
            <button
              onClick={() => {
                const { file } = uploadState;
                const uploadId = uploadIdRef.current;
                if (uploading) {
                  uploadAbortRef.current?.abort();
                  if (uploadId) {
                    cancelUpload(uploadId).catch(() => setUploadState({
                      file,
                      progress: 0,
                      status: 'error',
                      error: `${file.name} was cancelled here, but the server may have kept it.`,
                    }));
                  }
                }
                setUploadState(null);
              }}
              aria-label={uploading ? 'Cancel upload' : 'Dismiss'}
              title={uploading ? 'Cancel upload' : undefined}
              className="flex-shrink-0 opacity-60 hover:opacity-100 transition-opacity cursor-pointer text-xs"
            >
              <span aria-hidden="true">&#10005;</span>
            </button>
          </div>
        </div>
      )}

      {/* Hidden file inputs */}
      <input
        ref={fileInputRef}
        type="file"
        accept={ACCEPTED_TYPES}
        className="hidden"
        onChange={handleFileInputChange}
      />
      <input
        ref={imageInputRef}
        type="file"
        accept={ACCEPTED_IMAGE_TYPES}
        className="hidden"
        onChange={handleImageInputChange}
      />

      {/* Tag autocomplete dropdown */}
      {tagQuery !== null && tagCatalog && (
        <div className={`${CHAT_COLUMN} mb-2`}>
          <div className="bg-bg-secondary border border-border rounded-xl shadow-lg overflow-hidden">
            {/* Search header */}
            <div className="px-4 py-2 border-b border-border flex items-center gap-2">
              <SearchIcon className="text-text-secondary flex-shrink-0" />
              <span className="text-xs text-text-secondary">
                {tagQuery ? <>Searching: <span className="text-text-primary font-medium">{tagQuery}</span></> : 'Type to search knowledge...'}
              </span>
              <span className="ml-auto text-[10px] text-text-secondary">{plural(tagResult.total, 'result')}</span>
            </div>
            <TagResultList
              result={tagResult}
              highlight={activeTagIndex}
              onSelect={selectTag}
              gutter="px-4"
              empty={(
                <p className="px-4 py-4 text-xs text-text-secondary text-center">
                  No matches for "{tagQuery}"
                </p>
              )}
            />
          </div>
        </div>
      )}

      {/* Slash command autocomplete */}
      {slashMatches.length > 0 && (
        <div className={`${CHAT_COLUMN} mb-2`}>
          <div className="bg-bg-secondary border border-border rounded-xl shadow-lg overflow-hidden">
            {slashMatches.map((cmd, i) => (
              <button
                key={cmd.command}
                onClick={() => selectSlashCommand(cmd.command)}
                className={`w-full flex items-center gap-3 px-4 py-2.5 text-left text-sm transition-colors cursor-pointer ${
                  i === slashHighlight ? 'bg-bg-tertiary text-text-primary' : 'text-text-secondary hover:bg-bg-tertiary'
                }`}
              >
                <span className="shrink-0">{cmd.icon}</span>
                <span className="font-mono font-medium">{cmd.command}</span>
                <span className="text-text-secondary text-xs">{cmd.description}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Input bubble */}
      <div id="composer-bubble" className={`${CHAT_COLUMN} bg-bg-secondary border border-border rounded-2xl`}>
        {/* Active tag chips */}
        {activeTags.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 px-5 pt-3 pb-0">
            {activeTags.map((tag, i) => {
              const label = tagChipLabel(tagCatalog, tag);
              return (
              <span
                key={`${tag.kind}:${tag.value}`}
                title={`#${tag.kind === 'contributor' ? '@' : ''}${tag.value}`}
                className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-lg text-xs font-medium ${
                  tag.kind === 'group' ? 'bg-accent/15 text-accent' :
                  tag.kind === 'contributor' ? 'bg-success/15 text-success' :
                  'bg-warning/15 text-warning'
                }`}
              >
                {label}
                <button
                  onClick={() => removeTag(i)}
                  aria-label={`Remove tag ${label}`}
                  className="ml-0.5 opacity-60 hover:opacity-100 cursor-pointer"
                >
                  <span aria-hidden="true">&#10005;</span>
                </button>
              </span>
              );
            })}
          </div>
        )}

        {/* Active command badge */}
        {activeCommand && (
          <div className="flex items-center gap-2 px-5 pt-3 pb-0">
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-accent/15 text-accent rounded-lg text-xs font-medium">
              <span className="shrink-0">{activeCommand.icon}</span>
              {activeCommand.command.slice(1)}
            </span>
          </div>
        )}

        {/* Pending image thumbnails */}
        {pendingImages.length > 0 && (
          <div className="flex gap-2 px-5 pt-3">
            {pendingImages.map(img => (
              <div key={img.id} className="relative group">
                <img src={img.dataUrl} alt={img.name} className="w-14 h-14 rounded-lg object-cover border border-border" />
                <button
                  onClick={() => removeImage(img.id)}
                  className="absolute -top-1.5 -right-1.5 w-5 h-5 bg-bg-primary border border-border rounded-full text-text-secondary hover:text-error text-xs flex items-center justify-center cursor-pointer opacity-100 [@media(hover:hover)]:opacity-0 [@media(hover:hover)]:group-hover:opacity-100 [@media(hover:hover)]:focus-visible:opacity-100 transition-opacity"
                  aria-label={`Remove image ${img.name}`}
                >
                  <span aria-hidden="true">&#10005;</span>
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Pending document chips */}
        {pendingDocs.length > 0 && (
          <div className="flex flex-wrap gap-2 px-5 pt-3">
            {pendingDocs.map(doc => (
              <div
                key={doc.document_id}
                className="flex items-center gap-2 pl-3 pr-2 py-1.5 rounded-lg bg-bg-secondary border border-border text-xs text-text-primary max-w-[16rem]"
              >
                <span className="truncate">{doc.filename}</span>
                <button
                  onClick={() => removeDoc(doc.document_id)}
                  className="w-4 h-4 shrink-0 text-text-secondary hover:text-error flex items-center justify-center cursor-pointer"
                  title="Remove attachment"
                  aria-label={`Remove attachment ${doc.filename}`}
                >
                  <span aria-hidden="true">&#10005;</span>
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Image error */}
        {imageError && (
          <div className="px-5 pt-2 text-xs text-error">{imageError}</div>
        )}

        {/* Deep Research mode armed (toggle on, no job yet): the next send starts
            a background research job rather than a chat turn. */}
        {deepResearchMode && !drActive && (
          <div data-testid="dr-armed" className={`px-5 pt-2 flex items-center gap-2 text-xs ${deepResearchBlocked ? 'text-warning' : 'text-accent'}`}>
            <FlaskIcon size={13} className="shrink-0" />
            <span className="flex-1 min-w-0">
              {deepResearchBlocked
                ? 'Deep Research cannot use attachments. Remove them or turn Deep Research off.'
                : 'Deep Research is on. Your next message runs as a background report.'}
            </span>
            <button
              onClick={() => setDeepResearchMode(false)}
              className="shrink-0 text-text-secondary hover:text-text-primary cursor-pointer"
              title="Turn off Deep Research"
            >
              Turn off
            </button>
          </div>
        )}

        {/* While a Deep Research job runs, the progress is shown inline in the
            conversation (ResearchTimeline) and the composer is blocked. */}
        {researchActive && (
          <div data-testid="dr-blocked" className="px-5 pt-2 text-xs text-text-secondary">
            {drQueued
              ? 'Deep Research is waiting for another research job to finish. You can send new messages once it is done.'
              : 'Deep Research is running. You can send new messages once it finishes.'}
          </div>
        )}

        {/* Textarea */}
        <textarea
          ref={textareaRef}
          id="composer-textarea"
          data-testid="composer-textarea"
          value={input}
          onChange={e => {
            setInput(e.target.value);
            setCursor(e.target.selectionStart);
            setTagHighlight(0);
          }}
          onSelect={e => {
            const caret = e.currentTarget.selectionStart;
            if (caret === cursor) return;
            setCursor(caret);
            setTagHighlight(0);
          }}
          onKeyDown={handleKeyDown}
          onFocus={() => setKeyboardFocus(lastInputWasKeyboard())}
          onBlur={() => setKeyboardFocus(false)}
          data-keyboard-focus={keyboardFocus || undefined}
          disabled={researchActive}
          aria-label="Message Munin"
          placeholder={researchActive ? undefined : 'Ask Munin…'}
          rows={1}
          className="w-full resize-none bg-transparent px-5 pt-4 pb-3 text-base text-text-primary placeholder-text-secondary outline-none disabled:opacity-50"
        />

        {/* Bottom bar: attach, spacer, Munin mark, send */}
        <div className="flex items-center gap-1 px-3 pb-3">
          {/* Attach button (+) with menu */}
          <div className="relative">
            <button
              {...attachMenu.triggerProps}
              data-testid="composer-attach"
              aria-label="Attach"
              className="p-2 text-text-secondary hover:text-text-primary rounded-lg hover:bg-bg-tertiary transition-colors cursor-pointer"
              title="Attach"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="12" y1="5" x2="12" y2="19" />
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
            </button>

            {attachMenu.open && (
              <div {...attachMenu.menuProps} aria-label="Attach" className="absolute bottom-full left-0 mb-2 w-64 max-w-[calc(100vw-3rem)] bg-bg-secondary border border-border rounded-xl shadow-lg z-50 overflow-hidden">
                <button
                  role="menuitem"
                  onClick={() => { attachMenu.close(); fileInputRef.current?.click(); }}
                  disabled={isEphemeral}
                  className={`w-full flex items-center gap-3 px-3 py-2.5 text-left text-sm transition-colors cursor-pointer ${
                    isEphemeral ? 'text-text-secondary cursor-not-allowed' : 'text-text-primary hover:bg-bg-tertiary'
                  }`}
                >
                  <PageIcon size={16} />
                  <span className="flex-1 min-w-0">
                    <span className="block">Upload file</span>
                    {isEphemeral && <span className="block text-[11px]">Not available in temporary chats</span>}
                  </span>
                </button>
                <button
                  role="menuitem"
                  onClick={() => { attachMenu.close(); imageInputRef.current?.click(); }}
                  className="w-full flex items-center gap-3 px-3 py-2.5 text-left text-sm text-text-primary hover:bg-bg-tertiary transition-colors cursor-pointer"
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
                    <circle cx="8.5" cy="8.5" r="1.5" />
                    <polyline points="21 15 16 10 5 21" />
                  </svg>
                  Upload image
                </button>
                <button
                  role="menuitem"
                  onClick={() => { attachMenu.close(); setKnowledgePickerOpen(true); setKnowledgeSearch(''); setKnowledgeHighlight(0); }}
                  className="w-full flex items-center gap-3 px-3 py-2.5 text-left text-sm text-text-primary hover:bg-bg-tertiary transition-colors cursor-pointer"
                >
                  <BookIcon size={16} />
                  <span className="flex-1">Choose sources</span>
                  <kbd className="text-[11px] font-mono text-text-secondary border border-border rounded px-1.5 py-0.5">#</kbd>
                </button>

                {/* Deep Research toggle. When on, the next send starts a
                    long-running background research job (delivers a report as an
                    artifact) instead of a normal chat turn. Needs a saved chat. */}
                <div className="border-t border-border" />
                <button
                  role="menuitemcheckbox"
                  aria-checked={deepResearchMode}
                  data-testid="attach-deep-research"
                  onClick={() => {
                    if (drResearchUnavailable) return;
                    setDeepResearchMode(m => !m);
                    attachMenu.close();
                    textareaRef.current?.focus();
                  }}
                  disabled={drResearchUnavailable}
                  className={`w-full flex items-center gap-3 px-3 py-2.5 text-left text-sm transition-colors cursor-pointer ${
                    drResearchUnavailable ? 'text-text-secondary cursor-not-allowed' : 'text-text-primary hover:bg-bg-tertiary'
                  }`}
                >
                  <FlaskIcon size={16} />
                  <span className="flex-1 min-w-0">
                    <span className="block">Deep Research</span>
                    <span className="block text-[11px] text-text-secondary">
                      {isEphemeral ? 'Not available in temporary chats'
                        : drActive ? 'A research report is already running'
                        : 'Slower, multi-source report that runs in the background'}
                    </span>
                  </span>
                  {/* on/off pill */}
                  <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${deepResearchMode ? 'bg-accent/15 text-accent' : 'bg-bg-tertiary text-text-secondary'}`}>
                    {deepResearchMode ? 'On' : 'Off'}
                  </span>
                </button>
              </div>
            )}

            {/* Knowledge picker */}
            {knowledgePickerOpen && (
              <div
                {...knowledgePicker.menuProps}
                aria-label="Attach knowledge"
                onKeyDown={e => {
                  if (e.key !== 'Escape') return;
                  // Handled here so that focus goes back to the + button.
                  e.preventDefault();
                  setKnowledgePickerOpen(false);
                  attachMenu.triggerProps.ref.current?.focus();
                }}
                className="absolute bottom-full left-0 mb-2 w-72 max-w-[calc(100vw-3rem)] bg-bg-secondary border border-border rounded-xl shadow-lg z-50 overflow-hidden">
                <div className="px-3 py-2 border-b border-border">
                  <input
                    type="text"
                    value={knowledgeSearch}
                    onChange={e => { setKnowledgeSearch(e.target.value); setKnowledgeHighlight(0); }}
                    onKeyDown={handleKnowledgeSearchKeyDown}
                    aria-label="Search groups, topics, contributors"
                    placeholder="Search groups, topics, contributors..."
                    className="w-full text-xs px-2.5 py-1.5 bg-bg-tertiary border border-border rounded-lg text-text-primary placeholder:text-text-secondary outline-none focus:border-accent transition-colors"
                    autoFocus
                  />
                </div>
                <TagResultList
                  result={knowledgeResult}
                  highlight={activeKnowledgeIndex}
                  onSelect={selectKnowledge}
                  gutter="px-3"
                  empty={(
                    <p className="px-3 py-4 text-xs text-text-secondary text-center">
                      {tagCatalog ? 'No matches' : 'Loading...'}
                    </p>
                  )}
                />
              </div>
            )}
          </div>

          <div className="flex-1" />

          {/* The mode the next message goes in, where Claude has its model
              picker. A typed /command shows here too and wins on send. */}
          <ModeChip mode={mode} onChange={m => { setMode(m); textareaRef.current?.focus(); }} typed={typedMode(input)} deepResearch={deepResearchMode} />

          {/* Send / Stop button */}
          {isStreaming ? (
            <button
              data-testid="composer-stop"
              onClick={onStop}
              aria-label="Stop generating"
              className="p-2.5 bg-bg-tertiary border border-border rounded-full text-text-primary cursor-pointer hover:border-text-secondary transition-colors"
              title="Stop generating"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                <rect x="6" y="6" width="12" height="12" rx="2" />
              </svg>
            </button>
          ) : (
            <button
              data-testid="composer-send"
              onClick={handleSubmit}
              disabled={(!input.trim() && !hasAttachments) || drStarting || researchActive || deepResearchBlocked || uploading}
              className="p-2.5 bg-accent rounded-full text-bg-primary cursor-pointer enabled:hover:bg-accent-hover transition-colors disabled:bg-bg-tertiary disabled:text-text-secondary disabled:cursor-not-allowed"
              title={
                uploading ? 'Waiting for upload to finish'
                : deepResearchBlocked ? 'Deep Research cannot use attachments'
                : deepResearchMode ? 'Start Deep Research'
                : 'Send message'
              }
            >
              {drStarting ? (
                <SpinnerIcon size={16} strokeWidth={2.5} className="animate-spin" />
              ) : deepResearchMode ? (
                /* flask: sending will start a research job */
                <FlaskIcon size={16} strokeWidth={2.5} />
              ) : (
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="12" y1="19" x2="12" y2="5" />
                  <polyline points="5 12 12 5 19 12" />
                </svg>
              )}
            </button>
          )}
        </div>
      </div>

      {/* The empty screen teaches the two shortcuts once, quietly. */}
      {showSuggestions && (
        <p className="text-center text-xs text-text-secondary mt-3" data-testid="composer-hint">
          Type <kbd className="font-mono text-text-primary border border-border rounded px-1 py-px">#</kbd> to choose which sources Munin searches, or <kbd className="font-mono text-text-primary border border-border rounded px-1 py-px">/</kbd> for a mode.
        </p>
      )}
      {/* A little more air under the composer on a phone. */}
      <p className="text-center text-[11px] text-text-secondary mt-3 md:mt-2">
        Munin can make mistakes. Verify important information.
      </p>
    </div>
  );
});
