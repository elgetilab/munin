import { useState, useEffect, useCallback } from 'react';
import {
  fetchAdminUsers,
  fetchAdminGroups,
  createAdminUser,
  updateAdminUser,
  deleteAdminUser,
  addAdminUserEmail,
  removeAdminUserEmail,
  setAdminUserPrimaryEmail,
} from '../../lib/api';
import type { AdminUser, AdminGroup, AdminRole } from '../../lib/api';


const ROLES: AdminRole[] = ['user', 'group_leader', 'admin'];

function roleLabel(role: AdminRole): string {
  return role === 'group_leader' ? 'Group leader' : role[0].toUpperCase() + role.slice(1);
}

type SortKey = 'first_name' | 'last_name' | 'primary_email' | 'role' | 'group' | 'emails';

// Rank roles by privilege so the Role column sorts user -> group_leader ->
// admin rather than alphabetically.
const ROLE_RANK: Record<AdminRole, number> = { user: 0, group_leader: 1, admin: 2 };

// Comparable value for a user under a given column. Numbers sort numerically
// (role rank, email count); everything else case-insensitively as strings.
function sortValue(u: AdminUser, key: SortKey): string | number {
  switch (key) {
    case 'first_name': return u.first_name.toLowerCase();
    case 'last_name': return u.last_name.toLowerCase();
    case 'primary_email': return u.primary_email.toLowerCase();
    case 'role': return ROLE_RANK[u.role];
    case 'group': return (u.group ?? '').toLowerCase();
    case 'emails': return u.emails.length;
  }
}

function SortHeader({ label, col, active, dir, onSort, align }: {
  label: string;
  col: SortKey;
  active: boolean;
  dir: 'asc' | 'desc';
  onSort: (key: SortKey) => void;
  align?: 'right';
}) {
  return (
    <th className={`px-3 py-2 font-medium ${align === 'right' ? 'text-right' : ''}`}>
      <button
        type="button"
        onClick={() => onSort(col)}
        className={`inline-flex items-center gap-1 hover:text-text-primary cursor-pointer ${align === 'right' ? 'flex-row-reverse' : ''}`}
      >
        <span>{label}</span>
        <span className={active ? 'text-accent' : 'text-text-secondary/40'} aria-hidden>
          {active ? (dir === 'asc' ? '▲' : '▼') : '↕'}
        </span>
      </button>
    </th>
  );
}


