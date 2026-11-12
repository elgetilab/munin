import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { cookieDomain, readPref } from './lib/theme';

// Outside the Vite root, so a ?raw import is refused. Vitest runs from frontend/webui.
const readStatic = (path: string) => readFileSync(resolve(process.cwd(), '../static', path), 'utf-8');
const searchHtml = readStatic('search/index.html');
const uploadHtml = readStatic('upload/index.html');

// These pages are plain HTML with no build step, so each test loads the real
// file into jsdom and runs its inline script against a stubbed fetch.
function fillEnv(source: string) {
  return source.replace(/\{\{env "([A-Z_]+)"\}\}/g, (_m, name: string) =>
    name === 'MUNIN_SUPPORT_EMAIL' ? 'support@example.org' : `https://${name.toLowerCase()}.example.org`);
}

function loadPage(html: string, fetchStub: unknown, globals: Record<string, unknown> = {}) {
  const doc = new DOMParser().parseFromString(fillEnv(html), 'text/html');
  document.head.innerHTML = '';
  document.body.innerHTML = doc.body.innerHTML;
  // The last inline script is the page's own.
  const source = [...doc.querySelectorAll('script:not([src])')].pop()!.textContent!;
  const names = Object.keys(globals);
  new Function('fetch', ...names, source)(fetchStub, ...names.map((n) => globals[n]));
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
const field = (id: string) => document.getElementById(id) as HTMLInputElement;

describe('search page', () => {
  const results = () => document.getElementById('results-container')!;
  const errors = () => document.getElementById('error-container')!;
  const submit = () => document.getElementById('search-form')!
    .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));

  const searchGlobals = { createVortex: () => () => {}, createRotatingMessage: () => {} };
  let routes: (url: string, init?: RequestInit) => Response | Promise<Response>;
  const fetchStub = vi.fn((url: string, init?: RequestInit) => {
    if (url === '/api/sources') return Promise.resolve(json({}));
    if (url.endsWith('/pdf/exists')) return Promise.resolve(json({ pdf_available: false }));
    return Promise.resolve(routes(url, init));
  });

  beforeEach(() => {
    fetchStub.mockClear();
    vi.spyOn(console, 'error').mockImplementation(() => {});
    loadPage(searchHtml, fetchStub, searchGlobals);
  });
  afterEach(() => vi.restoreAllMocks());

  describe('DOI lookup', () => {
    it.each([401, 500, 503])('reports a %i as an error, not as an Exact match paper', async (status) => {
      routes = () => json({ detail: 'nope' }, status);
      field('search-input').value = '10.1000/xyz';
      submit();
      await flush();

      expect(results().textContent).not.toContain('Exact match');
      expect(results().textContent).not.toContain('Untitled');
      expect(errors().textContent).toContain(String(status));
    });

    it('still shows a found paper', async () => {
      routes = () => json({ doi: '10.1000/xyz', title: 'A real paper' });
      field('search-input').value = '10.1000/xyz';
      submit();
      await flush();

      expect(results().textContent).toContain('Exact match');
      expect(results().textContent).toContain('A real paper');
    });
  });

  describe('author-only search with a year range', () => {
    const papers = [
      { title: 'Too new', year: 2023, doi: '10.1/a' },
      { title: 'In range', year: 2018, doi: '10.1/b' },
      { title: 'Too old', year: 2001, doi: '10.1/c' },
      { title: 'No year', doi: '10.1/d' },
    ];

    it('keeps only papers in range and names the range', async () => {
      routes = () => json({ papers, authors_found: [] });
      field('author-input').value = 'Curie';
      field('year-min').value = '2015';
      field('year-max').value = '2020';
      submit();
      await flush();

      const text = results().textContent!;
      expect(text).toContain('In range');
      expect(text).not.toContain('Too new');
      expect(text).not.toContain('Too old');
      expect(text).not.toContain('No year');
      expect(text).toContain('by "Curie" (2015-2020)');
    });

    it('asks for more than the display limit, as the API returns newest first', async () => {
      routes = () => json({ papers: [], authors_found: [] });
      field('author-input').value = 'Curie';
      field('year-max').value = '2020';
      submit();
      await flush();

      expect(fetchStub.mock.calls.map(([url]) => url)).toContain('/api/author/Curie/papers?limit=500');
      expect(results().textContent).toContain('(up to 2020)');
    });

    it('says when the range was checked over only part of the list', async () => {
      routes = () => json({ papers: Array.from({ length: 500 }, () => ({ title: 'New', year: 2024 })), authors_found: [] });
      field('author-input').value = 'Curie';
      field('year-max').value = '2020';
      submit();
      await flush();

      expect(results().textContent).toContain('Checked their 500 most recent papers.');
    });

    it('leaves the author search alone without a range', async () => {
      routes = () => json({ papers, authors_found: [] });
      field('author-input').value = 'Curie';
      submit();
      await flush();

      expect(fetchStub.mock.calls.map(([url]) => url)).toContain('/api/author/Curie/papers?limit=10');
      expect(results().textContent).toContain('Too old');
      expect(results().textContent).toContain('No year');
    });
  });

  describe('submitting', () => {
    it('searches when the form is submitted from a filter field', async () => {
      routes = () => json({ papers: [{ title: 'By author', year: 2020 }], authors_found: [] });
      field('author-input').value = 'Curie';
      expect(field('author-input').form).toBe(document.getElementById('search-form'));
      submit();
      await flush();

      expect(results().textContent).toContain('By author');
    });

    it('keeps the newer results when an older search answers last', async () => {
      const answers: ((r: Response) => void)[] = [];
      const signals: (AbortSignal | null | undefined)[] = [];
      routes = (_url, init) => {
        signals.push(init?.signal);
        return new Promise<Response>((resolve) => answers.push(resolve));
      };

      field('search-input').value = 'older';
      submit();
      field('search-input').value = 'newer';
      submit();
      await flush();

      expect(signals[0]?.aborted).toBe(true);
      expect(signals[1]?.aborted).toBe(false);

      answers[1](json({ papers: [{ title: 'Newer paper' }] }));
      await flush();
      answers[0](json({ papers: [{ title: 'Older paper' }] }));
      await flush();

      expect(results().textContent).toContain('Newer paper');
      expect(results().textContent).not.toContain('Older paper');
    });

    it('shows no error when an aborted search rejects', async () => {
      routes = (_url, init) => new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
      });
      field('search-input').value = 'older';
      submit();
      routes = () => json({ papers: [{ title: 'Newer paper' }] });
      field('search-input').value = 'newer';
      submit();
      await flush();

      expect(errors().textContent).toBe('');
      expect(results().textContent).toContain('Newer paper');
    });
  });
});

