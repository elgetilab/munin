// Form pieces shared by the admin dialogs, styled to match the Settings pages:
// sentence-case labels and rounded-lg controls.

export function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-sm text-text-secondary mb-1">{label}</label>
      {children}
    </div>
  );
}

const CONTROL = 'w-full bg-bg-primary border border-border rounded-lg px-3 py-1.5 text-sm text-text-primary focus:outline-none focus:border-text-secondary';

export function Input({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder?: string }) {
  return (
    <input
      type="text"
      value={value}
      onChange={e => onChange(e.target.value)}
      placeholder={placeholder}
      className={`${CONTROL} placeholder:text-text-secondary`}
    />
  );
}

export function Select({ value, onChange, options }: { value: string; onChange: (v: string) => void; options: { value: string; label: string }[] }) {
  return (
    <select
      value={value}
      onChange={e => onChange(e.target.value)}
      className={`${CONTROL} cursor-pointer`}
    >
      {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}

export const PRIMARY_BUTTON = 'text-sm px-3 py-1.5 bg-accent text-bg-primary rounded-lg font-medium cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed';
export const SECONDARY_BUTTON = 'text-sm px-3 py-1.5 text-text-secondary hover:text-text-primary cursor-pointer';

export function ModalActions({ formId, onCancel, submitLabel, submitting, disabled }: {
  formId: string;
  onCancel: () => void;
  submitLabel: string;
  submitting: boolean;
  disabled: boolean;
}) {
  return (
    <>
      <button type="button" onClick={onCancel} className={SECONDARY_BUTTON}>Cancel</button>
      <button
        type="submit"
        form={formId}
        disabled={submitting || disabled}
        className={PRIMARY_BUTTON}
      >{submitting ? 'Saving...' : submitLabel}</button>
    </>
  );
}