export function UsersTab() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [groups, setGroups] = useState<AdminGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<AdminUser | null>(null);
  const [adding, setAdding] = useState(false);
  const [filter, setFilter] = useState('');
  const [unassignedOnly, setUnassignedOnly] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>('first_name');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');

  // Click a header to sort by it; click the active header again to flip
  // direction. Switching columns always starts ascending.
  const toggleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir(d => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('asc');
    }
  };

  // Effect-owned read, same shape as GroupsTab: nothing is set synchronously
  // when the effect fires, a response that arrives after unmount or after a
  // newer read started is discarded, and `load` is now "ask for another
  // read" rather than "do a read". Both requests still go out together,
  // because the table needs the group list to render a user's group.
  const [reloadKey, setReloadKey] = useState(0);
  const load = useCallback(() => setReloadKey(k => k + 1), []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [u, g] = await Promise.all([fetchAdminUsers(), fetchAdminGroups()]);
        if (cancelled) return;
        setUsers(u);
        setGroups(g);
        setError(null);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : 'Failed to load users');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [reloadKey]);

  const filtered = users.filter(u => {
    // "Unassigned" = no primary group. A multi-group member still has a
    // primary group set, so this surfaces only users with no group at all.
    if (unassignedOnly && u.group) return false;
    if (!filter) return true;
    const f = filter.toLowerCase();
    return (
      u.first_name.toLowerCase().includes(f) ||
      u.last_name.toLowerCase().includes(f) ||
      u.name.toLowerCase().includes(f) ||
      u.emails.some(e => e.toLowerCase().includes(f)) ||
      (u.group ?? '').toLowerCase().includes(f)
    );
  });

  const displayed = [...filtered].sort((a, b) => {
    const av = sortValue(a, sortKey);
    const bv = sortValue(b, sortKey);
    const cmp =
      typeof av === 'number' && typeof bv === 'number'
        ? av - bv
        : String(av).localeCompare(String(bv));
    return sortDir === 'asc' ? cmp : -cmp;
  });

  if (loading) return <div className="text-text-secondary text-sm py-12 text-center">Loading users...</div>;
  if (error) return <div className="text-error text-sm py-12 text-center">{error}</div>;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <input
          type="text"
          placeholder="Filter by first/last name, email, or group..."
          value={filter}
          onChange={e => setFilter(e.target.value)}
          className="flex-1 bg-bg-secondary border border-border rounded px-3 py-1.5 text-sm text-text-primary placeholder:text-text-secondary focus:outline-none focus:border-accent"
        />
        <label className="flex items-center gap-1.5 text-xs text-text-secondary cursor-pointer select-none whitespace-nowrap">
          <input
            type="checkbox"
            checked={unassignedOnly}
            onChange={e => setUnassignedOnly(e.target.checked)}
            className="accent-accent cursor-pointer"
          />
          Unassigned only
        </label>
        <span className="text-xs text-text-secondary">{filtered.length} / {users.length}</span>
        <button
          onClick={() => setAdding(true)}
          className="bg-accent hover:bg-accent/90 text-bg-primary text-sm font-medium px-3 py-1.5 rounded cursor-pointer transition-colors"
        >
          + Add user
        </button>
      </div>

      <div className="bg-bg-secondary border border-border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-text-secondary text-xs">
              <SortHeader label="First name" col="first_name" active={sortKey === 'first_name'} dir={sortDir} onSort={toggleSort} />
              <SortHeader label="Last name" col="last_name" active={sortKey === 'last_name'} dir={sortDir} onSort={toggleSort} />
              <SortHeader label="Primary email" col="primary_email" active={sortKey === 'primary_email'} dir={sortDir} onSort={toggleSort} />
              <SortHeader label="Role" col="role" active={sortKey === 'role'} dir={sortDir} onSort={toggleSort} />
              <SortHeader label="Group" col="group" active={sortKey === 'group'} dir={sortDir} onSort={toggleSort} />
              <SortHeader label="Emails" col="emails" active={sortKey === 'emails'} dir={sortDir} onSort={toggleSort} align="right" />
              <th className="px-3 py-2 font-medium w-px"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {displayed.map(u => (
              <tr key={u.id} className="hover:bg-bg-primary/30">
                <td className="px-3 py-2 text-text-primary">{u.first_name}</td>
                <td className="px-3 py-2 text-text-primary">
                  {u.last_name || <span className="text-text-secondary/50">–</span>}
                </td>
                <td className="px-3 py-2 text-text-secondary font-mono text-xs">{u.primary_email}</td>
                <td className="px-3 py-2"><RoleBadge role={u.role} /></td>
                <td className="px-3 py-2 text-text-secondary">{u.group ?? <span className="text-text-secondary/50">–</span>}</td>
                <td className="px-3 py-2 text-right text-text-secondary text-xs">{u.emails.length}</td>
                <td className="px-3 py-2 whitespace-nowrap">
                  <button
                    onClick={() => setEditing(u)}
                    className="text-xs text-accent hover:underline cursor-pointer mr-2"
                  >Edit</button>
                </td>
              </tr>
            ))}
            {displayed.length === 0 && (
              <tr><td colSpan={7} className="px-3 py-6 text-center text-text-secondary text-sm">
                {users.length === 0
                  ? 'No users yet.'
                  : unassignedOnly && !filter
                    ? 'No unassigned users — everyone belongs to a group.'
                    : 'No users match the filter.'}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      {editing && (
        <UserEditModal
          user={editing}
          groups={groups}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); load(); }}
        />
      )}
      {adding && (
        <UserCreateModal
          groups={groups}
          onClose={() => setAdding(false)}
          onSaved={() => { setAdding(false); load(); }}
        />
      )}
    </div>
  );
}