describe('search page, usability', () => {
  const results = () => document.getElementById('results-container')!;
  const errors = () => document.getElementById('error-container')!;
  const summary = () => document.getElementById('results-summary')!;
  const submit = () => document.getElementById('search-form')!
    .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  const searchGlobals = { createVortex: () => () => {}, createRotatingMessage: () => {} };

  function load(routes: (url: string) => Response | Promise<Response>) {
    const stub = vi.fn((url: string) => {
      if (url === '/api/sources') return Promise.resolve(json({}));
      return Promise.resolve(routes(url));
    });
    loadPage(searchHtml, stub, searchGlobals);
    return stub;
  }

  beforeEach(() => { vi.spyOn(console, 'error').mockImplementation(() => {}); });
  afterEach(() => vi.restoreAllMocks());

  const paper = (n: number) => ({ title: `Paper ${n}`, doi: `10.1/${n}`, authors: ['A. Author'] });

  it('shows no link while a PDF check is pending, then fills it in', async () => {
    let answer = (_r: Response) => {};
    load((url) => {
      if (url.endsWith('/pdf/exists')) return new Promise<Response>((resolve) => { answer = resolve; });
      return json({ papers: [paper(1)] });
    });
    field('search-input').value = 'topic';
    submit();
    await flush();

    const button = results().querySelector('.download-btn')!;
    expect(button.classList.contains('checking')).toBe(true);
    expect(button.hasAttribute('href')).toBe(false);

    answer(json({ pdf_available: true }));
    await flush();
    await flush();
    expect(button.getAttribute('href')).toBe('/paper/10.1%2F1/pdf');
    expect(button.classList.contains('checking')).toBe(false);
  });

  it('checks PDFs side by side, up to a limit', async () => {
    let inFlight = 0;
    let peak = 0;
    const answers: (() => void)[] = [];
    load((url) => {
      if (!url.endsWith('/pdf/exists')) return json({ papers: Array.from({ length: 10 }, (_, i) => paper(i)) });
      inFlight += 1;
      peak = Math.max(peak, inFlight);
      return new Promise<Response>((resolve) => {
        answers.push(() => { inFlight -= 1; resolve(json({ pdf_available: false })); });
      });
    });
    field('search-input').value = 'topic';
    submit();
    await flush();

    expect(peak).toBe(6);
    while (answers.length > 0) {
      answers.shift()!();
      await flush();
      await flush();
    }
    expect(results().querySelectorAll('.download-btn.unavailable')).toHaveLength(10);
  });

  it('is honest when the author filter removed every top match, and offers the author search', async () => {
    const stub = load((url) => {
      if (url.endsWith('/pdf/exists')) return json({ pdf_available: false });
      if (url === '/api/search/hybrid') return json({ papers: [paper(1), paper(2)] });
      return json({ papers: [{ title: 'Their paper', year: 2020 }], authors_found: [] });
    });
    field('search-input').value = 'topic';
    field('author-input').value = '<b>Curie</b>';
    submit();
    await flush();

    expect(results().textContent).toContain('None of the top 2 matches for "topic" are by "<b>Curie</b>"');
    expect(results().querySelector('b')).toBeNull();

    (document.getElementById('author-only-btn') as HTMLButtonElement).click();
    await flush();
    expect(stub.mock.calls.map(([url]) => url)).toContain(`/api/author/${encodeURIComponent('<b>Curie</b>')}/papers?limit=10`);
    expect(results().textContent).toContain('Their paper');
  });

  it('keeps the plain empty message when the search itself found nothing', async () => {
    load(() => json({ papers: [] }));
    field('search-input').value = 'topic';
    field('author-input').value = 'Curie';
    submit();
    await flush();

    expect(results().textContent).toContain('No papers found for "topic"');
    expect(results().querySelector('#author-only-btn')).toBeNull();
  });

  it('words a failure for a scientist, with a support link and the status small', async () => {
    load(() => json({}, 500));
    field('search-input').value = 'topic';
    submit();
    await flush();

    const alert = errors().querySelector('[role="alert"]')!;
    expect(alert.textContent).toContain('Search is unavailable right now');
    expect(alert.textContent).not.toContain('retrieval service');
    expect(alert.querySelector('a')?.getAttribute('href')).toBe('mailto:support@example.org');
    expect(alert.querySelector('small')?.textContent).toBe('Error 500');
  });

  it('writes no markup from an unexpected failure', async () => {
    load(() => { throw new Error('<img src=x>'); });
    field('search-input').value = 'topic';
    submit();
    await flush();

    expect(errors().querySelector('img')).toBeNull();
    expect(errors().querySelector('[role="alert"]')).not.toBeNull();
  });

  it('says what a year range needs when it is the only thing entered', () => {
    load(() => json({}));
    field('year-min').value = '2015';
    submit();

    const alert = errors().querySelector('[role="alert"]')!;
    expect(alert.textContent).toContain('Enter a topic or an author name');
  });

  it('labels every field', () => {
    load(() => json({}));
    expect(document.querySelector('label[for="search-input"]')).not.toBeNull();
    expect(field('year-min').getAttribute('aria-label')).toBe('Year from');
    expect(field('year-max').getAttribute('aria-label')).toBe('Year to');
  });

  it('announces how a search ended', async () => {
    load((url) => (url.endsWith('/pdf/exists') ? json({ pdf_available: false }) : json({ papers: [paper(1), paper(2)] })));
    expect(summary().getAttribute('aria-live')).toBe('polite');
    field('search-input').value = 'topic';
    submit();
    expect(summary().textContent).toBe('Searching');
    await flush();
    expect(summary().textContent).toBe('2 results found');
  });

  it('describes what the page actually does', () => {
    load(() => json({}));
    const intro = document.querySelector('.search-section p')!.textContent!;
    expect(intro).toContain('Author alone');
    expect(intro).not.toContain('or year');
  });
});

