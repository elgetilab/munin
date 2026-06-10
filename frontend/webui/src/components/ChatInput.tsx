import { useState, useRef, useEffect, useMemo, forwardRef, useImperativeHandle } from 'react';
import type { Persona, PromptSuggestion } from '../lib/types';
import { uploadDocument } from '../lib/api';
import type { UploadedDocument } from '../lib/api';
import { PersonaSelector } from './PersonaSelector';
import { useUserStore } from '../stores/userStore';
import { useWorkspaceStore } from '../stores/workspaceStore';

const SLASH_COMMANDS = [
  { command: '/research', description: 'Deep research on a topic', icon: '\uD83D\uDD2C' },
  { command: '/write', description: 'Write or draft a document', icon: '\uD83D\uDCDD' },
  { command: '/analyze', description: 'Analyze a paper by DOI', icon: '\uD83D\uDCCA' },
] as const;

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
  onSendMultimodal?: (content: Array<{ type: string; text?: string; image_url?: { url: string } }>) => void;
  onStop: () => void;
  isStreaming: boolean;
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
  suggestions: _suggestions,
  showSuggestions: _showSuggestions,
  conversationId,
  onFileUploaded,
}: ChatInputProps, ref) {
  const personas = useUserStore(s => s.personas);
  const selectedPersona = useUserStore(s => s.selectedPersona);
  const onSelectPersona = useUserStore(s => s.setSelectedPersona);
  const isEphemeral = useWorkspaceStore(s => s.isEphemeral);
  const tagCatalog = useWorkspaceStore(s => s.tagCatalog);
  const activeTags = useWorkspaceStore(s => s.activeTags);
  const onTagsChange = useWorkspaceStore(s => s.setActiveTags);
  const [input, setInput] = useState('');
  const [attachMenuOpen, setAttachMenuOpen] = useState(false);
  const [knowledgePickerOpen, setKnowledgePickerOpen] = useState(false);
  const [knowledgeSearch, setKnowledgeSearch] = useState('');
  const [uploadState, setUploadState] = useState<FileUploadState | null>(null);
  const [slashHighlight, setSlashHighlight] = useState(0);
  const [pendingImages, setPendingImages] = useState<PendingImage[]>([]);
  const [imageError, setImageError] = useState<string | null>(null);
  const [tagHighlight, setTagHighlight] = useState(0);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const attachRef = useRef<HTMLDivElement>(null);
  const knowledgeRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);

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
    const cursor = textareaRef.current?.selectionStart ?? input.length;
    const before = input.slice(0, cursor);
    const match = before.match(/#([@\w-]*)$/);
    return match ? match[1] : null;
  }, [input]);

  const tagMatches = useMemo((): TagAutocompleteItem[] => {
    if (tagQuery === null || !tagCatalog) return [];
    const q = tagQuery.toLowerCase();
    const isContributorForced = q.startsWith('@');
    const search = isContributorForced ? q.slice(1) : q;
    const activeKeys = new Set(activeTags.map(t => `${t.kind}:${t.value}`));
    const results: TagAutocompleteItem[] = [];

    if (!isContributorForced) {
      for (const g of tagCatalog.groups) {
        if (activeKeys.has(`group:${g.slug}`)) continue;
        if (!search || g.slug.includes(search) || g.display_name.toLowerCase().includes(search)) {
          results.push({ kind: 'group', value: g.slug, label: g.display_name, paperCount: g.paper_count });
        }
      }
      for (const t of tagCatalog.topics) {
        if (activeKeys.has(`topic:${t.slug}`)) continue;
        if (!search || t.slug.includes(search) || t.label.toLowerCase().includes(search)) {
          results.push({ kind: 'topic', value: t.slug, label: t.label, paperCount: t.paper_count });
        }
      }
    }
    for (const c of tagCatalog.contributors) {
      if (activeKeys.has(`contributor:${c.username}`)) continue;
      if (!search || c.username.includes(search) || c.display_name.toLowerCase().includes(search)) {
        results.push({ kind: 'contributor', value: c.username, label: c.display_name, paperCount: c.paper_count });
      }
    }
    return results.slice(0, 12);
  }, [tagQuery, tagCatalog, activeTags]);

  const selectTag = (item: TagAutocompleteItem) => {
    onTagsChange([...activeTags, { kind: item.kind, value: item.value }]);
    // Remove the #query from input
    const cursor = textareaRef.current?.selectionStart ?? input.length;
    const before = input.slice(0, cursor);
    const after = input.slice(cursor);
    const cleaned = before.replace(/#[@\w-]*$/, '');
    setInput(cleaned + after);
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

  // Close attach menu / knowledge picker on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (attachRef.current && !attachRef.current.contains(e.target as Node)) {
        setAttachMenuOpen(false);
        setKnowledgePickerOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  // Knowledge picker search results
  const knowledgeResults = useMemo((): TagAutocompleteItem[] => {
    if (!tagCatalog) return [];
    const q = knowledgeSearch.toLowerCase().trim();
    const activeKeys = new Set(activeTags.map(t => `${t.kind}:${t.value}`));
    const results: TagAutocompleteItem[] = [];

    for (const g of tagCatalog.groups) {
      if (activeKeys.has(`group:${g.slug}`)) continue;
      if (!q || g.slug.includes(q) || g.display_name.toLowerCase().includes(q)) {
        results.push({ kind: 'group', value: g.slug, label: g.display_name, paperCount: g.paper_count });
      }
    }
    for (const t of tagCatalog.topics) {
      if (activeKeys.has(`topic:${t.slug}`)) continue;
      if (!q || t.slug.includes(q) || t.label.toLowerCase().includes(q)) {
        results.push({ kind: 'topic', value: t.slug, label: t.label, paperCount: t.paper_count });
      }
    }
    for (const c of tagCatalog.contributors) {
      if (activeKeys.has(`contributor:${c.username}`)) continue;
      if (!q || c.username.includes(q) || c.display_name.toLowerCase().includes(q)) {
        results.push({ kind: 'contributor', value: c.username, label: c.display_name, paperCount: c.paper_count });
      }
    }
    return results.slice(0, 20);
  }, [tagCatalog, knowledgeSearch, activeTags]);

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
      setPendingImages(prev => [...prev, { id: `img-${Date.now()}`, dataUrl, name: file.name }]);
    } catch {
      setImageError('Failed to read image');
    }
  };

  const removeImage = (id: string) => {
    setPendingImages(prev => prev.filter(img => img.id !== id));
    setImageError(null);
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

  const handleSubmit = () => {
    const trimmed = input.trim();
    if ((!trimmed && pendingImages.length === 0) || isStreaming) return;

    if (pendingImages.length > 0 && onSendMultimodal) {
      const content: Array<{ type: string; text?: string; image_url?: { url: string } }> = [];
      if (trimmed) {
        content.push({ type: 'text', text: trimmed });
      }
      for (const img of pendingImages) {
        content.push({ type: 'image_url', image_url: { url: img.dataUrl } });
      }
      onSendMultimodal(content);
      setPendingImages([]);
    } else if (trimmed) {
      onSend(trimmed);
    }
    setInput('');
    setImageError(null);
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
        setInput(input + ' ');
        return;
      }
    }
    if (tagMatches.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setTagHighlight(h => (h + 1) % tagMatches.length);
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setTagHighlight(h => (h - 1 + tagMatches.length) % tagMatches.length);
        return;
      }
      if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) {
        e.preventDefault();
        selectTag(tagMatches[tagHighlight]);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        // Remove the # trigger
        const cursor = textareaRef.current?.selectionStart ?? input.length;
        const before = input.slice(0, cursor);
        const after = input.slice(cursor);
        setInput(before.replace(/#[@\w-]*$/, '') + after);
        return;
      }
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

    try {
      const doc = await uploadDocument(file, conversationId, (pct) => {
        setUploadState(s => s ? { ...s, progress: pct } : null);
      });
      setUploadState({ file, progress: 100, status: 'done', result: doc });
      onFileUploaded?.(doc);
      // Auto-dismiss after 5 seconds
      setTimeout(() => setUploadState(s => s?.status === 'done' ? null : s), 5000);
    } catch (e) {
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
    // Document upload is disabled in ephemeral chats, mirroring the menu.
    for (const f of docs) {
      if (isEphemeral) {
        setUploadState({ file: f, progress: 0, status: 'error', error: 'File upload is disabled in ephemeral chats' });
        break;
      }
      await handleFileSelect(f);
    }
    if (skipped > 0) {
      setImageError('Some files were skipped. Supported: PDF, TXT, MD, DOCX, PNG, JPG, WEBP');
    }
  };

  useImperativeHandle(ref, () => ({ handleDroppedFiles }));

  return (
    <div className="px-4 py-4 bg-bg-primary w-full">
      {/* Prompt suggestions — hidden for now, pending design review */}
      {/* {showSuggestions && suggestions && suggestions.length > 0 && (
        <div className="flex flex-wrap gap-2 mb-3 justify-center max-w-3xl mx-auto">
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
        <div className="max-w-3xl mx-auto mb-2">
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
                <span className="flex-shrink-0">&#128206;</span>
                <span className="flex-1 truncate">
                  {uploadState.result.filename}{' '}
                  {uploadState.result.status === 'embedded'
                    ? `(${uploadState.result.chunks} chunks embedded)`
                    : '(uploaded, not indexed)'}
                </span>
              </>
            )}
            {uploadState.status === 'error' && (
              <>
                <span className="flex-1 truncate">{uploadState.error || 'Upload failed'}</span>
              </>
            )}
            <button
              onClick={() => setUploadState(null)}
              className="flex-shrink-0 opacity-60 hover:opacity-100 transition-opacity cursor-pointer text-xs"
            >
              &#10005;
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
        <div className="max-w-3xl mx-auto mb-2">
          <div className="bg-bg-secondary border border-border rounded-xl shadow-lg overflow-hidden">
            {/* Search header */}
            <div className="px-4 py-2 border-b border-border flex items-center gap-2">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-text-secondary flex-shrink-0">
                <circle cx="11" cy="11" r="8" />
                <line x1="21" y1="21" x2="16.65" y2="16.65" />
              </svg>
              <span className="text-xs text-text-secondary">
                {tagQuery ? <>Searching: <span className="text-text-primary font-medium">{tagQuery}</span></> : 'Type to search knowledge...'}
              </span>
              <span className="ml-auto text-[10px] text-text-secondary">{tagMatches.length} results</span>
            </div>
            <div className="max-h-64 overflow-y-auto">
              {/* Group results by kind */}
              {(['group', 'contributor', 'topic'] as const).map(kind => {
                const items = tagMatches.filter(m => m.kind === kind);
                if (items.length === 0) return null;
                return (
                  <div key={kind}>
                    <div className="px-4 py-1.5 text-[10px] uppercase tracking-wider text-text-secondary bg-bg-tertiary font-medium">
                      {kind === 'group' ? 'Research Groups' : kind === 'contributor' ? 'Contributors' : 'Topics'}
                    </div>
                    {items.map(item => {
                      const globalIdx = tagMatches.indexOf(item);
                      return (
                        <button
                          key={`${item.kind}:${item.value}`}
                          onClick={() => selectTag(item)}
                          className={`w-full flex items-center gap-3 px-4 py-2 text-left text-sm transition-colors cursor-pointer ${
                            globalIdx === tagHighlight ? 'bg-bg-tertiary text-text-primary' : 'text-text-secondary hover:bg-bg-tertiary'
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
                          <span className={`text-xs ${item.paperCount === 0 ? 'text-text-secondary/50' : 'text-text-secondary'}`}>
                            {item.paperCount.toLocaleString()} papers
                          </span>
                        </button>
                      );
                    })}
                  </div>
                );
              })}
              {tagMatches.length === 0 && (
                <p className="px-4 py-4 text-xs text-text-secondary text-center">
                  No matches for "{tagQuery}"
                </p>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Slash command autocomplete */}
      {slashMatches.length > 0 && (
        <div className="max-w-3xl mx-auto mb-2">
          <div className="bg-bg-secondary border border-border rounded-xl shadow-lg overflow-hidden">
            {slashMatches.map((cmd, i) => (
              <button
                key={cmd.command}
                onClick={() => selectSlashCommand(cmd.command)}
                className={`w-full flex items-center gap-3 px-4 py-2.5 text-left text-sm transition-colors cursor-pointer ${
                  i === slashHighlight ? 'bg-bg-tertiary text-text-primary' : 'text-text-secondary hover:bg-bg-tertiary'
                }`}
              >
                <span>{cmd.icon}</span>
                <span className="font-mono font-medium">{cmd.command}</span>
                <span className="text-text-secondary text-xs">{cmd.description}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Input bubble */}
      <div className="max-w-3xl mx-auto bg-bg-secondary border border-border rounded-2xl focus-within:border-accent transition-colors">
        {/* Active tag chips */}
        {activeTags.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 px-5 pt-3 pb-0">
            {activeTags.map((tag, i) => (
              <span
                key={`${tag.kind}:${tag.value}`}
                className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-lg text-xs font-medium ${
                  tag.kind === 'group' ? 'bg-accent/15 text-accent' :
                  tag.kind === 'contributor' ? 'bg-success/15 text-success' :
                  'bg-warning/15 text-warning'
                }`}
              >
                #{tag.kind === 'contributor' ? '@' : ''}{tag.value}
                <button
                  onClick={() => removeTag(i)}
                  className="ml-0.5 opacity-60 hover:opacity-100 cursor-pointer"
                >
                  &#10005;
                </button>
              </span>
            ))}
          </div>
        )}

        {/* Active command badge */}
        {activeCommand && (
          <div className="flex items-center gap-2 px-5 pt-3 pb-0">
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-accent/15 text-accent rounded-lg text-xs font-medium">
              <span>{activeCommand.icon}</span>
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
                  className="absolute -top-1.5 -right-1.5 w-5 h-5 bg-bg-primary border border-border rounded-full text-text-secondary hover:text-error text-xs flex items-center justify-center cursor-pointer opacity-0 group-hover:opacity-100 transition-opacity"
                >
                  &#10005;
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Image error */}
        {imageError && (
          <div className="px-5 pt-2 text-xs text-error">{imageError}</div>
        )}

        {/* Textarea */}
        <textarea
          ref={textareaRef}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Send a message..."
          rows={1}
          className="w-full resize-none bg-transparent px-5 pt-4 pb-2 text-sm text-text-primary placeholder-text-secondary outline-none"
        />

        {/* Bottom bar: attach, spacer, persona selector, send */}
        <div className="flex items-center gap-1 px-3 pb-3">
          {/* Attach button (+) with menu */}
          <div ref={attachRef} className="relative">
            <button
              onClick={() => setAttachMenuOpen(!attachMenuOpen)}
              className="p-2 text-text-secondary hover:text-text-primary rounded-lg hover:bg-bg-tertiary transition-colors cursor-pointer"
              title="Attach"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="12" y1="5" x2="12" y2="19" />
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
            </button>

            {attachMenuOpen && (
              <div className="absolute bottom-full left-0 mb-2 w-48 bg-bg-secondary border border-border rounded-xl shadow-lg z-50 overflow-hidden">
                <button
                  onClick={() => { setAttachMenuOpen(false); fileInputRef.current?.click(); }}
                  disabled={isEphemeral}
                  className={`w-full flex items-center gap-3 px-3 py-2.5 text-left text-sm transition-colors cursor-pointer ${
                    isEphemeral ? 'text-text-secondary opacity-50 cursor-not-allowed' : 'text-text-primary hover:bg-bg-tertiary'
                  }`}
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
                    <polyline points="14 2 14 8 20 8" />
                  </svg>
                  Upload file{isEphemeral ? ' (disabled)' : ''}
                </button>
                <button
                  onClick={() => { setAttachMenuOpen(false); imageInputRef.current?.click(); }}
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
                  onClick={() => { setAttachMenuOpen(false); setKnowledgePickerOpen(true); setKnowledgeSearch(''); }}
                  className="w-full flex items-center gap-3 px-3 py-2.5 text-left text-sm text-text-primary hover:bg-bg-tertiary transition-colors cursor-pointer"
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M2 3h6a4 4 0 014 4v14a3 3 0 00-3-3H2z" />
                    <path d="M22 3h-6a4 4 0 00-4 4v14a3 3 0 013-3h7z" />
                  </svg>
                  Attach knowledge
                </button>
              </div>
            )}

            {/* Knowledge picker */}
            {knowledgePickerOpen && (
              <div ref={knowledgeRef} className="absolute bottom-full left-0 mb-2 w-72 bg-bg-secondary border border-border rounded-xl shadow-lg z-50 overflow-hidden">
                <div className="px-3 py-2 border-b border-border">
                  <input
                    type="text"
                    value={knowledgeSearch}
                    onChange={e => setKnowledgeSearch(e.target.value)}
                    placeholder="Search groups, topics, contributors..."
                    className="w-full text-xs px-2.5 py-1.5 bg-bg-tertiary border border-border rounded-lg text-text-primary placeholder:text-text-secondary outline-none focus:border-accent transition-colors"
                    autoFocus
                  />
                </div>
                <div className="max-h-64 overflow-y-auto">
                  {(['group', 'contributor', 'topic'] as const).map(kind => {
                    const items = knowledgeResults.filter(m => m.kind === kind);
                    if (items.length === 0) return null;
                    return (
                      <div key={kind}>
                        <div className="px-3 py-1.5 text-[10px] uppercase tracking-wider text-text-secondary bg-bg-tertiary font-medium">
                          {kind === 'group' ? 'Research Groups' : kind === 'contributor' ? 'Contributors' : 'Topics'}
                        </div>
                        {items.map(item => (
                          <button
                            key={`${item.kind}:${item.value}`}
                            onClick={() => {
                              onTagsChange([...activeTags, { kind: item.kind, value: item.value }]);
                              setKnowledgePickerOpen(false);
                              textareaRef.current?.focus();
                            }}
                            className="w-full flex items-center gap-2 px-3 py-2 text-left text-sm text-text-secondary hover:bg-bg-tertiary hover:text-text-primary transition-colors cursor-pointer"
                          >
                            <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${
                              item.kind === 'group' ? 'bg-accent/15 text-accent' :
                              item.kind === 'contributor' ? 'bg-success/15 text-success' :
                              'bg-warning/15 text-warning'
                            }`}>
                              {item.kind === 'group' ? 'group' : item.kind === 'contributor' ? 'person' : 'topic'}
                            </span>
                            <span className="flex-1 truncate">{item.label}</span>
                            <span className="text-[10px] tabular-nums text-text-secondary">{item.paperCount.toLocaleString()}</span>
                          </button>
                        ))}
                      </div>
                    );
                  })}
                  {knowledgeResults.length === 0 && (
                    <p className="px-3 py-4 text-xs text-text-secondary text-center">
                      {tagCatalog ? 'No matches' : 'Loading...'}
                    </p>
                  )}
                </div>
              </div>
            )}
          </div>

          <div className="flex-1" />

          {/* Persona selector — right side */}
          {personas.length > 0 && (
            <PersonaSelector
              personas={personas}
              selected={selectedPersona}
              onSelect={onSelectPersona}
            />
          )}

          {/* Send / Stop button */}
          {isStreaming ? (
            <button
              onClick={onStop}
              className="p-2.5 bg-error rounded-full text-bg-primary cursor-pointer hover:opacity-90 transition-opacity"
              title="Stop generating"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                <rect x="6" y="6" width="12" height="12" rx="2" />
              </svg>
            </button>
          ) : (
            <button
              onClick={handleSubmit}
              disabled={!input.trim() && pendingImages.length === 0}
              className="p-2.5 bg-accent rounded-full text-bg-primary cursor-pointer hover:bg-accent-hover transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
              title="Send message"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <line x1="12" y1="19" x2="12" y2="5" />
                <polyline points="5 12 12 5 19 12" />
              </svg>
            </button>
          )}
        </div>
      </div>

      <p className="text-center text-[11px] text-text-secondary mt-2">
        Munin can make mistakes. Verify important information.
      </p>
    </div>
  );
});
