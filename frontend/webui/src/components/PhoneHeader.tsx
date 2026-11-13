import { useMenu } from '../hooks/useMenu';
import { useTheme } from '../lib/theme';
import { BookIcon, FlagIcon, GhostIcon, LayersIcon, MoonIcon, PencilIcon, SunIcon } from './ui/icons';

const ITEM = 'w-full flex items-center gap-3 px-3 py-2.5 text-left text-sm text-text-primary hover:bg-bg-tertiary focus-visible:bg-bg-tertiary cursor-pointer';
const BUTTON = 'inline-flex flex-shrink-0 items-center justify-center w-11 h-11 -my-1.5 rounded-md text-text-secondary hover:text-text-primary hover:bg-bg-tertiary transition-colors cursor-pointer';

/** The ⋯ glyph. */
function MoreIcon() {
  return (
    <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
      <circle cx="5" cy="12" r="1.8" /><circle cx="12" cy="12" r="1.8" /><circle cx="19" cy="12" r="1.8" />
    </svg>
  );
}

/**
 * The header's middle and right on a phone, as in Claude: the chat's title, a
 * New chat pencil, and the chat's toggles in one labelled ⋯ menu instead of a
 * row of bare icons whose titles never show on touch. From md up the header
 * shows the toggles and site links itself.
 */
export function PhoneHeader({ title, onNewChat, isEphemeral, onToggleEphemeral, artifacts, sources, report }: {
  title: string;
  onNewChat: () => void;
  isEphemeral: boolean;
  onToggleEphemeral: () => void;
  /** Absent in a temporary chat, which keeps no artifacts. */
  artifacts?: { open: boolean; count: number; onToggle: () => void };
  /** Absent until the source catalogue has loaded. */
  sources?: { open: boolean; count: number; onToggle: () => void };
  /** Absent where the chat cannot be reported. */
  report?: { reported: boolean; onReport: () => void };
}) {
  const menu = useMenu();
  const { theme, setPref } = useTheme();
  const nextTheme = theme === 'dark' ? 'light' : 'dark';
  const pick = (fn: () => void) => () => { menu.close(); fn(); };

  return (
    <div className="flex md:hidden flex-1 min-w-0 items-center gap-1">
      <h1 className="flex-1 min-w-0 truncate text-[15px] font-semibold text-text-primary px-1" data-testid="phone-title">{title}</h1>
      <button type="button" onClick={onNewChat} aria-label="New chat" title="New chat" className={BUTTON}>
        <PencilIcon size={18} />
      </button>
      <div className="relative">
        <button
          {...menu.triggerProps}
          type="button"
          aria-label="Chat options"
          title="Chat options"
          className={`${BUTTON} ${isEphemeral ? 'text-warning' : ''}`}
          data-testid="phone-more"
        >
          <MoreIcon />
        </button>
        {menu.open && (
          <div {...menu.menuProps} aria-label="Chat options" className="absolute top-full right-0 mt-2 w-64 max-w-[calc(100vw-2rem)] bg-bg-secondary border border-border rounded-xl shadow-lg z-50 overflow-hidden py-1">
            <button type="button" role="menuitemcheckbox" aria-checked={isEphemeral} onClick={pick(onToggleEphemeral)} className={ITEM}>
              <span className={isEphemeral ? 'text-warning' : 'text-text-secondary'}><GhostIcon size={16} /></span>
              <span className="flex-1">Temporary chat</span>
              {isEphemeral && <span className="text-xs text-warning">On</span>}
            </button>
            {artifacts && (
              <button type="button" role="menuitemcheckbox" aria-checked={artifacts.open} onClick={pick(artifacts.onToggle)} className={ITEM}>
                <span className="text-text-secondary"><LayersIcon size={16} /></span>
                <span className="flex-1">Artifacts</span>
                {artifacts.count > 0 && <span className="text-xs text-text-secondary tabular-nums">{artifacts.count}</span>}
              </button>
            )}
            {sources && (
              <button type="button" role="menuitemcheckbox" aria-checked={sources.open} onClick={pick(sources.onToggle)} className={ITEM}>
                <span className="text-text-secondary"><BookIcon size={16} /></span>
                <span className="flex-1">Sources</span>
                {sources.count > 0 && <span className="text-xs text-accent tabular-nums">{sources.count} chosen</span>}
              </button>
            )}
            {report && (
              <button type="button" role="menuitem" onClick={pick(report.onReport)} disabled={report.reported} className={`${ITEM} disabled:text-text-secondary disabled:cursor-default`}>
                <span className={report.reported ? 'text-warning' : 'text-text-secondary'}><FlagIcon size={16} filled={report.reported} /></span>
                <span className="flex-1">{report.reported ? 'Chat reported' : 'Report this chat'}</span>
              </button>
            )}
            <div className="my-1 border-t border-border" />
            <button type="button" role="menuitem" onClick={pick(() => setPref(nextTheme))} className={ITEM}>
              <span className="text-text-secondary">{theme === 'dark' ? <SunIcon size={16} /> : <MoonIcon size={16} />}</span>
              <span className="flex-1">{`Switch to ${nextTheme} theme`}</span>
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