describe('support widget', () => {
  const widgetJs = readStatic('shared/support-widget.js');
  const panel = () => document.getElementById('munin-support-panel')!;
  const tab = (shiftKey = false) => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey, bubbles: true, cancelable: true }));

  // An open panel's key handler lives on the document, which outlasts a test.
  afterEach(() => {
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  });

  // The widget on an otherwise empty page, with `me` as the /auth/me answer.
  async function open(me: number, contact?: () => Response) {
    document.head.innerHTML = '';
    document.body.innerHTML = '<button id="opener">Help</button>';
    const stub = vi.fn((url: string) => Promise.resolve(url.endsWith('/auth/me') ? json({}, me) : contact!()));
    new Function('fetch', fillEnv(widgetJs))(stub);
    const opener = document.getElementById('opener')!;
    opener.focus();
    (window as unknown as { MuninSupport: { open(): void } }).MuninSupport.open();
    await new Promise((resolve) => setTimeout(resolve, 60));
    return { opener, stub };
  }

  it('is a labelled modal dialog, with a named button and an announced error', async () => {
    await open(200);
    expect(panel().getAttribute('role')).toBe('dialog');
    expect(panel().getAttribute('aria-modal')).toBe('true');
    expect(document.getElementById(panel().getAttribute('aria-labelledby')!)?.textContent).toBe('Contact Support');
    expect(document.getElementById('munin-support-btn')!.getAttribute('aria-label')).toBe('Contact support');
    expect(document.getElementById('munin-sp-error')!.getAttribute('role')).toBe('alert');
  });

  it('keeps Tab inside the panel, both ways', async () => {
    await open(200);
    const send = document.getElementById('munin-sp-send')!;
    const close = panel().querySelector<HTMLElement>('.sp-close')!;

    send.focus();
    tab();
    expect(document.activeElement).not.toBe(send);
    expect(panel().contains(document.activeElement)).toBe(true);

    close.focus();
    tab(true);
    expect(document.activeElement).toBe(send);
  });

  it('pulls focus back in if it has fallen out', async () => {
    const { opener } = await open(200);
    opener.focus();
    tab();
    expect(panel().contains(document.activeElement)).toBe(true);
  });

  it.each(['.sp-close', '.sp-btn-cancel'])('closes through %s, returning focus and leaving no key handler', async (selector) => {
    const { opener } = await open(200);
    const remove = vi.spyOn(document, 'removeEventListener');
    panel().querySelector<HTMLElement>(selector)!.click();

    expect(document.getElementById('munin-support-overlay')).toBeNull();
    expect(document.activeElement).toBe(opener);
    expect(remove).toHaveBeenCalledWith('keydown', expect.any(Function), true);
    remove.mockRestore();
  });

  it('closes on Escape and returns focus', async () => {
    const { opener } = await open(200);
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    expect(document.getElementById('munin-support-overlay')).toBeNull();
    expect(document.activeElement).toBe(opener);
  });

  it('keeps Escape and Tab from whatever the page has open underneath', async () => {
    await open(200);
    const page = vi.fn();
    document.addEventListener('keydown', page);
    onTestFinished(() => document.removeEventListener('keydown', page));

    tab();
    expect(page).not.toHaveBeenCalled();
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    expect(page).not.toHaveBeenCalled();
    expect(document.getElementById('munin-support-overlay')).toBeNull();

    // Closed, the page has its keys back.
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    expect(page).toHaveBeenCalledTimes(1);
  });

  it('moves focus to Close after a send, since the focused button has gone', async () => {
    const { opener } = await open(200, () => json({}));
    (document.getElementById('munin-sp-message') as HTMLTextAreaElement).value = 'Help';
    const send = document.getElementById('munin-sp-send')!;
    send.focus();
    send.click();
    await flush();
    await flush();

    const close = panel().querySelector<HTMLElement>('.sp-footer [data-sp-close]')!;
    expect(document.activeElement).toBe(close);
    close.click();
    expect(document.getElementById('munin-support-overlay')).toBeNull();
    expect(document.activeElement).toBe(opener);
  });

  it('offers a signed-out visitor a sign-in that comes back to this page', async () => {
    await open(401);
    const signIn = panel().querySelector<HTMLAnchorElement>('.sp-footer a')!;

    expect(signIn.textContent).toBe('Sign in');
    expect(signIn.getAttribute('href')).toBe(
      `https://munin_url_auth.example.org/login?redirect=${encodeURIComponent(window.location.href)}`,
    );
    expect(document.activeElement).toBe(signIn);
    expect(panel().querySelector('.sp-body')!.textContent).toContain('Sign in to send a message here');
    panel().querySelector<HTMLElement>('.sp-footer [data-sp-close]')!.click();
    expect(document.getElementById('munin-support-overlay')).toBeNull();
  });
});

