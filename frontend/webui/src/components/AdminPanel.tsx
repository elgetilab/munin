import { useState, useEffect, useCallback } from 'react';
import { fetchAdminActivity, fetchAdminUsage } from '../lib/api';
import type { AdminActivity, AdminUsage } from '../lib/api';
import { useUiStore } from '../stores/uiStore';
import { UsersTab } from './admin/UsersTab';
import { GroupsTab } from './admin/GroupsTab';

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function timeAgo(iso: string): string {
  const now = Date.now();
  const then = new Date(iso).getTime();
  const seconds = Math.floor((now - then) / 1000);
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function shortEmail(email: string): string {
  return email.split('@')[0];
}

// P2 #26 commit 2: onClose used to be a prop; App.tsx passed
// `() => setShowAdmin(false)` purely to invert its own flag. With
// uiStore the panel can close itself, so the prop is gone and
// AdminPanel becomes a zero-prop component.
export function AdminPanel() {
  const setShowAdmin = useUiStore(s => s.setShowAdmin);
  const onClose = () => setShowAdmin(false);
  const [tab, setTab] = useState<'activity' | 'usage' | 'users' | 'groups'>('activity');
  const [activity, setActivity] = useState<AdminActivity | null>(null);
  const [usage, setUsage] = useState<AdminUsage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Activity/usage data is on a 30s timer; users + groups load on their
  // own tab mount because they're rarely-changing CRUD lists.
  const needsLiveData = tab === 'activity' || tab === 'usage';

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [act, usg] = await Promise.all([fetchAdminActivity(), fetchAdminUsage()]);
      setActivity(act);
      setUsage(usg);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load admin data');
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    if (needsLiveData) loadData();
  }, [loadData, needsLiveData]);

  // Auto-refresh every 30 seconds (only while the user is looking at
  // a tab that consumes the live data).
  useEffect(() => {
    if (!needsLiveData) return;
    const timer = setInterval(loadData, 30_000);
    return () => clearInterval(timer);
  }, [loadData, needsLiveData]);

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-4xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-xl font-semibold text-text-primary">Admin Dashboard</h2>
          <button
            onClick={onClose}
            className="text-text-secondary hover:text-text-primary transition-colors cursor-pointer text-sm"
          >
            &#10005; Close
          </button>
        </div>

        {/* Summary cards */}
        {!loading && activity && usage && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
            <div className="bg-bg-secondary border border-border rounded-lg px-4 py-3">
              <div className="text-2xl font-bold text-green-400">{activity.online.length}</div>
              <div className="text-xs text-text-secondary">Online now</div>
            </div>
            <div className="bg-bg-secondary border border-border rounded-lg px-4 py-3">
              <div className="text-2xl font-bold text-yellow-400">{activity.recent.length}</div>
              <div className="text-xs text-text-secondary">Active today</div>
            </div>
            <div className="bg-bg-secondary border border-border rounded-lg px-4 py-3">
              <div className="text-2xl font-bold text-accent">{usage.active_users}</div>
              <div className="text-xs text-text-secondary">Users this month</div>
            </div>
            <div className="bg-bg-secondary border border-border rounded-lg px-4 py-3">
              <div className="text-2xl font-bold text-text-primary">{formatTokens(usage.total_tokens)}</div>
              <div className="text-xs text-text-secondary">Tokens this month</div>
            </div>
          </div>
        )}

        {/* Tabs */}
        <div className="flex gap-1 mb-4 border-b border-border">
          <TabButton current={tab} value="activity" onClick={setTab}>Activity</TabButton>
          <TabButton current={tab} value="usage" onClick={setTab}>Usage</TabButton>
          <TabButton current={tab} value="users" onClick={setTab}>Users</TabButton>
          <TabButton current={tab} value="groups" onClick={setTab}>Groups</TabButton>
        </div>

        {/* Content */}
        {tab === 'users' ? (
          <UsersTab />
        ) : tab === 'groups' ? (
          <GroupsTab />
        ) : loading ? (
          <div className="text-center text-text-secondary text-sm py-12">Loading...</div>
        ) : error ? (
          <div className="text-center text-error text-sm py-12">{error}</div>
        ) : tab === 'activity' ? (
          <ActivityTab activity={activity!} />
        ) : (
          <UsageTab usage={usage!} />
        )}
      </div>
    </div>
  );
}

type AdminTab = 'activity' | 'usage' | 'users' | 'groups';

function TabButton({ current, value, onClick, children }: {
  current: AdminTab;
  value: AdminTab;
  onClick: (t: AdminTab) => void;
  children: React.ReactNode;
}) {
  const active = current === value;
  return (
    <button
      onClick={() => onClick(value)}
      className={`px-4 py-2 text-sm transition-colors cursor-pointer border-b-2 -mb-px ${
        active
          ? 'border-accent text-accent'
          : 'border-transparent text-text-secondary hover:text-text-primary'
      }`}
    >
      {children}
    </button>
  );
}


