import { useState, useEffect, useCallback } from 'react';
import {
  fetchAdminGroups,
  createAdminGroup,
  updateAdminGroup,
  deleteAdminGroup,
} from '../../lib/api';
import type { AdminGroup } from '../../lib/api';


export function GroupsTab() {
  const [groups, setGroups] = useState<AdminGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<AdminGroup | null>(null);
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setGroups(await fetchAdminGroups());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load groups');
    }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading) return <div className="text-text-secondary text-sm py-12 text-center">Loading groups...</div>;
  if (error) return <div className="text-error text-sm py-12 text-center">{error}</div>;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-end">
        <button
          onClick={() => setAdding(true)}
          className="bg-accent hover:bg-accent/90 text-bg-primary text-sm font-medium px-3 py-1.5 rounded cursor-pointer transition-colors"
        >
          + Add group
        </button>
      </div>

      <div className="bg-bg-secondary border border-border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-text-secondary text-xs">
              <th className="px-3 py-2 font-medium">Display name</th>
              <th className="px-3 py-2 font-medium">Slug</th>
              <th className="px-3 py-2 font-medium text-right">Members</th>
              <th className="px-3 py-2 font-medium w-px"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {groups.map(g => (
              <tr key={g.slug} className="hover:bg-bg-primary/30">
                <td className="px-3 py-2 text-text-primary">{g.display_name}</td>
                <td className="px-3 py-2 text-text-secondary font-mono text-xs">{g.slug}</td>
                <td className="px-3 py-2 text-right text-text-secondary text-xs">{g.member_count}</td>
                <td className="px-3 py-2 whitespace-nowrap">
                  <button
                    onClick={() => setEditing(g)}
                    className="text-xs text-accent hover:underline cursor-pointer"
                  >Edit</button>
                </td>
              </tr>
            ))}
            {groups.length === 0 && (
              <tr><td colSpan={4} className="px-3 py-6 text-center text-text-secondary text-sm">
                No groups yet.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      {editing && (
        <GroupEditModal
          group={editing}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); load(); }}
        />
      )}
      {adding && (
        <GroupCreateModal
          onClose={() => setAdding(false)}
          onSaved={() => { setAdding(false); load(); }}
        />
      )}
    </div>
  );
}


function GroupCreateModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [slug, setSlug] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit() {
    setSaving(true);
    setErr(null);
    try {
      await createAdminGroup({ slug: slug.trim(), display_name: displayName.trim() });
      onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to create');
      setSaving(false);
    }
  }

  const slugValid = /^[a-z0-9][a-z0-9_-]*$/.test(slug);

  return (
    <ModalShell title="Add group" onClose={onClose}>
      <Field label="Slug">
        <Input value={slug} onChange={v => setSlug(v.toLowerCase())} placeholder="lowercase, no spaces" />
        {slug && !slugValid && (
          <p className="text-[11px] text-error mt-1">Slug must be lowercase alphanumeric (hyphens / underscores allowed).</p>
        )}
      </Field>
      <Field label="Display name">
        <Input value={displayName} onChange={setDisplayName} placeholder="e.g. Elgeti Lab (Leipzig)" />
      </Field>
      {err && <div className="text-xs text-error">{err}</div>}
      <Actions
        onCancel={onClose}
        onSubmit={submit}
        submitLabel="Create"
        submitting={saving}
        disabled={!slugValid || !displayName.trim()}
      />
    </ModalShell>
  );
}


function GroupEditModal({ group, onClose, onSaved }: { group: AdminGroup; onClose: () => void; onSaved: () => void }) {
  const [displayName, setDisplayName] = useState(group.display_name);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function save() {
    setSaving(true);
    setErr(null);
    try {
      await updateAdminGroup(group.slug, displayName.trim());
      onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to save');
      setSaving(false);
    }
  }

  async function remove() {
    if (group.member_count > 0) return;
    if (!confirm(`Delete group "${group.display_name}"?`)) return;
    setSaving(true);
    setErr(null);
    try {
      await deleteAdminGroup(group.slug);
      onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to delete');
      setSaving(false);
    }
  }

  return (
    <ModalShell title={`Edit ${group.display_name}`} onClose={onClose}>
      <Field label="Slug (read-only)">
        <code className="block px-3 py-1.5 text-sm font-mono bg-bg-secondary border border-border rounded text-text-secondary">{group.slug}</code>
      </Field>
      <Field label="Display name">
        <Input value={displayName} onChange={setDisplayName} />
      </Field>
      <div className="text-xs text-text-secondary">
        Members: <span className="text-text-primary font-medium">{group.member_count}</span>
        {group.member_count > 0 && (
          <span className="text-text-secondary/70"> &middot; reassign members before deleting.</span>
        )}
      </div>
      {err && <div className="text-xs text-error">{err}</div>}
      <div className="flex items-center justify-between pt-2 border-t border-border mt-3">
        <button
          onClick={remove}
          disabled={group.member_count > 0 || saving}
          className="text-xs text-error hover:underline cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed disabled:no-underline"
          title={group.member_count > 0 ? 'Group has members; reassign first' : 'Delete this group'}
        >Delete group</button>
        <div className="flex items-center gap-2">
          <button
            onClick={onClose}
            className="text-sm px-3 py-1.5 text-text-secondary hover:text-text-primary cursor-pointer"
          >Close</button>
          <button
            onClick={save}
            disabled={!displayName.trim() || displayName.trim() === group.display_name || saving}
            className="text-sm px-3 py-1.5 bg-accent text-bg-primary rounded font-medium cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
          >{saving ? 'Saving...' : 'Save'}</button>
        </div>
      </div>
    </ModalShell>
  );
}


function ModalShell({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-bg-primary border border-border rounded-lg p-5 w-full max-w-md"
        onClick={e => e.stopPropagation()}
      >
        <h3 className="text-base font-semibold text-text-primary mb-4">{title}</h3>
        <div className="space-y-3">{children}</div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs uppercase tracking-wide text-text-secondary mb-1">{label}</label>
      {children}
    </div>
  );
}

function Input({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder?: string }) {
  return (
    <input
      type="text"
      value={value}
      onChange={e => onChange(e.target.value)}
      placeholder={placeholder}
      className="w-full bg-bg-secondary border border-border rounded px-3 py-1.5 text-sm text-text-primary placeholder:text-text-secondary focus:outline-none focus:border-accent"
    />
  );
}

function Actions({ onCancel, onSubmit, submitLabel, submitting, disabled }: {
  onCancel: () => void;
  onSubmit: () => void;
  submitLabel: string;
  submitting: boolean;
  disabled: boolean;
}) {
  return (
    <div className="flex items-center justify-end gap-2 pt-2">
      <button
        onClick={onCancel}
        className="text-sm px-3 py-1.5 text-text-secondary hover:text-text-primary cursor-pointer"
      >Cancel</button>
      <button
        onClick={onSubmit}
        disabled={submitting || disabled}
        className="text-sm px-3 py-1.5 bg-accent text-bg-primary rounded font-medium cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
      >{submitting ? 'Saving...' : submitLabel}</button>
    </div>
  );
}