describe('docs and landing pages', () => {
  const page = (path: string) => new DOMParser().parseFromString(fillEnv(readStatic(path)), 'text/html');
  const landing = () => page('landing/index.html');
  const guide = () => page('docs/guide.html');

  it('wraps every architecture table, so only the table scrolls on a phone', () => {
    const doc = page('docs/architecture.html');
    const tables = [...doc.querySelectorAll('table')];
    expect(tables.length).toBeGreaterThan(0);
    for (const table of tables) expect(table.parentElement?.classList.contains('table-scroll')).toBe(true);
  });

  it('draws the architecture without preformatted art', () => {
    const doc = page('docs/architecture.html');
    expect(doc.querySelector('pre')).toBeNull();
    expect(doc.querySelector('.arch')?.textContent).toContain('Retrieval API');
  });

  it('points the knowledge base at the Knowledge browser', () => {
    const doc = landing();
    const browse = [...doc.querySelectorAll('main a')].find((a) => a.textContent === 'Browse')!;
    expect(browse.getAttribute('href')).toBe('https://munin_url_chat.example.org/knowledge');
    const nav = [...doc.querySelectorAll('header nav a')].find((a) => a.textContent === 'Knowledge')!;
    expect(nav.getAttribute('href')).toBe('https://munin_url_chat.example.org/knowledge');
  });

  it('numbers no feature cards, and speaks of one assistant', () => {
    const doc = landing();
    expect(doc.querySelector('.howto-step')).toBeNull();
    expect(doc.body.textContent).not.toContain('All assistants');
    expect(guide().body.textContent).not.toMatch(/All personas|the assistants/);
  });

  it('says who can upload, wherever it invites uploads', () => {
    const row = [...landing().querySelectorAll('.mode')].find((c) => c.querySelector('h3')?.textContent === 'Contribute')!;
    expect(row.textContent).toContain('Group leaders');
    expect(row.querySelector('a')?.getAttribute('href')).toBe('https://munin_url_upload.example.org');
    const text = guide().body.textContent!;
    expect(text).toContain('Research group leaders can grow');
    expect(text).not.toContain('Upload your papers');
  });

  it('gives Deep Research a way in', () => {
    const section = guide().getElementById('deep-research');
    expect(section?.textContent).toBe('Deep Research');
    expect(section?.nextElementSibling?.nextElementSibling?.querySelectorAll('li')).toHaveLength(3);
    const link = [...landing().querySelectorAll('a')].find((a) => a.textContent === 'Deep Research');
    expect(link?.getAttribute('href')).toBe('https://munin_url_docs.example.org/guide.html#deep-research');
  });

  it('describes search as it behaves', () => {
    const text = guide().body.textContent!;
    expect(text).toContain('type their name in the Author filter');
    expect(text).toContain('The title opens the paper at doi.org');
    expect(text).not.toContain('Click a result to see its abstract');
  });
});