function ActivityTab({ activity }: { activity: AdminActivity }) {
  return (
    <div className="space-y-6">
      {/* Online users */}
      {activity.online.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-text-primary mb-2 flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-green-400 inline-block" />
            Online ({activity.online.length})
          </h3>
          <div className="bg-bg-secondary border border-border rounded-lg divide-y divide-border">
            {activity.online.map(u => (
              <UserRow key={u.email} user={u} status="online" />
            ))}
          </div>
        </div>
      )}

      {/* Recent users */}
      {activity.recent.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-text-primary mb-2 flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-yellow-400 inline-block" />
            Recent ({activity.recent.length})
          </h3>
          <div className="bg-bg-secondary border border-border rounded-lg divide-y divide-border">
            {activity.recent.map(u => (
              <UserRow key={u.email} user={u} status="recent" />
            ))}
          </div>
        </div>
      )}

      {/* All users */}
      <div>
        <h3 className="text-sm font-semibold text-text-primary mb-2">
          All tracked users ({activity.total})
        </h3>
        <div className="bg-bg-secondary border border-border rounded-lg divide-y divide-border max-h-[50vh] overflow-y-auto">
          {activity.all_users.map(u => {
            const isOnline = activity.online.some(o => o.email === u.email);
            const isRecent = activity.recent.some(r => r.email === u.email);
            return (
              <UserRow
                key={u.email}
                user={u}
                status={isOnline ? 'online' : isRecent ? 'recent' : 'offline'}
              />
            );
          })}
          {activity.all_users.length === 0 && (
            <div className="px-4 py-6 text-center text-text-secondary text-sm">No activity recorded yet</div>
          )}
        </div>
      </div>
    </div>
  );
}

function UserRow({ user, status }: { user: { email: string; last_seen_at: string; source: string }; status: 'online' | 'recent' | 'offline' }) {
  const dotColor = status === 'online' ? 'bg-green-400' : status === 'recent' ? 'bg-yellow-400' : 'bg-text-secondary/30';
  return (
    <div className="flex items-center gap-3 px-4 py-2.5">
      <span className={`w-2 h-2 rounded-full ${dotColor} flex-shrink-0`} />
      <div className="flex-1 min-w-0">
        <div className="text-sm text-text-primary truncate">{shortEmail(user.email)}</div>
        <div className="text-[11px] text-text-secondary truncate">{user.email}</div>
      </div>
      <div className="text-right flex-shrink-0">
        <div className="text-xs text-text-secondary">{timeAgo(user.last_seen_at)}</div>
        <div className="text-[10px] text-text-secondary/60">{user.source}</div>
      </div>
    </div>
  );
}

function UsageTab({ usage }: { usage: AdminUsage }) {
  return (
    <div className="space-y-6">
      {/* Period */}
      <div className="text-sm text-text-secondary">
        Period: <span className="text-text-primary font-medium">{usage.period}</span>
        {' '}&middot;{' '}
        {usage.total_requests.toLocaleString()} requests
        {' '}&middot;{' '}
        {formatTokens(usage.total_tokens)} tokens
      </div>

      {/* Per-user table */}
      <div>
        <h3 className="text-sm font-semibold text-text-primary mb-2">Per-user usage</h3>
        <div className="bg-bg-secondary border border-border rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-text-secondary text-xs">
                <th className="px-4 py-2 font-medium">User</th>
                <th className="px-4 py-2 font-medium text-right">Tokens</th>
                <th className="px-4 py-2 font-medium text-right">Requests</th>
                <th className="px-4 py-2 font-medium text-right hidden md:table-cell">Browser</th>
                <th className="px-4 py-2 font-medium text-right hidden md:table-cell">API</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {usage.top_users.map(u => {
                const browserTokens = u.by_source?.browser?.tokens || 0;
                const apiTokens = u.by_source?.api_key?.tokens || 0;
                // Bar width relative to top user
                const maxTokens = usage.top_users[0]?.tokens || 1;
                const pct = Math.max(1, (u.tokens / maxTokens) * 100);
                return (
                  <tr key={u.email} className="relative">
                    <td className="px-4 py-2.5 relative">
                      {/* Background bar */}
                      <div
                        className="absolute inset-y-0 left-0 bg-accent/8"
                        style={{ width: `${pct}%` }}
                      />
                      <div className="relative">
                        <div className="text-text-primary truncate max-w-[200px]">{shortEmail(u.email)}</div>
                        <div className="text-[10px] text-text-secondary truncate md:hidden">{u.email}</div>
                      </div>
                    </td>
                    <td className="px-4 py-2.5 text-right text-text-primary font-mono text-xs">
                      {formatTokens(u.tokens)}
                    </td>
                    <td className="px-4 py-2.5 text-right text-text-secondary text-xs">
                      {u.requests.toLocaleString()}
                    </td>
                    <td className="px-4 py-2.5 text-right text-text-secondary text-xs hidden md:table-cell">
                      {browserTokens > 0 ? formatTokens(browserTokens) : '-'}
                    </td>
                    <td className="px-4 py-2.5 text-right text-text-secondary text-xs hidden md:table-cell">
                      {apiTokens > 0 ? formatTokens(apiTokens) : '-'}
                    </td>
                  </tr>
                );
              })}
              {usage.top_users.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-4 py-6 text-center text-text-secondary">No usage data this month</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Tools usage */}
      {Object.keys(usage.tools_usage).length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-text-primary mb-2">Tool invocations</h3>
          <div className="bg-bg-secondary border border-border rounded-lg divide-y divide-border">
            {Object.entries(usage.tools_usage)
              .sort(([, a], [, b]) => b - a)
              .map(([tool, count]) => (
                <div key={tool} className="flex items-center px-4 py-2">
                  <span className="text-sm text-text-primary flex-1 font-mono">{tool}</span>
                  <span className="text-xs text-text-secondary">{count.toLocaleString()}</span>
                </div>
              ))}
          </div>
        </div>
      )}
    </div>
  );
}
