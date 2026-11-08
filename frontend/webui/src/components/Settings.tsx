import { useId, useState, useRef, useEffect, useCallback } from 'react';
import { useStatus } from '../hooks/useStatus';
import { updateProfile, fetchApiKeys, createApiKey, revokeApiKey, fetchUsageStats, fetchMuninProfile, updateMuninProfile, deleteMuninProfile } from '../lib/api';
import type { UserProfile, ApiKeyInfo, UsageStats } from '../lib/api';
import type { MuninProfile } from '../lib/types';
import { useUiStore } from '../stores/uiStore';
import { useUserStore } from '../stores/userStore';
import { API_PUBLIC_URL } from '../lib/urls';
import { copyText } from '../lib/copyText';
import { ErrorNotice } from './ui/ErrorNotice';
import { ThemePicker } from './ThemePicker';
import { PageHeader } from './ui/PageHeader';

interface SettingsProps {
  profile: UserProfile;
  onUpdate: (profile: UserProfile) => void;
  // P2 #26 commit 2: onClose dropped; pulled from uiStore directly.
  // P2 #26 commit 3: isAdmin + personas dropped; pulled from userStore.
}

function formatDate(iso: string | null): string {
  if (!iso) return 'Never';
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return String(n);
}

// Only for browsers without Intl.supportedValuesOf.
const COMMON_TIMEZONES = [
  'Europe/Berlin', 'Europe/London', 'Europe/Paris', 'Europe/Amsterdam',
  'Europe/Zurich', 'Europe/Vienna', 'Europe/Rome', 'Europe/Madrid',
  'Europe/Stockholm', 'Europe/Helsinki', 'Europe/Warsaw', 'Europe/Prague',
  'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
  'Asia/Tokyo', 'Asia/Shanghai', 'Asia/Singapore', 'Asia/Kolkata',
  'Australia/Sydney', 'Pacific/Auckland',
];

// Chrome leaves UTC out of supportedValuesOf.
const TIMEZONES = [...new Set([
  'UTC',
  ...(typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone') : COMMON_TIMEZONES),
])];

// The picture is stored as a data URL in the profile, so it has to stay small.
const AVATAR_LIMIT_BYTES = 150_000;

type CopyState = 'idle' | 'copied' | 'failed';

function deviceTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || '';
  } catch {
    return '';
  }
}