describe('upload page', () => {
  it('tells a denied account only what is true for them, once', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    loadPage(uploadHtml, vi.fn(() => Promise.resolve(json({ email: 'member@example.org', role: 'user' }))));
    await flush();

    const box = document.getElementById('user-box')!;
    expect(box.textContent).toContain('Signed in as: member@example.org');
    expect(box.textContent).not.toContain('Uploading as');
    expect(box.textContent).not.toContain('tagged');
    expect((document.getElementById('upload-notice') as HTMLElement).hidden).toBe(true);
    expect((document.getElementById('upload-intro') as HTMLElement).hidden).toBe(true);
    expect(document.querySelector('.no-access.warn')?.querySelector('a')?.getAttribute('href')).toBe('mailto:support@example.org');
    vi.restoreAllMocks();
  });
});

describe('upload page', () => {
  const whoami = (body: unknown) => vi.fn(() => Promise.resolve(json(body)));
  const dashboard = () => document.getElementById('uppy-dashboard')!;
  const uppyStub = () => ({
    Uppy: vi.fn(function () { return { use: vi.fn(), on: vi.fn() }; }),
    Dashboard: {},
    Tus: {},
  });

  beforeEach(() => { vi.spyOn(console, 'error').mockImplementation(() => {}); });
  afterEach(() => vi.restoreAllMocks());

  it('explains itself when Uppy did not load, and keeps the real address', async () => {
    loadPage(uploadHtml, whoami({ email: 'leader@example.org', role: 'group_leader', group: 'lab' }));
    await flush();

    expect(dashboard().querySelector('[role="alert"]')?.textContent).toContain('could not load');
    expect(dashboard().querySelector('a')?.getAttribute('href')).toBe('mailto:support@example.org');
    expect(document.getElementById('user-email')!.textContent).toBe('leader@example.org');
    expect(document.getElementById('user-box')!.classList.contains('warn')).toBe(true);
  });

  it('explains itself the same way when /whoami failed too', async () => {
    loadPage(uploadHtml, vi.fn(() => Promise.reject(new Error('network'))));
    await flush();

    expect(dashboard().querySelector('[role="alert"]')?.textContent).toContain('could not load');
    expect(document.getElementById('user-email')!.textContent).toBe('unknown');
  });

  it('starts the uploader when Uppy is there', async () => {
    const Uppy = uppyStub();
    loadPage(uploadHtml, whoami({ email: 'leader@example.org', role: 'group_leader', group: 'lab' }), { Uppy });
    await flush();

    expect(Uppy.Uppy).toHaveBeenCalledOnce();
    expect(dashboard().querySelector('[role="alert"]')).toBeNull();
    expect(document.getElementById('user-box')!.classList.contains('warn')).toBe(false);
  });

  it('does not show the load error to someone who is denied anyway', async () => {
    loadPage(uploadHtml, whoami({ email: 'member@example.org', role: 'user' }));
    await flush();

    expect(dashboard().textContent).toContain('not a research group leader');
    expect(dashboard().querySelector('[role="alert"]')).toBeNull();
  });
});

