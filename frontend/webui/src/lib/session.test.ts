import { http, HttpResponse } from 'msw';
import { server } from '../test/msw-server';
import { AUTH_BASE } from './urls';
import {
  SESSION_CHECK_INTERVAL_MS,
  _resetSessionForTests,
  classifyApiFailure,
  isSessionEnded,
  reportApiFailure,
  shouldCheckSession,
  signInAgainUrl,
  subscribeSession,
} from './session';

// Counts the session checks and answers them with `status`.
function mockMe(status: number | 'network') {
  const calls = { count: 0 };
  server.use(http.get(`${AUTH_BASE}/auth/me`, () => {
    calls.count += 1;
    if (status === 'network') return HttpResponse.error();
    return status === 200 ? HttpResponse.json({ email: 'a@b.c' }) : new HttpResponse(null, { status });
  }));
  return calls;
}

beforeEach(() => _resetSessionForTests());
afterEach(() => vi.useRealTimers());

describe('classifyApiFailure', () => {
  it('reads a fetch TypeError as a network failure', () => {
    expect(classifyApiFailure(new TypeError('Failed to fetch'))).toBe('network');
  });

  it('reads a 401 response as unauthorized', () => {
    expect(classifyApiFailure(new Response(null, { status: 401 }))).toBe('unauthorized');
  });

  it('ignores aborts, other errors and other statuses', () => {
    expect(classifyApiFailure(new DOMException('Aborted', 'AbortError'))).toBeNull();
    expect(classifyApiFailure(new Error('boom'))).toBeNull();
    expect(classifyApiFailure(new Response(null, { status: 403 }))).toBeNull();
    expect(classifyApiFailure(new Response(null, { status: 500 }))).toBeNull();
    expect(classifyApiFailure(new Response('{}', { status: 200 }))).toBeNull();
  });
});

describe('shouldCheckSession', () => {
  const base = { ended: false, checking: false, lastCheckAt: Number.NEGATIVE_INFINITY, now: 100_000 };

  it('checks the first time', () => {
    expect(shouldCheckSession(base)).toBe(true);
  });

  it('waits out the interval after a check', () => {
    expect(shouldCheckSession({ ...base, lastCheckAt: base.now - SESSION_CHECK_INTERVAL_MS + 1 })).toBe(false);
    expect(shouldCheckSession({ ...base, lastCheckAt: base.now - SESSION_CHECK_INTERVAL_MS })).toBe(true);
  });

  it('never checks while a check runs or once the session has ended', () => {
    expect(shouldCheckSession({ ...base, checking: true })).toBe(false);
    expect(shouldCheckSession({ ...base, ended: true })).toBe(false);
  });
});

describe('reportApiFailure', () => {
  it('ends the session when /auth/me answers 401, and tells subscribers', async () => {
    mockMe(401);
    const onChange = vi.fn();
    subscribeSession(onChange);

    reportApiFailure('network');

    await vi.waitFor(() => expect(isSessionEnded()).toBe(true));
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it('leaves the session alone when /auth/me still answers', async () => {
    const calls = mockMe(200);
    reportApiFailure('unauthorized');
    await vi.waitFor(() => expect(calls.count).toBe(1));
    await new Promise(r => setTimeout(r, 0));
    expect(isSessionEnded()).toBe(false);
  });

  it('leaves the session alone when /auth/me cannot be reached or fails otherwise', async () => {
    const calls = mockMe('network');
    reportApiFailure('network');
    await vi.waitFor(() => expect(calls.count).toBe(1));
    await new Promise(r => setTimeout(r, 0));
    expect(isSessionEnded()).toBe(false);
  });

  it('does nothing for a failure that says nothing about the session', async () => {
    const calls = mockMe(401);
    reportApiFailure(null);
    await new Promise(r => setTimeout(r, 20));
    expect(calls.count).toBe(0);
    expect(isSessionEnded()).toBe(false);
  });

  it('checks once for a burst of failures, and again only after the interval', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    const calls = mockMe(200);

    reportApiFailure('network');
    reportApiFailure('network');
    reportApiFailure('unauthorized');
    await vi.waitFor(() => expect(calls.count).toBe(1));

    vi.setSystemTime(Date.now() + SESSION_CHECK_INTERVAL_MS - 1000);
    reportApiFailure('network');
    await new Promise(r => setTimeout(r, 20));
    expect(calls.count).toBe(1);

    vi.setSystemTime(Date.now() + 1000);
    reportApiFailure('network');
    await vi.waitFor(() => expect(calls.count).toBe(2));
  });
});

describe('signInAgainUrl', () => {
  it('sends the user back to the page they were on', () => {
    expect(signInAgainUrl('https://chat.example.org/c/abc?x=1')).toBe(
      `${AUTH_BASE}/login?redirect=${encodeURIComponent('https://chat.example.org/c/abc?x=1')}`,
    );
  });
});