export function Settings({ profile, onUpdate }: SettingsProps) {
  // The served backbone for the quick-start snippet; /api/status already
  // carries it. Falls back to the reference model while loading.
  const status = useStatus(300000);
  const servedModel = status?.vllm?.model || 'qwen3.8-27b';
  const quickStart = `from openai import OpenAI

client = OpenAI(
    api_key="sk-munin-...",
    base_url="${API_PUBLIC_URL}"
)

response = client.chat.completions.create(
    model="${servedModel}",
    messages=[{"role": "user", "content": "Hello!"}]
)

print(response.choices[0].message.content)`;
  const fieldId = useId();
  const setShowSettings = useUiStore(s => s.setShowSettings);
  const onClose = () => setShowSettings(false);
  const personas = useUserStore(s => s.personas);
  const [fullName, setFullName] = useState(profile.full_name);
  const [nickname, setNickname] = useState(profile.nickname);
  const [avatar, setAvatar] = useState(profile.avatar);
  // The picture as last saved, to flag a change that still needs Save.
  const [savedAvatar, setSavedAvatar] = useState(profile.avatar);
  const [avatarError, setAvatarError] = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);

  // API Keys state
  const [apiKeys, setApiKeys] = useState<ApiKeyInfo[]>([]);
  const [newKeyName, setNewKeyName] = useState('');
  const [createdKey, setCreatedKey] = useState<string | null>(null);
  const [creatingKey, setCreatingKey] = useState(false);
  const [copyState, setCopyState] = useState<CopyState>('idle');
  const [snippetCopyState, setSnippetCopyState] = useState<CopyState>('idle');
  const [keysLoad, setKeysLoad] = useState<'loading' | 'ready' | 'failed'>('loading');
  const [keyError, setKeyError] = useState('');

  // Usage state
  const [usage, setUsage] = useState<UsageStats | null>(null);

  // Munin profile state
  const [muninProfile, setMuninProfile] = useState<MuninProfile | null>(null);
  const [aboutMe, setAboutMe] = useState('');
  const [responseFormat, setResponseFormat] = useState('');
  const [defaultPersona, setDefaultPersona] = useState('');
  const [timezone, setTimezone] = useState('');
  const [muninSaving, setMuninSaving] = useState(false);
  const [muninSaved, setMuninSaved] = useState(false);
  const [muninError, setMuninError] = useState('');
  // Saving writes all four fields, so it stays off until they hold what the
  // server has: saving a form that never loaded would wipe the profile.
  const [muninLoad, setMuninLoad] = useState<'loading' | 'ready' | 'failed'>('loading');
  const [muninReloadKey, setMuninReloadKey] = useState(0);

  // Three independent reads, each owning its own failure so one server
  // problem cannot blank the whole page. They live in the effects rather
  // than in callbacks the effects call, which means no setState runs
  // synchronously when an effect fires (react-hooks/set-state-in-effect)
  // and a response arriving after unmount is dropped instead of warned
  // about.
  //
  // The key list is the only read anything repeats, after a key is created
  // or revoked, so it gets a reload key of its own. `loadKeys` keeps its
  // name and its call sites: it now asks for another read rather than
  // performing one.
  const [keysReloadKey, setKeysReloadKey] = useState(0);
  const loadKeys = useCallback(() => setKeysReloadKey(k => k + 1), []);

  useEffect(() => {
    let cancelled = false;
    fetchApiKeys()
      .then(res => {
        if (cancelled) return;
        setApiKeys(res.keys);
        setKeysLoad('ready');
      })
      .catch(() => { if (!cancelled) setKeysLoad('failed'); });
    return () => { cancelled = true; };
  }, [keysReloadKey]);

  useEffect(() => {
    let cancelled = false;
    fetchMuninProfile()
      .then(p => {
        if (cancelled) return;
        setMuninLoad('ready');
        if (!p) return;  // No profile yet: the empty form is correct.
        setMuninProfile(p);
        setAboutMe(p.about_me || '');
        setResponseFormat(p.response_format || '');
        setDefaultPersona(p.default_persona || '');
        setTimezone(p.timezone || '');
      })
      .catch(() => { if (!cancelled) setMuninLoad('failed'); });
    return () => { cancelled = true; };
  }, [muninReloadKey]);

  useEffect(() => {
    let cancelled = false;
    fetchUsageStats()
      .then(res => { if (!cancelled) setUsage(res); })
      .catch(() => { /* silently fail */ });
    return () => { cancelled = true; };
  }, []);

  const retryKeysLoad = () => {
    setKeysLoad('loading');
    loadKeys();
  };

  const retryMuninLoad = () => {
    setMuninLoad('loading');
    setMuninReloadKey(k => k + 1);
  };

  const handleAvatarChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    // Cleared so that choosing the same file again still fires a change.
    e.target.value = '';
    if (!file) return;
    if (file.size > AVATAR_LIMIT_BYTES) {
      setAvatarError(`That image is ${Math.ceil(file.size / 1000)} KB. Choose one up to 150 KB.`);
      return;
    }
    setAvatarError('');
    const reader = new FileReader();
    reader.onload = () => {
      setAvatar(reader.result as string);
    };
    reader.readAsDataURL(file);
  };

  const handleSave = async () => {
    setSaving(true);
    setSaved(false);
    setSaveError('');
    try {
      const updated = await updateProfile({
        full_name: fullName,
        nickname: nickname,
        avatar: avatar,
      });
      onUpdate(updated);
      setSavedAvatar(avatar);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch {
      setSaveError('Could not save your changes. Please try again.');
    }
    setSaving(false);
  };

  const handleMuninSave = async () => {
    setMuninSaving(true);
    setMuninSaved(false);
    setMuninError('');
    try {
      const updated = await updateMuninProfile({
        about_me: aboutMe || null,
        response_format: responseFormat || null,
        default_persona: defaultPersona || null,
        timezone: timezone || null,
      });
      setMuninProfile(updated);
      setMuninSaved(true);
      setTimeout(() => setMuninSaved(false), 2000);
    } catch (e) {
      setMuninError(e instanceof Error ? e.message : 'Failed to save');
    }
    setMuninSaving(false);
  };

  const handleMuninReset = async () => {
    if (!confirm('Reset your Munin profile? This clears all preferences.')) return;
    setMuninError('');
    try {
      await deleteMuninProfile();
      setAboutMe('');
      setResponseFormat('');
      setDefaultPersona('');
      setTimezone('');
      setMuninProfile(null);
    } catch {
      setMuninError('Could not reset your profile. Please try again.');
    }
  };

  const handleCreateKey = async () => {
    if (creatingKey) return;
    setCreatingKey(true);
    setCreatedKey(null);
    setCopyState('idle');
    setKeyError('');
    try {
      const res = await createApiKey(newKeyName.trim() || 'Untitled');
      setCreatedKey(res.key);
      setNewKeyName('');
      loadKeys();
    } catch {
      setKeyError('Could not create the key. Please try again.');
    }
    setCreatingKey(false);
  };

  const handleRevokeKey = async (id: string) => {
    if (!confirm('Revoke this API key? It will stop working immediately.')) return;
    setKeyError('');
    try {
      await revokeApiKey(id);
      loadKeys();
    } catch {
      setKeyError('Could not revoke the key, so it still works. Please try again.');
    }
  };

  const handleCopyKey = async () => {
    if (!createdKey) return;
    if (await copyText(createdKey)) {
      setCopyState('copied');
      setTimeout(() => setCopyState(s => (s === 'copied' ? 'idle' : s)), 2000);
    } else {
      setCopyState('failed');
    }
  };

  const handleCopySnippet = async () => {
    if (await copyText(quickStart)) {
      setSnippetCopyState('copied');
      setTimeout(() => setSnippetCopyState(s => (s === 'copied' ? 'idle' : s)), 2000);
    } else {
      setSnippetCopyState('failed');
    }
  };

  const handleKeySaved = () => {
    setCreatedKey(null);
    setCopyState('idle');
  };

  const deviceZone = deviceTimezone();
  const initials = (fullName || profile.email)[0].toUpperCase();
  const activeKeys = apiKeys.filter(k => !k.revoked);
  const revokedKeys = apiKeys.filter(k => k.revoked);
  // The gateway counts only API-key traffic against the limit. `requests`
  // also counts browser chat, so the API-only figure is used when present.
  const apiRequests = usage
    ? usage.current_month.api_requests ?? usage.current_month.requests
    : 0;
  const usagePercent = usage && usage.current_month.tokens_limit > 0
    ? Math.min(100, (usage.current_month.tokens_used / usage.current_month.tokens_limit) * 100)
    : 0;

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-xl mx-auto px-6 py-10">
        <PageHeader title="Settings" onBack={onClose} className="mb-8" />

        {/* ── Appearance ── */}
        <h3 className="text-lg font-semibold text-text-primary mb-6">Appearance</h3>
        <div className="mb-10">
          <ThemePicker />
        </div>

        {/* ── Account Section ── */}
        <h3 className="text-lg font-semibold text-text-primary mb-6">Account</h3>

        {/* Profile picture */}
        <div className="mb-8">
          <p className="text-sm text-text-secondary mb-3">Profile picture</p>
          <div className="flex items-center gap-4">
            <button
              onClick={() => fileRef.current?.click()}
              aria-label="Change profile picture"
              className="relative w-16 h-16 rounded-full overflow-hidden bg-accent/20 flex items-center justify-center cursor-pointer hover:opacity-80 transition-opacity"
            >
              {avatar ? (
                <img src={avatar} alt="" className="w-full h-full object-cover" />
              ) : (
                <span className="text-accent text-xl font-semibold">{initials}</span>
              )}
            </button>
            <div>
              <button
                onClick={() => fileRef.current?.click()}
                className="text-sm text-accent hover:text-accent-hover transition-colors cursor-pointer"
              >
                Upload photo
              </button>
              <p className="text-[11px] text-text-secondary mt-1">Image up to 150 KB</p>
              {avatar && (
                <button
                  onClick={() => { setAvatar(''); setAvatarError(''); }}
                  aria-label="Remove profile picture"
                  className="block text-xs text-text-secondary hover:text-error transition-colors cursor-pointer mt-1"
                >
                  Remove
                </button>
              )}
            </div>
            <input
              ref={fileRef}
              type="file"
              accept="image/*"
              onChange={handleAvatarChange}
              className="hidden"
            />
          </div>
          {avatarError && <p role="alert" className="text-xs text-error mt-2">{avatarError}</p>}
          {avatar !== savedAvatar && (
            <p className="text-xs text-warning mt-2">Not saved yet. Use Save below.</p>
          )}
        </div>

        {/* Full name */}
        <div className="mb-6">
          <label htmlFor={`${fieldId}-full-name`} className="block text-sm text-text-secondary mb-2">Full name</label>
          <input
            id={`${fieldId}-full-name`}
            type="text"
            value={fullName}
            onChange={e => setFullName(e.target.value)}
            placeholder="Your full name"
            className="w-full px-3 py-2 bg-bg-tertiary border border-border rounded-lg text-sm text-text-primary placeholder-text-secondary outline-none focus:border-accent transition-colors"
          />
        </div>

        {/* Nickname */}
        <div className="mb-8">
          <label htmlFor={`${fieldId}-nickname`} className="block text-sm text-text-secondary mb-2">What should Munin call you?</label>
          <input
            id={`${fieldId}-nickname`}
            type="text"
            value={nickname}
            onChange={e => setNickname(e.target.value)}
            placeholder="A name, nickname, whatever you like"
            className="w-full px-3 py-2 bg-bg-tertiary border border-border rounded-lg text-sm text-text-primary placeholder-text-secondary outline-none focus:border-accent transition-colors"
          />
          <p className="text-[11px] text-text-secondary mt-1.5">
            This is used in greetings like "Good morning, {nickname || fullName || 'you'}"
          </p>
        </div>

        {/* Email (read-only) */}
        <div className="mb-8">
          <p className="text-sm text-text-secondary mb-2">Email</p>
          <div className="px-3 py-2 bg-bg-tertiary border border-border rounded-lg text-sm text-text-secondary">
            {profile.email}
          </div>
        </div>

        {/* Save */}
        <button
          onClick={handleSave}
          disabled={saving}
          className="px-5 py-2 bg-accent text-bg-primary rounded-lg text-sm font-medium hover:bg-accent-hover transition-colors cursor-pointer disabled:opacity-50"
        >
          {/* Two Save buttons share this page, so each names its section for
              screen readers. */}
          {saving ? 'Saving...' : saved ? 'Saved' : 'Save'}{' '}<span className="sr-only">account</span>
        </button>
        {saveError && <ErrorNotice message={saveError} className="mt-3" />}

        {/* ── Divider ── */}
        <div className="border-t border-border my-10" />

        {/* ── Munin Profile Section ── */}
        <h3 className="text-lg font-semibold text-text-primary mb-2">How Munin works with you</h3>
        <p className="text-sm text-text-secondary mb-6">
          Tell Munin about yourself and how you'd like it to respond. Munin reads this at the start of every chat.
        </p>

        {muninLoad === 'failed' && (
          <ErrorNotice
            message="Could not load your Munin profile. Saving is off until it loads, so your saved preferences can't be overwritten."
            onRetry={retryMuninLoad}
            className="mb-6"
          />
        )}

        {/* About me */}
        <div className="mb-6">
          <label htmlFor={`${fieldId}-about`} className="block text-sm text-text-secondary mb-2">About you</label>
          <textarea
            id={`${fieldId}-about`}
            value={aboutMe}
            onChange={e => setAboutMe(e.target.value)}
            disabled={muninLoad !== 'ready'}
            placeholder="e.g. I'm a biophysics PhD candidate working on lipid bilayers. I use molecular dynamics simulations."
            rows={3}
            maxLength={1500}
            className="w-full px-3 py-2 bg-bg-tertiary border border-border rounded-lg text-sm text-text-primary placeholder-text-secondary outline-none focus:border-accent transition-colors resize-none disabled:opacity-50"
          />
          <p className="text-[11px] text-text-secondary mt-1 text-right">{aboutMe.length} / 1500</p>
        </div>

        {/* Response format */}
        <div className="mb-6">
          <label htmlFor={`${fieldId}-response`} className="block text-sm text-text-secondary mb-2">How should Munin respond?</label>
          <textarea
            id={`${fieldId}-response`}
            value={responseFormat}
            onChange={e => setResponseFormat(e.target.value)}
            disabled={muninLoad !== 'ready'}
            placeholder="e.g. Always cite DOIs. Use British spelling. Keep answers concise. Prefer equations over prose."
            rows={3}
            maxLength={1500}
            className="w-full px-3 py-2 bg-bg-tertiary border border-border rounded-lg text-sm text-text-primary placeholder-text-secondary outline-none focus:border-accent transition-colors resize-none disabled:opacity-50"
          />
          <p className="text-[11px] text-text-secondary mt-1 text-right">{responseFormat.length} / 1500</p>
        </div>

        {/* Default persona */}
        {personas.length > 0 && (
          <div className="mb-6">
            <label htmlFor={`${fieldId}-persona`} className="block text-sm text-text-secondary mb-2">Default persona</label>
            <p id={`${fieldId}-persona-help`} className="text-[11px] text-text-secondary mb-2">
              A persona is a preset way for Munin to work, such as general chat or coding. New chats start with this one. A project can set its own.
            </p>
            <select
              id={`${fieldId}-persona`}
              aria-describedby={`${fieldId}-persona-help`}
              value={defaultPersona}
              onChange={e => setDefaultPersona(e.target.value)}
              disabled={muninLoad !== 'ready'}
              className="w-full px-3 py-2 bg-bg-tertiary border border-border rounded-lg text-sm text-text-primary outline-none focus:border-accent transition-colors cursor-pointer disabled:opacity-50"
            >
              <option value="">None (Munin picks for each chat)</option>
              {personas.map(p => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>
        )}

        {/* Timezone */}
        <div className="mb-8">
          <label htmlFor={`${fieldId}-timezone`} className="block text-sm text-text-secondary mb-2">Timezone</label>
          <select
            id={`${fieldId}-timezone`}
            value={timezone}
            onChange={e => setTimezone(e.target.value)}
            disabled={muninLoad !== 'ready'}
            className="w-full px-3 py-2 bg-bg-tertiary border border-border rounded-lg text-sm text-text-primary outline-none focus:border-accent transition-colors cursor-pointer disabled:opacity-50"
          >
            <option value="">Not set</option>
            {deviceZone && <option value={deviceZone}>{deviceZone} (this device)</option>}
            {/* A stored zone the list doesn't have, so the control still shows it. */}
            {timezone && timezone !== deviceZone && !TIMEZONES.includes(timezone) && (
              <option value={timezone}>{timezone}</option>
            )}
            {TIMEZONES.filter(tz => tz !== deviceZone).map(tz => (
              <option key={tz} value={tz}>{tz}</option>
            ))}
          </select>
        </div>

        {/* Munin profile save / reset */}
        <div className="flex items-center gap-3">
          <button
            onClick={handleMuninSave}
            disabled={muninSaving || muninLoad !== 'ready'}
            className="px-5 py-2 bg-accent text-bg-primary rounded-lg text-sm font-medium hover:bg-accent-hover transition-colors cursor-pointer disabled:opacity-50"
          >
            {muninSaving ? 'Saving...' : muninSaved ? 'Saved' : 'Save'}{' '}<span className="sr-only">Munin profile</span>
          </button>
          {muninProfile && (
            <button
              onClick={handleMuninReset}
              className="px-4 py-2 text-sm text-text-secondary hover:text-error transition-colors cursor-pointer"
            >
              Reset
            </button>
          )}
        </div>
        {muninError && <ErrorNotice message={muninError} className="mt-3" />}

        {/* ── Divider ── */}
        <div className="border-t border-border my-10" />

        {/* ── API Keys Section ── */}
        <h3 className="text-lg font-semibold text-text-primary mb-2">API Keys</h3>
        <p className="text-sm text-text-secondary mb-4">
          Use API keys to access Munin from scripts, notebooks, or any OpenAI-compatible client. Keys are tied to your email ({profile.email}).
        </p>
        <div className="text-xs text-text-secondary mb-6 flex flex-wrap gap-x-4 gap-y-1">
          {usage && usage.current_month.tokens_limit > 0 && (
            <span>{formatTokens(usage.current_month.tokens_limit)} tokens/month</span>
          )}
          <span>No request-rate or concurrency limit</span>
        </div>

        {/* Usage bar */}
        {usage && (
          <div className="mb-6 p-4 bg-bg-secondary border border-border rounded-lg">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm text-text-secondary">Monthly API usage (browser chat is not counted)</span>
              <span className="text-sm text-text-primary">
                {formatTokens(usage.current_month.tokens_used)} / {formatTokens(usage.current_month.tokens_limit)} tokens
              </span>
            </div>
            <div
              role="progressbar"
              aria-label="Monthly token usage"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(usagePercent)}
              className="w-full h-2 bg-bg-tertiary rounded-full overflow-hidden"
            >
              <div
                className={`h-full rounded-full transition-all ${usagePercent > 90 ? 'bg-error' : usagePercent > 70 ? 'bg-warning' : 'bg-accent'}`}
                style={{ width: `${usagePercent}%` }}
              />
            </div>
            <div className="flex items-center gap-4 mt-2 text-xs text-text-secondary">
              <span>{apiRequests} API requests this month</span>
            </div>
          </div>
        )}

        {/* Create new key */}
        <div className="mb-6">
          <div className="flex gap-2">
            <input
              type="text"
              value={newKeyName}
              onChange={e => setNewKeyName(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleCreateKey()}
              placeholder="Key name (e.g. my laptop, research script)"
              aria-label="API key name"
              className="flex-1 px-3 py-2 bg-bg-tertiary border border-border rounded-lg text-sm text-text-primary placeholder-text-secondary outline-none focus:border-accent transition-colors"
            />
            <button
              onClick={handleCreateKey}
              disabled={creatingKey}
              className="px-4 py-2 bg-accent text-bg-primary rounded-lg text-sm font-medium hover:bg-accent-hover transition-colors cursor-pointer disabled:opacity-50 flex-shrink-0"
            >
              {creatingKey ? 'Creating...' : 'Create key'}
            </button>
          </div>
          {keyError && <ErrorNotice message={keyError} className="mt-3" />}
        </div>

        {/* Newly created key banner */}
        {createdKey && (
          <div className="mb-6 p-4 bg-accent/10 border border-accent rounded-lg">
            <p className="text-sm font-medium text-text-primary mb-2">Your new API key (save it now, it won't be shown again):</p>
            <div className="flex items-center gap-2">
              <code className="flex-1 text-xs bg-bg-primary px-3 py-2 rounded border border-border text-text-primary break-all select-all">
                {createdKey}
              </code>
              <button
                onClick={handleCopyKey}
                className="px-3 py-2 bg-bg-primary border border-border rounded-lg text-xs text-text-secondary hover:text-text-primary transition-colors cursor-pointer flex-shrink-0"
              >
                {copyState === 'copied' ? 'Copied!' : 'Copy'}
              </button>
            </div>
            <div className="flex items-center justify-between gap-3 mt-3">
              <p role="status" className="text-xs text-error">
                {copyState === 'failed' && 'Copy failed. Select the key above and copy it by hand.'}
              </p>
              <button
                onClick={handleKeySaved}
                className="px-3 py-2 bg-bg-primary border border-border rounded-lg text-xs text-text-secondary hover:text-text-primary transition-colors cursor-pointer flex-shrink-0"
              >
                Done
              </button>
            </div>
          </div>
        )}

        {/* Active keys list */}
        {activeKeys.length > 0 && (
          <div className="mb-6">
            <div className="border border-border rounded-lg overflow-hidden">
              {activeKeys.map((key, i) => (
                <div
                  key={key.id}
                  className={`flex items-center gap-3 px-4 py-3 ${i > 0 ? 'border-t border-border' : ''}`}
                >
                  <div className="flex-1 min-w-0">
                    <div className="text-sm text-text-primary">{key.name || 'Untitled'}</div>
                    <div className="text-xs text-text-secondary mt-0.5">
                      <code>{key.key_prefix}...</code>
                      <span className="mx-2">|</span>
                      Created {formatDate(key.created_at)}
                      {key.last_used_at && (
                        <>
                          <span className="mx-2">|</span>
                          Last used {formatDate(key.last_used_at)}
                        </>
                      )}
                    </div>
                  </div>
                  <button
                    onClick={() => handleRevokeKey(key.id)}
                    aria-label={`Revoke ${key.name || 'Untitled'}`}
                    className="text-xs text-text-secondary hover:text-error transition-colors cursor-pointer flex-shrink-0"
                  >
                    Revoke
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {keysLoad === 'failed' && (
          <ErrorNotice message="Could not load your API keys." onRetry={retryKeysLoad} className="mb-6" />
        )}

        {keysLoad === 'ready' && activeKeys.length === 0 && !createdKey && (
          <p className="text-sm text-text-secondary mb-6">No API keys yet. Create one to get started.</p>
        )}

        {/* Revoked keys (collapsed) */}
        {revokedKeys.length > 0 && (
          <details className="mb-6">
            <summary className="text-xs text-text-secondary cursor-pointer hover:text-text-primary transition-colors">
              {revokedKeys.length} revoked key{revokedKeys.length > 1 ? 's' : ''}
            </summary>
            <div className="mt-2 border border-border rounded-lg overflow-hidden opacity-60">
              {revokedKeys.map((key, i) => (
                <div
                  key={key.id}
                  className={`flex items-center gap-3 px-4 py-3 ${i > 0 ? 'border-t border-border' : ''}`}
                >
                  <div className="flex-1 min-w-0">
                    <div className="text-sm text-text-secondary line-through">{key.name || 'Untitled'}</div>
                    <div className="text-xs text-text-secondary mt-0.5">
                      <code>{key.key_prefix}...</code>
                    </div>
                  </div>
                  <span className="text-xs text-error">Revoked</span>
                </div>
              ))}
            </div>
          </details>
        )}

        {/* Quick start snippet */}
        <div className="mb-6">
          <div className="flex items-center justify-between gap-3 mb-3">
            <h4 className="text-sm font-medium text-text-primary">Quick start</h4>
            <button
              onClick={handleCopySnippet}
              aria-label={snippetCopyState === 'copied' ? 'Copied quick start code' : 'Copy quick start code'}
              className="px-3 py-1 bg-bg-primary border border-border rounded-lg text-xs text-text-secondary hover:text-text-primary transition-colors cursor-pointer flex-shrink-0"
            >
              {snippetCopyState === 'copied' ? 'Copied!' : 'Copy'}
            </button>
          </div>
          {/* Wrapped rather than scrolled, so the whole call shows on a phone. */}
          <pre className="bg-bg-secondary border border-border rounded-lg p-4 text-xs text-text-secondary whitespace-pre-wrap wrap-anywhere">{quickStart}</pre>
          <p role="status" className="text-xs text-error mt-2">
            {snippetCopyState === 'failed' && 'Copy failed. Select the code above and copy it by hand.'}
          </p>
        </div>
      </div>
    </div>
  );
}
