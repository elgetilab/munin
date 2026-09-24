import { useState, useRef, useEffect, useCallback } from 'react';
import { reportChat } from '../lib/api';
import { useUiStore } from '../stores/uiStore';

interface ReportDialogProps {
  conversationId: string;
  // P2 #26 commit 2: onClose dropped; pulled from uiStore directly.
  onReported: () => void;
}

const MAX_REASON = 2000;

export function ReportDialog({ conversationId, onReported }: ReportDialogProps) {
  const setShowReportDialog = useUiStore(s => s.setShowReportDialog);
  // Memoised because the two document-listener effects below depend on it:
  // a fresh identity every render had them removing and re-adding the
  // mousedown and keydown handlers on each keystroke in the textarea. The
  // store action is stable, so this is too.
  const onClose = useCallback(() => setShowReportDialog(false), [setShowReportDialog]);
  const [reason, setReason] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setTimeout(() => textareaRef.current?.focus(), 50);
  }, []);

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [onClose]);

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  const handleSubmit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      await reportChat(conversationId, reason.trim() || undefined);
      onReported();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to send report');
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-bg-primary/70 backdrop-blur-sm" />

      {/* Dialog */}
      <div ref={panelRef} className="relative w-full max-w-md bg-bg-secondary border border-border rounded-xl shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center gap-2 px-5 py-4 border-b border-border">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-warning flex-shrink-0">
            <path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z" />
            <line x1="4" y1="22" x2="4" y2="15" />
          </svg>
          <h3 className="text-sm font-semibold text-text-primary">Report this chat</h3>
        </div>

        {/* Body */}
        <div className="px-5 py-4 space-y-3">
          <label className="text-xs text-text-secondary">
            What went wrong? (optional)
          </label>
          <textarea
            ref={textareaRef}
            value={reason}
            onChange={e => setReason(e.target.value.slice(0, MAX_REASON))}
            placeholder="Describe the issue..."
            rows={4}
            className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-sm text-text-primary placeholder-text-secondary outline-none resize-none focus:border-accent"
          />
          <div className="flex items-center justify-between">
            <span className={`text-[11px] ${reason.length >= MAX_REASON ? 'text-error' : 'text-text-secondary'}`}>
              {reason.length}/{MAX_REASON}
            </span>
            {error && <span className="text-[11px] text-error">{error}</span>}
          </div>
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 px-5 py-3 border-t border-border">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-xs text-text-secondary hover:text-text-primary transition-colors cursor-pointer"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={submitting}
            className="px-4 py-1.5 bg-warning/15 border border-warning text-warning rounded-lg text-xs font-medium hover:bg-warning/25 transition-colors cursor-pointer disabled:opacity-50"
          >
            {submitting ? 'Sending...' : 'Send report'}
          </button>
        </div>
      </div>
    </div>
  );
}
