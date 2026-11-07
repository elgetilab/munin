import { buildWidgetDocument, WIDGET_CSP, WIDGET_THEME_SCRIPT } from './widgetDocument';

// The last <script> element is the widget's own, after the theme script.
function lastScript(doc: string): string {
  return doc.slice(doc.lastIndexOf('<script>') + '<script>'.length, doc.lastIndexOf('</script>'));
}

describe('buildWidgetDocument', () => {
  it('puts the policy before anything it has to govern', () => {
    const doc = buildWidgetDocument({ scripts: ['run()'], css: 'a{}', body: '<div></div>' });
    expect(doc.indexOf('Content-Security-Policy')).toBeLessThan(doc.indexOf('<style>'));
    expect(doc.indexOf('Content-Security-Policy')).toBeLessThan(doc.indexOf('<script>'));
    expect(doc).toContain('<script>run()</script>');
  });

  it('allows no network sources', () => {
    expect(WIDGET_CSP).toContain("default-src 'none'");
    expect(WIDGET_CSP).not.toMatch(/https?:|\*|'self'|connect-src/);
  });

  it('keeps script text from ending its element or opening a comment', () => {
    const doc = buildWidgetDocument({ scripts: ['var a = "</script><img src=x>"; var b = "<!-- c";'] });
    // One for the theme script and one for the widget's: the text adds none.
    expect(doc.match(/<\/script/gi)).toHaveLength(2);
    expect(doc).not.toContain('<!--');
    expect(lastScript(doc)).toContain('<img src=x>');
  });

  it('leaves the meaning of escaped script text alone', () => {
    const doc = buildWidgetDocument({ scripts: ['globalThis.x = "</SCRIPT><!--";'] });
    expect(new Function(`${lastScript(doc)}; return globalThis.x;`)()).toBe('</SCRIPT><!--');
  });
});

describe('the widget theme script', () => {
  const root = document.documentElement;
  let stop: () => void;

  // jsdom does not run a frame, so the script runs in the test window. There
  // window.parent is window itself, which stands in for the app.
  beforeEach(() => {
    const add = vi.spyOn(window, 'addEventListener');
    new Function(WIDGET_THEME_SCRIPT)();
    const listener = add.mock.calls.find(([type]) => type === 'message')![1] as EventListener;
    add.mockRestore();
    stop = () => window.removeEventListener('message', listener);
  });
  afterEach(() => {
    stop();
    root.removeAttribute('data-theme');
    root.style.colorScheme = '';
  });

  function send(data: unknown, source: unknown = window.parent) {
    const event = new MessageEvent('message', { data });
    Object.defineProperty(event, 'source', { value: source });
    window.dispatchEvent(event);
  }

  it("runs after the policy and before the widget's own styles and scripts", () => {
    const doc = buildWidgetDocument({ scripts: ['run()'], css: 'a{}' });
    const at = doc.indexOf(WIDGET_THEME_SCRIPT);
    expect(at).toBeGreaterThan(doc.indexOf('Content-Security-Policy'));
    expect(at).toBeLessThan(doc.indexOf('<style>'));
    expect(at).toBeLessThan(doc.indexOf('<script>run()'));
  });

  it('puts the theme from a state message on <html>, and follows a change', () => {
    send({ type: 'state', state: {}, theme: 'light' });
    expect(root).toHaveAttribute('data-theme', 'light');
    expect(root.style.colorScheme).toBe('light');
    send({ type: 'state', state: {}, theme: 'dark' });
    expect(root).toHaveAttribute('data-theme', 'dark');
    expect(root.style.colorScheme).toBe('dark');
  });

  it.each([
    ['another window', { type: 'state', theme: 'light' }, null],
    ['another type', { type: 'theme', theme: 'light' }, undefined],
    ['an unknown theme', { type: 'state', theme: 'x" onload="y' }, undefined],
    ['no theme', { type: 'state', state: {} }, undefined],
    ['no data', null, undefined],
  ])('ignores a message from %s', (_name, data, source) => {
    send(data, source === undefined ? window.parent : source);
    expect(root).not.toHaveAttribute('data-theme');
  });
});
