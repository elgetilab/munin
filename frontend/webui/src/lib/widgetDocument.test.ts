import { buildWidgetDocument, WIDGET_CSP } from './widgetDocument';

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
    expect(doc.match(/<\/script/gi)).toHaveLength(1);
    expect(doc).not.toContain('<!--');
  });

  it('leaves the meaning of escaped script text alone', () => {
    const doc = buildWidgetDocument({ scripts: ['globalThis.x = "</SCRIPT><!--";'] });
    const code = doc.slice(doc.indexOf('<script>') + '<script>'.length, doc.lastIndexOf('</script>'));
    expect(new Function(`${code}; return globalThis.x;`)()).toBe('</SCRIPT><!--');
  });
});
