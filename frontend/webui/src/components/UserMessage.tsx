import { useEffect, useId, useRef, useState } from 'react';
import { PencilIcon } from './ui/icons';
import { lastInputWasKeyboard } from '../lib/focusModality';
import { MODES, splitMode } from '../lib/modes';
import { ModeIcon } from './ModeChip';

/**
 * A user's message, with an Edit pencil when it can be sent again in place.
 * Editing opens the text in a box under the same bubble shape; sending it
 * replaces this message and everything after it (replace_from), so the box
 * says so when there is anything after the reply.
 */
export function UserMessage({ content, onEdit, dropsLaterTurns }: {
  content: string;
  /** Present when the message can be edited: sends the new text in its place. */
  onEdit?: (text: string) => void;
  /** Whether sending would also drop turns after this message's reply. */
  dropsLaterTurns: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(content);
  // Ring for keyboard focus only, as in the composer (index.css .edit-bubble).
  const [keyboardFocus, setKeyboardFocus] = useState(false);
  const boxRef = useRef<HTMLTextAreaElement>(null);
  const pencilRef = useRef<HTMLButtonElement>(null);
  const noteId = useId();

  useEffect(() => {
    if (!editing) return;
    const box = boxRef.current;
    if (!box) return;
    box.focus();
    box.setSelectionRange(box.value.length, box.value.length);
    box.style.height = 'auto';
    box.style.height = `${Math.min(box.scrollHeight, 320)}px`;
  }, [editing]);

  const cancel = () => {
    setEditing(false);
    setText(content);
    // Give focus back to the pencil, where the edit began.
    requestAnimationFrame(() => pencilRef.current?.focus());
  };
  const send = () => {
    const next = text.trim();
    if (!next || !onEdit) return;
    setEditing(false);
    onEdit(next);
  };

  if (editing) {
    return (
      <div className="flex justify-end">
        <div className="edit-bubble w-full max-w-[80%] bg-bg-tertiary border border-border rounded-2xl px-4 py-3 space-y-2">
          <textarea
            ref={boxRef}
            value={text}
            aria-label="Edit your message"
            aria-describedby={dropsLaterTurns ? noteId : undefined}
            onFocus={() => setKeyboardFocus(lastInputWasKeyboard())}
            onBlur={() => setKeyboardFocus(false)}
            data-keyboard-focus={keyboardFocus || undefined}
            onChange={e => {
              setText(e.target.value);
              e.target.style.height = 'auto';
              e.target.style.height = `${Math.min(e.target.scrollHeight, 320)}px`;
            }}
            onKeyDown={e => {
              if (e.key === 'Escape') { e.preventDefault(); cancel(); }
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); }
            }}
            rows={1}
            className="w-full resize-none bg-transparent text-base text-text-primary leading-relaxed outline-none"
          />
          {dropsLaterTurns && (
            <p id={noteId} className="text-xs text-text-secondary">Sending replaces this message and everything after it.</p>
          )}
          <div className="flex justify-end gap-2">
            <button type="button" onClick={cancel} className="px-3 py-1.5 text-sm text-text-secondary hover:text-text-primary rounded-lg cursor-pointer">Cancel</button>
            <button
              type="button"
              onClick={send}
              disabled={!text.trim() || text.trim() === content.trim()}
              className="px-3 py-1.5 text-sm font-medium rounded-lg bg-accent text-bg-primary enabled:hover:bg-accent-hover disabled:bg-bg-secondary disabled:text-text-secondary disabled:cursor-not-allowed cursor-pointer"
            >
              Send
            </button>
          </div>
        </div>
      </div>
    );
  }

  // A leading /research, /code or /chat (typed, or added by the mode chip)
  // shows as a small label over the bubble instead of in the text.
  const shown = splitMode(content);
  const forced = shown.mode ? MODES.find(m => m.id === shown.mode) : null;

  return (
    // The pencil sits beside the bubble, so it takes no height of its own.
    <div className="group/user flex flex-col items-end gap-1">
      {forced && (
        <span className="inline-flex items-center gap-1 pr-1 text-xs text-text-secondary select-none" data-testid="sent-mode">
          <ModeIcon mode={forced.id} size={12} />
          {forced.label}
        </span>
      )}
    <div className="flex justify-end items-end gap-1 w-full">
      {onEdit && (
        <button
          ref={pencilRef}
          type="button"
          onClick={() => { setText(content); setEditing(true); }}
          aria-label="Edit message"
          title="Edit"
          className="inline-flex items-center justify-center w-8 h-8 rounded-md text-text-secondary hover:text-text-primary hover:bg-bg-tertiary transition-colors cursor-pointer flex-shrink-0 opacity-0 group-hover/user:opacity-100 focus-visible:opacity-100 [@media(hover:none)]:opacity-100"
        >
          <PencilIcon size={15} />
        </button>
      )}
      <div className="max-w-[80%] bg-bg-tertiary border border-transparent rounded-2xl px-4 py-3 text-base text-text-primary leading-relaxed whitespace-pre-wrap wrap-anywhere">
        {shown.text}
      </div>
    </div>
    </div>
  );
}
