import { useState, useEffect, useCallback } from 'react';
import {
  fetchAdminGroups,
  createAdminGroup,
  updateAdminGroup,
  deleteAdminGroup,
  fetchAdminUsers,
  updateAdminUser,
  fetchGroupMembers,
  addGroupMember,
  removeGroupMember,
} from '../../lib/api';
import type { AdminGroup, AdminUser, AdminRole } from '../../lib/api';


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
          onReload={load}
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


function GroupEditModal({ group, onClose, onReload, onSaved }: {
  group: AdminGroup;
  onClose: () => void;
  onReload: () => void;
  onSaved: () => void;
}) {
  const [displayName, setDisplayName] = useState(group.display_name);
  const [members, setMembers] = useState<AdminUser[]>([]);
  const [allUsers, setAllUsers] = useState<AdminUser[]>([]);
  const [loadingMembers, setLoadingMembers] = useState(true);
  const [addFilter, setAddFilter] = useState('');
  const [memberBusy, setMemberBusy] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const loadMembers = useCallback(async () => {
    setLoadingMembers(true);
    try {
      const [m, all] = await Promise.all([fetchGroupMembers(group.slug), fetchAdminUsers()]);
      setMembers(m);
      setAllUsers(all);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to load members');
    }
    setLoadingMembers(false);
  }, [group.slug]);

  useEffect(() => { loadMembers(); }, [loadMembers]);

  async function withMember<T>(id: number, fn: () => Promise<T>): Promise<T | undefined> {
    setMemberBusy(id);
    setErr(null);
    try { return await fn(); }
    catch (e) { setErr(e instanceof Error ? e.message : 'Failed'); }
    finally { setMemberBusy(null); }
  }

  async function setMemberRole(m: AdminUser, role: AdminRole) {
    if (role === m.role) return;
    const updated = await withMember(m.id, () => updateAdminUser(m.id, { role }));
    if (updated) {
      setMembers(prev => prev.map(x => (x.id === m.id ? updated : x)));
      onReload();
    }
  }

  async function removeMember(m: AdminUser) {
    if (!confirm(`Remove ${m.name || m.primary_email} from ${group.display_name}?`)) return;
    const list = await withMember(m.id, () => removeGroupMember(group.slug, m.id));
    if (list) { setMembers(list); onReload(); }
  }

  async function addMember(u: AdminUser) {
    const list = await withMember(u.id, () => addGroupMember(group.slug, u.id));
    if (list) { setMembers(list); setAddFilter(''); onReload(); }
  }

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
    if (members.length > 0) return;
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

  const available = allUsers
    .filter(u => !members.some(m => m.id === u.id))
    .filter(u => {
      const f = addFilter.trim().toLowerCase();
      if (!f) return true;
      return u.name.toLowerCase().includes(f) || u.emails.some(e => e.toLowerCase().includes(f));
    })
    .slice(0, 8);

  return (
    <ModalShell title={`Edit ${group.display_name}`} onClose={onClose}>
      <Field label="Slug (read-only)">
        <code className="block px-3 py-1.5 text-sm font-mono bg-bg-secondary border border-border rounded text-text-secondary">{group.slug}</code>
      </Field>
      <Field label="Display name">
        <Input value={displayName} onChange={setDisplayName} />
      </Field>

      <Field label={`Members (${members.length})`}>
        {loadingMembers ? (
          <div className="text-xs text-text-secondary py-2">Loading members...</div>
        ) : (
          <div className="space-y-1">
            {members.map(m => (
              <div key={m.id} className="flex items-center gap-2 text-sm">
                <div className="flex-1 min-w-0">
                  <div className="text-text-primary truncate">{m.name || m.primary_email}</div>
                  <div className="text-[11px] text-text-secondary truncate font-mono">{m.primary_email}</div>
                </div>
                {m.role === 'admin' ? (
                  <span className="text-[10px] uppercase tracking-wide text-accent px-1.5 py-0.5 bg-accent/15 rounded">Admin</span>
                ) : (
                  <select
                    value={m.role}
                    onChange={e => setMemberRole(m, e.target.value as AdminRole)}
                    disabled={memberBusy === m.id}
                    className="bg-bg-secondary border border-border rounded px-2 py-1 text-xs text-text-primary cursor-pointer focus:outline-none focus:border-accent disabled:opacity-50"
                  >
                    <option value="user">Member</option>
                    <option value="group_leader">Leader</option>
                  </select>
                )}
                <button
                  onClick={() => removeMember(m)}
                  disabled={memberBusy === m.id}
                  className="text-[11px] text-text-secondary hover:text-error cursor-pointer disabled:opacity-50"
                >Remove</button>
              </div>
            ))}
            {members.length === 0 && (
              <div className="text-xs text-text-secondary py-1">No members yet.</div>
            )}
          </div>
        )}
      </Field>

      <Field label="Add member">
        <Input value={addFilter} onChange={setAddFilter} placeholder="search users by name or email..." />
        {addFilter.trim() && (
          <div className="mt-1 border border-border rounded divide-y divide-border max-h-40 overflow-y-auto">
            {available.length === 0 && (
              <div className="px-3 py-2 text-xs text-text-secondary">No matching users.</div>
            )}
            {available.map(u => (
              <button
                key={u.id}
                onClick={() => addMember(u)}
                disabled={memberBusy === u.id}
                className="w-full text-left px-3 py-1.5 hover:bg-bg-secondary flex items-center gap-2 cursor-pointer disabled:opacity-50"
              >
                <span className="flex-1 truncate text-text-primary text-sm">{u.name || u.primary_email}</span>
                <span className="text-[11px] text-text-secondary truncate font-mono">{u.primary_email}</span>
              </button>
            ))}
          </div>
        )}
      </Field>

      <div className="text-[11px] text-text-secondary">
        The <span className="text-text-primary">Leader</span> role lets a member upload to the knowledge base; it applies account-wide, not just to this group.
      </div>

      {err && <div className="text-xs text-error">{err}</div>}
      <div className="flex items-center justify-between pt-2 border-t border-border mt-3">
        <button
          onClick={remove}
          disabled={members.length > 0 || saving}
          className="text-xs text-error hover:underline cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed disabled:no-underline"
          title={members.length > 0 ? 'Group has members; remove them first' : 'Delete this group'}
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
