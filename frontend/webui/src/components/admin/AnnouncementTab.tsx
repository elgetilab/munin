import { useEffect, useId, useState } from 'react';
import { fetchAnnouncement, setAnnouncement, clearAnnouncement } from '../../lib/api';
import { useUiStore } from '../../stores/uiStore';
import { ErrorNotice } from '../ui/ErrorNotice';

type Level = 'info' | 'warning' | 'error';

const LEVELS: { value: Level; label: string }[] = [
  { value: 'info', label: 'Info' },
  { value: 'warning', label: 'Warning' },
  { value: 'error', label: 'Error' },
];

// The same colours as the banner on the chat page (App.tsx).
const BANNER_COLOURS: Record<Level, string> = {
  error: 'bg-error/10 border border-error text-error',
  warning: 'bg-warning/10 border border-warning text-warning',
  info: 'bg-accent/10 border border-accent text-accent',
};

const PRESSED_COLOURS: Record<Level, string> = {
  error: 'bg-error/20 text-error border border-error',
  warning: 'bg-warning/20 text-warning border border-warning',
  info: 'bg-accent/20 text-accent border border-accent',
};

export function AnnouncementTab() {
  const levelId = useId();
  const setBanner = useUiStore(s => s.setAnnouncement);
  const setBannerDismissed = useUiStore(s => s.setAnnouncementDismissed);
  // The banner the app loaded on start. It also tells us an announcement is
  // stored when our own read fails.
  const banner = useUiStore(s => s.announcement);
  const [text, setText] = useState('');
  const [level, setLevel] = useState<Level>('info');
  const [stored, setStored] = useState(false);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    fetchAnnouncement()
      .then(a => {
        if (cancelled || !a) return;
        setText(a.message);
        setLevel(a.level);
        setStored(true);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const message = text.trim();
  const hasStored = stored || banner !== null;
  // Clearing with nothing stored would do nothing.
  const canSave = !saving && (message !== '' || hasStored);

  const save = async () => {
    setSaving(true);
    setStatus('');
    setError('');
    try {
      if (message) {
        await setAnnouncement(message, level);
        setBanner({ message, level, updated_at: new Date().toISOString() });
        setBannerDismissed(false);
        setStored(true);
        setStatus('Announcement set');
      } else {
        await clearAnnouncement();
        setBanner(null);
        setStored(false);
        setStatus('Announcement cleared');
      }
      setTimeout(() => setStatus(''), 3000);
    } catch {
      setError('Could not save the announcement. Please try again.');
    }
    setSaving(false);
  };

  return (
    <div className="space-y-4">
      <p className="text-sm text-text-secondary">
        Set a banner message for all users on the chat page. People who already have Munin open see the change after reloading. Leave empty and save to clear.
      </p>

      <textarea
        value={text}
        onChange={e => setText(e.target.value)}
        placeholder="e.g. Cluster maintenance scheduled for tomorrow 8:00 AM"
        aria-label="Announcement message"
        rows={2}
        className="w-full px-3 py-2 bg-bg-tertiary border border-border rounded-lg text-sm text-text-primary placeholder-text-secondary outline-none focus:border-text-secondary transition-colors resize-none"
      />

      <div role="group" aria-labelledby={levelId} className="flex items-center gap-3">
        <span id={levelId} className="text-sm text-text-secondary">Level:</span>
        {LEVELS.map(l => (
          <button
            key={l.value}
            onClick={() => setLevel(l.value)}
            aria-pressed={level === l.value}
            className={`px-3 py-1 rounded-lg text-xs font-medium cursor-pointer transition-colors ${
              level === l.value
                ? PRESSED_COLOURS[l.value]
                : 'bg-bg-tertiary text-text-secondary border border-border hover:border-text-secondary'
            }`}
          >
            {l.label}
          </button>
        ))}
      </div>

      {message && (
        <div>
          <p className="text-sm text-text-secondary mb-2">Preview</p>
          <div data-testid="announcement-preview" className={`px-4 py-3 rounded-lg text-sm ${BANNER_COLOURS[level]}`}>
            {message}
          </div>
        </div>
      )}

      <div className="flex items-center gap-3">
        <button
          onClick={save}
          disabled={!canSave}
          className="px-5 py-2 bg-accent text-bg-primary rounded-lg text-sm font-medium hover:bg-accent-hover transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {saving ? 'Saving...' : message ? 'Set announcement' : 'Clear announcement'}
        </button>
        <span role="status" className="text-sm text-text-secondary">{status}</span>
      </div>
      {error && <ErrorNotice message={error} />}
    </div>
  );
}
