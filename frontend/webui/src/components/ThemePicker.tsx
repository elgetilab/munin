import { useTheme, type ThemePref } from '../lib/theme';

const OPTIONS: { value: ThemePref; label: string }[] = [
  { value: 'dark', label: 'Dark' },
  { value: 'light', label: 'Light' },
  { value: 'system', label: 'Match system' },
];

/** Dark, light or match the system. The choice applies to every Munin site in this browser. */
export function ThemePicker() {
  const { pref, setPref } = useTheme();
  return (
    <fieldset>
      <legend className="text-sm text-text-secondary mb-3">Theme</legend>
      <div className="inline-flex rounded-lg border border-border p-0.5 bg-bg-secondary">
        {OPTIONS.map(o => (
          <label
            key={o.value}
            className={`px-3 py-1.5 text-sm rounded-md cursor-pointer transition-colors has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-accent ${
              pref === o.value
                ? 'bg-bg-tertiary text-text-primary font-medium'
                : 'text-text-secondary hover:text-text-primary'
            }`}
          >
            <input
              type="radio"
              name="munin-theme"
              value={o.value}
              checked={pref === o.value}
              onChange={() => setPref(o.value)}
              className="sr-only"
            />
            {o.label}
          </label>
        ))}
      </div>
      <p className="text-xs text-text-secondary mt-2">Applies to every Munin page in this browser.</p>
    </fieldset>
  );
}
