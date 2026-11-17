import { useEffect, useId, useRef, useState } from 'react';
import {
  confirmEmailCode,
  fetchCodeMailState,
  fetchMyEmails,
  makeMyEmailPrimary,
  removeMyEmail,
  requestEmailCode,
  type MyEmails,
} from '../lib/api';

const inputClass =
  'flex-1 min-w-0 px-3 py-2 bg-bg-tertiary border border-border rounded-lg text-sm text-text-primary placeholder-text-secondary outline-none focus:border-text-secondary transition-colors';
const buttonClass =
  'text-xs px-3 py-2 bg-accent/15 text-accent hover:bg-accent/25 rounded-lg cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed';
const linkClass = 'text-[11px] text-text-secondary hover:text-text-primary cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed';

type Pending = { email: string; send: string; minutes: number; mail: 'sending' | 'sent' | 'failed' | 'unknown' };

/** The addresses a user can sign in with. Any of them opens the same
 *  account; adding one needs a code mailed to it. */
export function EmailAddresses({ fallbackEmail }: { fallbackEmail: string }) {
  const id = useId();
  const [data, setData] = useState<MyEmails | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [newEmail, setNewEmail] = useState('');
  const [code, setCode] = useState('');
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    fetchMyEmails().then(setData).catch(() => setLoadFailed(true));
    return () => { alive.current = false; };
  }, []);

  // Ask how the code mail went, so a failed send is said, not waited out.
  useEffect(() => {
    if (!pending || pending.mail !== 'sending') return;
    let tries = 0;
    const t = setInterval(async () => {
      tries += 1;
      const state = await fetchCodeMailState(pending.send).catch(() => 'unknown' as const);
      if (!alive.current) return;
      if (state !== 'sending' || tries >= 20) {
        clearInterval(t);
        setPending(p => (p && p.send === pending.send ? { ...p, mail: state } : p));
      }
    }, 1500);
    return () => clearInterval(t);
  }, [pending]);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Something went wrong. Please try again.');
    } finally {
      setBusy(false);
    }
  };

  const sendCode = (email: string) => run(async () => {
    const r = await requestEmailCode(email);
    setPending({ email: r.email, send: r.send, minutes: r.expiry_minutes, mail: 'sending' });
    setCode('');
  });

  const confirm = () => run(async () => {
    if (!pending) return;
    setData(await confirmEmailCode(pending.email, code));
    setNotice(`${pending.email} added. You can now sign in with it.`);
    setPending(null);
    setNewEmail('');
    setCode('');
  });

  const remove = (email: string) => {
    if (!window.confirm(`Remove ${email}? It will no longer sign in to your account. Your chats and files stay.`)) return;
    run(async () => setData(await removeMyEmail(email)));
  };

  const makePrimary = (email: string) => run(async () => setData(await makeMyEmailPrimary(email)));

  if (loadFailed || !data) {
    return (
      <div className="mb-8">
        <p className="text-sm text-text-secondary mb-2">Email</p>
        <div className="px-3 py-2 bg-bg-tertiary border border-border rounded-lg text-sm text-text-secondary">
          {fallbackEmail}
        </div>
        {loadFailed && (
          <p className="text-[11px] text-text-secondary mt-1.5">Could not load your other addresses. Reload to manage them.</p>
        )}
      </div>
    );
  }

  const full = data.emails.length >= data.max;

  return (
    <div className="mb-8">
      <p className="text-sm text-text-secondary mb-1">Email addresses</p>
      <p className="text-[11px] text-text-secondary mb-3">
        Sign in with any of these; they all open the same account. Mail from Munin goes to the primary one.
      </p>
      <ul className="space-y-1.5 mb-3">
        {data.emails.map(e => (
          <li key={e.email} className="flex flex-wrap items-center gap-2 px-3 py-2 bg-bg-tertiary border border-border rounded-lg text-sm">
            <span className="font-mono text-xs text-text-primary flex-1 min-w-0 break-all">{e.email}</span>
            {e.primary && (
              <span className="text-[10px] uppercase tracking-wide text-accent px-1.5 py-0.5 bg-accent/15 rounded">Primary</span>
            )}
            {e.current && <span className="text-[11px] text-text-secondary">signed in</span>}
            {!e.primary && (
              <button type="button" className={linkClass} disabled={busy} onClick={() => makePrimary(e.email)}>
                Make primary
              </button>
            )}
            <button
              type="button"
              className={`${linkClass} hover:text-error`}
              disabled={busy || e.current || data.emails.length === 1}
              title={e.current ? 'You are signed in with this address' : data.emails.length === 1 ? 'Your only address' : 'Remove'}
              onClick={() => remove(e.email)}
            >
              Remove
            </button>
          </li>
        ))}
      </ul>

      {pending ? (
        <form
          className="space-y-2"
          onSubmit={ev => { ev.preventDefault(); confirm(); }}
        >
          <p className="text-[11px] text-text-secondary">
            {pending.mail === 'failed'
              ? `We could not send the email to ${pending.email}. Check the address, or try again in a minute.`
              : `We sent a 6-digit code to ${pending.email}. It expires in ${pending.minutes} minutes and does not sign you in; it only confirms the address.`}
          </p>
          <div className="flex items-center gap-2">
            <label htmlFor={`${id}-code`} className="sr-only">Confirmation code</label>
            <input
              id={`${id}-code`}
              inputMode="numeric"
              autoComplete="one-time-code"
              value={code}
              onChange={ev => setCode(ev.target.value)}
              placeholder="123456"
              className={inputClass}
            />
            <button type="submit" className={buttonClass} disabled={busy || code.replace(/\D/g, '').length !== 6}>
              Confirm
            </button>
          </div>
          <div className="flex gap-3">
            <button type="button" className={linkClass} disabled={busy} onClick={() => sendCode(pending.email)}>
              Send again
            </button>
            <button type="button" className={linkClass} disabled={busy} onClick={() => { setPending(null); setError(''); }}>
              Cancel
            </button>
          </div>
        </form>
      ) : full ? (
        <p className="text-[11px] text-text-secondary">You have the maximum of {data.max} addresses.</p>
      ) : (
        <form
          className="flex items-center gap-2"
          onSubmit={ev => { ev.preventDefault(); sendCode(newEmail.trim()); }}
        >
          <label htmlFor={`${id}-new`} className="sr-only">Add an email address</label>
          <input
            id={`${id}-new`}
            type="email"
            value={newEmail}
            onChange={ev => setNewEmail(ev.target.value)}
            placeholder="Add another address"
            className={inputClass}
          />
          <button type="submit" className={buttonClass} disabled={busy || !newEmail.includes('@')}>
            Send code
          </button>
        </form>
      )}

      {error && <p role="alert" className="text-[11px] text-error mt-2">{error}</p>}
      {notice && <p role="status" className="text-[11px] text-text-secondary mt-2">{notice}</p>}
    </div>
  );
}
