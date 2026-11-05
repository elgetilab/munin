import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

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
