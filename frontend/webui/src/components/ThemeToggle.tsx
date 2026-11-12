import { useTheme } from '../lib/theme';
import { MoonIcon, SunIcon } from './ui/icons';

/**
 * The header's light/dark switch, the same button /shared/theme.js puts on the
 * static pages: a sun on a dark page, a moon on a light one, so the icon shows
 * where a click goes. Settings still offers Match system.
 */
export function ThemeToggle({ className = '' }: { className?: string }) {
  const { theme, setPref } = useTheme();
  const next = theme === 'dark' ? 'light' : 'dark';
  const label = `Switch to ${next} theme`;
  return (
    <button
      type="button"
      data-testid="theme-toggle"
      onClick={() => setPref(next)}
      aria-label={label}
      title={label}
      className={`inline-flex flex-shrink-0 items-center justify-center w-8 h-8 rounded-md text-text-secondary hover:text-text-primary hover:bg-bg-tertiary transition-colors cursor-pointer ${className}`}
    >
      {theme === 'dark' ? <SunIcon size={16} /> : <MoonIcon size={16} />}
    </button>
  );
}