function RoleBadge({ role }: { role: AdminRole }) {
  const colors: Record<AdminRole, string> = {
    user: 'bg-text-secondary/15 text-text-secondary',
    group_leader: 'bg-yellow-400/15 text-yellow-300',
    admin: 'bg-accent/20 text-accent',
  };
  return (
    <span className={`inline-block text-[10px] font-medium uppercase tracking-wide px-1.5 py-0.5 rounded ${colors[role]}`}>
      {roleLabel(role)}
    </span>
  );
}


function UserCreateModal({ groups, onClose, onSaved }: {
  groups: AdminGroup[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<AdminRole>('user');
  const [group, setGroup] = useState<string>('');
  const [username, setUsername] = useState('');
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit() {
    setSaving(true);
    setErr(null);
    try {
      await createAdminUser({
        first_name: firstName.trim(),
        last_name: lastName.trim() || undefined,
        email: email.trim(),
        role,
        group: group || null,
        username: username.trim() || null,
      });
      onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to create');
      setSaving(false);
    }
  }

  return (
    <Modal title="Add user" onClose={onClose}>
      <Field label="First name">
        <Input value={firstName} onChange={setFirstName} placeholder="Jane" />
      </Field>
      <Field label="Last name">
        <Input value={lastName} onChange={setLastName} placeholder="Doe (optional)" />
      </Field>
      <Field label="Primary email">
        <Input value={email} onChange={setEmail} placeholder="jane.doe@example.org" />
      </Field>
      <Field label="Role">
        <Select value={role} onChange={v => setRole(v as AdminRole)} options={ROLES.map(r => ({ value: r, label: roleLabel(r) }))} />
      </Field>
      <Field label="Group">
        <Select value={group} onChange={setGroup} options={[
          { value: '', label: '(none)' },
          ...groups.map(g => ({ value: g.slug, label: `${g.display_name} (${g.slug})` })),
        ]} />
      </Field>
      <Field label="Username (optional)">
        <Input value={username} onChange={setUsername} placeholder="for #@handle citations" />
      </Field>
      {err && <div className="text-xs text-error">{err}</div>}
      <ModalActions
        onCancel={onClose}
        onSubmit={submit}
        submitLabel="Create"
        submitting={saving}
        disabled={!firstName.trim() || !email.trim() || !email.includes('@')}
      />
    </Modal>
  );
}


function UserEditModal({ user, groups, onClose, onSaved }: {
  user: AdminUser;
  groups: AdminGroup[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [current, setCurrent] = useState<AdminUser>(user);
  const [firstName, setFirstName] = useState(user.first_name);
  const [lastName, setLastName] = useState(user.last_name);
  const [role, setRole] = useState<AdminRole>(user.role);
  const [group, setGroup] = useState<string>(user.group ?? '');
  const [username, setUsername] = useState(user.username ?? '');
  const [newEmail, setNewEmail] = useState('');
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const dirty =
    firstName.trim() !== current.first_name ||
    lastName.trim() !== current.last_name ||
    role !== current.role ||
    (group || null) !== current.group ||
    (username.trim() || null) !== current.username;

  async function saveProps() {
    setSaving(true);
    setErr(null);
    try {
      const updated = await updateAdminUser(current.id, {
        first_name: firstName.trim(),
        last_name: lastName.trim(),
        role,
        group: group || null,
        username: username.trim() || null,
      });
      setCurrent(updated);
      onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to save');
    }
    setSaving(false);
  }

  async function withBusy<T>(key: string, fn: () => Promise<T>) {
    setBusy(key);
    setErr(null);
    try { return await fn(); }
    catch (e) { setErr(e instanceof Error ? e.message : 'Failed'); }
    finally { setBusy(null); }
  }

  async function addEmail() {
    const em = newEmail.trim();
    if (!em || !em.includes('@')) return;
    const updated = await withBusy('add-' + em, () => addAdminUserEmail(current.id, em));
    if (updated) { setCurrent(updated); setNewEmail(''); }
  }

  async function removeEmail(em: string) {
    if (current.emails.length === 1) return;
    if (!confirm(`Remove email ${em} from ${current.name}?`)) return;
    const updated = await withBusy('rm-' + em, () => removeAdminUserEmail(current.id, em));
    if (updated) setCurrent(updated);
  }

  async function setPrimary(em: string) {
    const updated = await withBusy('prim-' + em, () => setAdminUserPrimaryEmail(current.id, em));
    if (updated) setCurrent(updated);
  }

  async function deleteUser() {
    if (!confirm(`Delete user ${current.name} (${current.primary_email})? This will sign them out and cascade to all their emails.`)) return;
    await withBusy('delete', async () => {
      await deleteAdminUser(current.id);
      onSaved();
    });
  }

  return (
    <Modal title={`Edit ${user.name}`} onClose={onClose}>
      <Field label="First name">
        <Input value={firstName} onChange={setFirstName} />
      </Field>
      <Field label="Last name">
        <Input value={lastName} onChange={setLastName} placeholder="(optional)" />
      </Field>
      <Field label="Role">
        <Select value={role} onChange={v => setRole(v as AdminRole)} options={ROLES.map(r => ({ value: r, label: roleLabel(r) }))} />
      </Field>
      <Field label="Group">
        <Select value={group} onChange={setGroup} options={[
          { value: '', label: '(none)' },
          ...groups.map(g => ({ value: g.slug, label: `${g.display_name} (${g.slug})` })),
        ]} />
      </Field>
      <Field label="Username">
        <Input value={username} onChange={setUsername} placeholder="for #@handle citations" />
      </Field>

      <Field label={`Emails (${current.emails.length})`}>
        <div className="space-y-1">
          {current.emails.map(em => {
            const isPrimary = em === current.primary_email;
            return (
              <div key={em} className="flex items-center gap-2 text-sm">
                <span className="font-mono flex-1 truncate text-text-primary">{em}</span>
                {isPrimary ? (
                  <span className="text-[10px] uppercase tracking-wide text-accent px-1.5 py-0.5 bg-accent/15 rounded">Primary</span>
                ) : (
                  <button
                    onClick={() => setPrimary(em)}
                    disabled={!!busy}
                    className="text-[11px] text-text-secondary hover:text-accent cursor-pointer disabled:opacity-50"
                  >Make primary</button>
                )}
                <button
                  onClick={() => removeEmail(em)}
                  disabled={current.emails.length === 1 || !!busy}
                  className="text-[11px] text-text-secondary hover:text-error cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed"
                  title={current.emails.length === 1 ? 'A user must have at least one email' : 'Remove'}
                >Remove</button>
              </div>
            );
          })}
          <div className="flex items-center gap-2 pt-2">
            <Input value={newEmail} onChange={setNewEmail} placeholder="add an alias..." />
            <button
              onClick={addEmail}
              disabled={!newEmail.trim() || !newEmail.includes('@') || !!busy}
              className="text-xs px-2 py-1 bg-accent/15 text-accent hover:bg-accent/25 rounded cursor-pointer disabled:opacity-50"
            >Add</button>
          </div>
        </div>
      </Field>

      {err && <div className="text-xs text-error">{err}</div>}

      <div className="flex items-center justify-between pt-2 border-t border-border mt-3">
        <button
          onClick={deleteUser}
          disabled={!!busy || saving}
          className="text-xs text-error hover:underline cursor-pointer disabled:opacity-50"
        >Delete user</button>
        <div className="flex items-center gap-2">
          <button
            onClick={onClose}
            className="text-sm px-3 py-1.5 text-text-secondary hover:text-text-primary cursor-pointer"
          >Close</button>
          <button
            onClick={saveProps}
            disabled={!dirty || saving}
            className="text-sm px-3 py-1.5 bg-accent text-bg-primary rounded font-medium cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
          >{saving ? 'Saving...' : 'Save'}</button>
        </div>
      </div>
    </Modal>
  );
}


function Modal({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-bg-primary border border-border rounded-lg p-5 w-full max-w-md max-h-[85vh] overflow-y-auto"
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

function Select({ value, onChange, options }: { value: string; onChange: (v: string) => void; options: { value: string; label: string }[] }) {
  return (
    <select
      value={value}
      onChange={e => onChange(e.target.value)}
      className="w-full bg-bg-secondary border border-border rounded px-3 py-1.5 text-sm text-text-primary cursor-pointer focus:outline-none focus:border-accent"
    >
      {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}

function ModalActions({ onCancel, onSubmit, submitLabel, submitting, disabled }: {
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
