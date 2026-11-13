import { useMenu } from '../hooks/useMenu';
import { MODES, routedLabel, type Mode } from '../lib/modes';
import { ChatIcon, CheckIcon, ChevronDownIcon, CodeIcon, FlaskIcon, SearchIcon } from './ui/icons';

/** The mark for each mode; Auto keeps the raven the composer has always shown. */
export function ModeIcon({ mode, size = 14 }: { mode: Mode; size?: number }) {
  if (mode === 'chat') return <ChatIcon size={size} />;
  if (mode === 'code') return <CodeIcon size={size} />;
  if (mode === 'research') return <SearchIcon size={size} />;
  return (
    <span
      aria-hidden="true"
      className="inline-block bg-current [mask:url(/shared/munin_logo_without_script.svg)_center/contain_no-repeat]"
      style={{ width: size + 2, height: size + 2 }}
    />
  );
}

/** The mode a reply was routed to, as quiet text; nothing for plain chat. */
export function ModeLabel({ profile }: { profile?: string | null }) {
  const label = routedLabel(profile);
  if (!label) return null;
  const id = MODES.find(m => m.label === label)?.id;
  return (
    <span className="inline-flex items-center gap-1 text-xs text-text-secondary select-none" data-testid="mode-label">
      {id && <ModeIcon mode={id} size={12} />}
      {label}
    </span>
  );
}

/**
 * Where Claude has its model picker: the mode the next message goes in. Auto
 * lets Munin pick per message; the others force one. While Deep Research is on
 * the chip says so and does nothing, as the send then starts a research job.
 */
export function ModeChip({ mode, onChange, typed, deepResearch }: {
  mode: Mode;
  onChange: (mode: Mode) => void;
  /** A mode the draft forces with a typed /command, which wins over the chip. */
  typed: Mode | null;
  deepResearch: boolean;
}) {
  const menu = useMenu();
  const shown = typed ?? mode;
  const label = MODES.find(m => m.id === shown)?.label ?? 'Auto';

  if (deepResearch) {
    return (
      <span className="inline-flex items-center gap-1.5 h-8 px-2 text-sm text-accent select-none" data-testid="mode-chip">
        <FlaskIcon size={14} />
        Deep Research
      </span>
    );
  }

  return (
    <div className="relative">
      <button
        {...menu.triggerProps}
        type="button"
        data-testid="mode-chip"
        aria-label={`Mode: ${label}${typed ? ', set by the typed command' : ''}`}
        title={typed ? 'Set by the command you typed' : 'Choose the mode'}
        className={`inline-flex items-center gap-1.5 h-8 px-2 rounded-lg text-sm transition-colors cursor-pointer hover:bg-bg-tertiary ${shown === 'auto' ? 'text-text-secondary hover:text-text-primary' : 'text-text-primary bg-bg-tertiary'}`}
      >
        <ModeIcon mode={shown} />
        <span>{label}</span>
        <ChevronDownIcon size={12} className="opacity-70" />
      </button>
      {menu.open && (
        <div {...menu.menuProps} aria-label="Mode" className="absolute bottom-full right-0 mb-2 w-72 max-w-[calc(100vw-3rem)] bg-bg-secondary border border-border rounded-xl shadow-lg z-50 overflow-hidden py-1">
          {MODES.map(m => (
            <button
              key={m.id}
              type="button"
              role="menuitemradio"
              aria-checked={mode === m.id}
              onClick={() => { onChange(m.id); menu.close(); }}
              className="w-full flex items-start gap-3 px-3 py-2 text-left text-sm text-text-primary hover:bg-bg-tertiary focus-visible:bg-bg-tertiary transition-colors cursor-pointer"
            >
              <span className="mt-0.5 text-text-secondary"><ModeIcon mode={m.id} size={16} /></span>
              <span className="flex-1 min-w-0">
                <span className="block">{m.label}</span>
                <span className="block text-xs text-text-secondary">{m.description}</span>
              </span>
              {mode === m.id && <span className="mt-0.5 text-accent"><CheckIcon size={14} /></span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