// /shared/theme.js adds a light/dark toggle to every static page header, with
// the same cookie rules as src/lib/theme.ts.
describe('static theme toggle', () => {
  const themeJs = readStatic('shared/theme.js');
  const root = document.documentElement;
  const HEADER = '<header><div class="container header-content"><a class="logo">Munin</a><nav><a>Home</a></nav></div></header>';
  type Api = {
    setPref(p: string): string;
    mount(): void;
    cookieDomain(h: string): string | null;
    readPref(c: string): string;
  };

  // Runs theme.js against a stand-in window, so the host, the protocol and the
  // system setting are the test's to choose.
  function load({ hostname = 'search.muninai.org', protocol = 'https:', prefersLight = false } = {}) {
    const listeners: Record<string, (() => void)[]> = {};
    const system = { light: prefersLight, change: () => {} };
    const media = {
      get matches() { return system.light; },
      addEventListener: (_name: string, fn: () => void) => { system.change = fn; },
    };
    const win: Record<string, unknown> = {
      location: { hostname, protocol },
      matchMedia: () => media,
      addEventListener: (name: string, fn: () => void) => { (listeners[name] ??= []).push(fn); },
    };
    new Function('window', themeJs)(win);
    return {
      api: win.MuninTheme as Api,
      fire: (name: string) => listeners[name]?.forEach((fn) => fn()),
      setSystem: (light: boolean) => { system.light = light; system.change(); },
    };
  }

  const toggle = () => document.querySelector<HTMLButtonElement>('header .header-content > .theme-toggle')!;
  // What the inline head script leaves behind before theme.js runs.
  const headScript = (pref: string, theme = pref) => {
    root.setAttribute('data-theme-pref', pref);
    root.setAttribute('data-theme', theme);
  };

  let events: { pref: string; theme: string }[];
  const record = (e: Event) => events.push((e as CustomEvent).detail);

  beforeEach(() => {
    document.head.innerHTML = '';
    document.body.innerHTML = HEADER;
    events = [];
    document.addEventListener('munin-themechange', record);
  });
  afterEach(() => {
    vi.restoreAllMocks();
    document.removeEventListener('munin-themechange', record);
    document.cookie = 'munin_theme=; Max-Age=0; Path=/';
    document.cookie = 'munin_theme=; Max-Age=0; Path=/; Domain=muninai.org';
    root.removeAttribute('data-theme');
    root.removeAttribute('data-theme-pref');
  });

  it('adds one button after the nav, named for what a click does', () => {
    document.cookie = 'munin_theme=light; Path=/';
    headScript('light');
    const { api } = load();
    api.mount();

    expect(document.querySelectorAll('.theme-toggle')).toHaveLength(1);
    expect(toggle().previousElementSibling?.tagName).toBe('NAV');
    expect(toggle().type).toBe('button');
    expect(toggle().getAttribute('aria-label')).toBe('Switch to dark theme');
    // Loading changed nothing, so nothing was announced.
    expect(events).toEqual([]);
  });

  it('follows the system with no cookie', () => {
    headScript('system', 'light');
    load({ prefersLight: true });
    expect(root.getAttribute('data-theme')).toBe('light');
    expect(root.getAttribute('data-theme-pref')).toBe('system');
    expect(toggle().getAttribute('aria-label')).toBe('Switch to dark theme');
  });

  it('a click stores the other theme on the shared domain for a year, and applies it at once', () => {
    document.cookie = 'munin_theme=dark; Path=/';
    headScript('dark');
    const set = vi.spyOn(document, 'cookie', 'set');
    load({ hostname: 'search.muninai.org', protocol: 'https:' });
    toggle().click();

    expect(set).toHaveBeenCalledWith('munin_theme=light; Path=/; Max-Age=31536000; SameSite=Lax; Domain=muninai.org; Secure');
    expect(root.getAttribute('data-theme')).toBe('light');
    expect(root.getAttribute('data-theme-pref')).toBe('light');
    expect(toggle().getAttribute('aria-label')).toBe('Switch to dark theme');
    expect(events).toEqual([{ pref: 'light', theme: 'light' }]);
  });

  it('flips what is showing, when that came from the system', () => {
    headScript('system', 'light');
    load({ prefersLight: true });
    toggle().click();
    expect(root.getAttribute('data-theme')).toBe('dark');
    expect(root.getAttribute('data-theme-pref')).toBe('dark');
  });

  it('uses a host-only cookie, without Secure, on localhost over http', () => {
    headScript('dark');
    const set = vi.spyOn(document, 'cookie', 'set');
    load({ hostname: 'localhost', protocol: 'http:' });
    toggle().click();

    expect(set).toHaveBeenCalledWith('munin_theme=light; Path=/; Max-Age=31536000; SameSite=Lax');
  });

  it('works out the cookie domain and reads the cookie exactly as theme.ts does', () => {
    const { api } = load();
    for (const host of ['chat.muninai.org', 'upload.muninai.org', 'research.muninai.org', 'muninai.org',
      'docs.munin.uni-example.de', 'munin.uni-example.de', 'www.muninai.org', 'localhost', '127.0.0.1', '::1', 'intranet']) {
      expect(api.cookieDomain(host), host).toBe(cookieDomain(host));
    }
    for (const cookie of ['', 'munin_theme=light', 'a=1; munin_theme=system; b=2', 'a=1;munin_theme=dark',
      'munin_theme=purple', 'xmunin_theme=light', 'munin_theme=lightish']) {
      expect(api.readPref(cookie), cookie).toBe(readPref(cookie));
    }
  });

  it('picks up a choice made in another tab or site when this tab comes back', () => {
    document.cookie = 'munin_theme=dark; Path=/';
    headScript('dark');
    const { fire } = load();

    document.cookie = 'munin_theme=light; Path=/';
    fire('focus');
    expect(root.getAttribute('data-theme')).toBe('light');
    expect(toggle().getAttribute('aria-label')).toBe('Switch to dark theme');

    fire('focus');
    fire('pageshow');
    expect(events).toEqual([{ pref: 'light', theme: 'light' }]);
  });

  it('follows the system while set to Match system, and stops once toggled', () => {
    // On the shared domain, as theme.js writes it, so the later choice replaces it.
    document.cookie = 'munin_theme=system; Path=/; Domain=muninai.org';
    headScript('system', 'dark');
    const { setSystem } = load({ prefersLight: false });

    setSystem(true);
    expect(root.getAttribute('data-theme')).toBe('light');
    expect(root.getAttribute('data-theme-pref')).toBe('system');
    expect(events).toEqual([{ pref: 'system', theme: 'light' }]);

    toggle().click();
    setSystem(false);
    setSystem(true);
    expect(root.getAttribute('data-theme')).toBe('dark');
  });

  it('still sets the theme on a page without a header', () => {
    document.body.innerHTML = '<main></main>';
    headScript('dark');
    const { api } = load();
    expect(document.querySelector('.theme-toggle')).toBeNull();
    expect(api.setPref('light')).toBe('light');
    expect(root.getAttribute('data-theme')).toBe('light');
  });

  const pages = ['landing/index.html', 'search/index.html', 'research/index.html', 'upload/index.html',
    'maintenance/index.html', 'docs/index.html', 'docs/architecture.html', 'docs/guide.html'];

  it.each(pages)('%s loads the toggle and has a header for it', (path) => {
    const html = readStatic(path);
    expect(html).toMatch(/<script src="\/shared\/theme\.js\?v=\d+" defer><\/script>/);
    const doc = new DOMParser().parseFromString(fillEnv(html), 'text/html');
    expect(doc.querySelector('header .header-content > nav')).not.toBeNull();
  });

  it.each(pages)('%s keeps every Caddy directive an env lookup', (path) => {
    const html = readStatic(path);
    const opened = html.split('{{').length - 1;
    const directives = html.match(/\{\{env "[A-Z_]+"\}\}/g) ?? [];
    expect(directives).toHaveLength(opened);
  });

  it('has nothing Caddy templates would read as a directive', () => {
    expect(themeJs).not.toContain('{{');
  });
});

describe('upload page theme', () => {
  const root = document.documentElement;

  beforeEach(() => { vi.spyOn(console, 'error').mockImplementation(() => {}); });
  afterEach(() => {
    vi.restoreAllMocks();
    root.removeAttribute('data-theme');
  });

  it.each(['light', 'dark'])('starts Uppy in the %s theme the page is in, and follows the switch', async (theme) => {
    root.setAttribute('data-theme', theme);
    const dashboard = { setOptions: vi.fn() };
    const use = vi.fn();
    const Uppy = {
      Uppy: vi.fn(function () {
        return { use, on: vi.fn(), getPlugin: (id: string) => (id === 'Dashboard' ? dashboard : undefined) };
      }),
      Dashboard: { name: 'Dashboard' },
      Tus: {},
    };
    loadPage(uploadHtml, vi.fn(() => Promise.resolve(json({ email: 'a@example.org', role: 'admin' }))), { Uppy });
    await flush();

    expect(use).toHaveBeenCalledWith(Uppy.Dashboard, expect.objectContaining({ theme }));

    const other = theme === 'light' ? 'dark' : 'light';
    root.setAttribute('data-theme', other);
    document.dispatchEvent(new CustomEvent('munin-themechange', { detail: { pref: other, theme: other } }));
    expect(dashboard.setOptions).toHaveBeenLastCalledWith({ theme: other });
  });
});
