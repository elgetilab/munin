import { PRINT_OVERRIDE_CSS, PRINT_THEME, printArtifactAsPdf } from './printPdf';
import { contrastRatio } from './colorContrast';

// Build a stand-in for the popup window backed by a real detached document so
// doc.open/write/close and DOM APIs behave as in a browser.
function makeFakeWindow() {
  const doc = document.implementation.createHTMLDocument('blank');
  return {
    document: doc,
    focus: vi.fn(),
    print: vi.fn(),
    onafterprint: null as null | (() => void),
    close: vi.fn(),
  };
}

const flush = () => new Promise((r) => setTimeout(r, 0));

describe('printArtifactAsPdf', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    document.head.querySelectorAll('[data-test-injected]').forEach((n) => n.remove());
  });

  it('returns false and does nothing when the popup is blocked', () => {
    vi.spyOn(window, 'open').mockReturnValue(null);
    expect(printArtifactAsPdf('<p>hi</p>', 'Report')).toBe(false);
  });

  it('renders the content into a .pdf-report main and applies the print override', async () => {
    const fake = makeFakeWindow();
    vi.spyOn(window, 'open').mockReturnValue(fake as unknown as Window);

    const ok = printArtifactAsPdf('<h1>Findings</h1><p>body text</p>', 'Lipid GPCR report');
    expect(ok).toBe(true);
    expect(fake.document.title).toBe('Lipid GPCR report');

    const main = fake.document.querySelector('main.pdf-report');
    expect(main).not.toBeNull();
    expect(main!.innerHTML).toContain('<h1>Findings</h1>');
    expect(main!.textContent).toContain('body text');

    // A print override <style> that flips the theme to a light scheme is present.
    const styleText = Array.from(fake.document.head.querySelectorAll('style'))
      .map((s) => s.textContent || '')
      .join('\n');
    expect(styleText).toContain('.pdf-report');
    expect(styleText).toContain('--color-bg-primary: #ffffff');

    // With no external <link> stylesheets to await, printing fires promptly.
    await flush();
    expect(fake.print).toHaveBeenCalledTimes(1);
    expect(fake.focus).toHaveBeenCalled();
    expect(typeof fake.onafterprint).toBe('function');
  });

  it('clones the app stylesheets into the print window', async () => {
    const marker = document.createElement('style');
    marker.setAttribute('data-test-injected', '');
    marker.textContent = '.prose-munin { line-height: 1.7; }';
    document.head.appendChild(marker);

    const fake = makeFakeWindow();
    vi.spyOn(window, 'open').mockReturnValue(fake as unknown as Window);
    printArtifactAsPdf('<p>x</p>', 'R');
    await flush();

    const cloned = Array.from(fake.document.head.querySelectorAll('style'))
      .map((s) => s.textContent || '')
      .join('\n');
    expect(cloned).toContain('.prose-munin { line-height: 1.7; }');
  });
});

describe('print colours', () => {
  // The custom properties the override sets on :root.
  const vars = Object.fromEntries(
    [...PRINT_OVERRIDE_CSS.matchAll(/--([\w-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()])
  );

  it('forces the light theme on the print document', () => {
    const fake = makeFakeWindow();
    vi.spyOn(window, 'open').mockReturnValue(fake as unknown as Window);
    printArtifactAsPdf('<p>x</p>', 'R');
    expect(PRINT_THEME).toBe('light');
    expect(fake.document.documentElement.getAttribute('data-theme')).toBe('light');
    expect(PRINT_OVERRIDE_CSS).toContain('color-scheme: light');
  });

  it('outranks the app light block, not just a bare :root', () => {
    expect(PRINT_OVERRIDE_CSS).toMatch(/:root, :root\[data-theme\] \{/);
  });

  it('carries the success, warning and error colours', () => {
    for (const name of ['color-success', 'color-warning', 'color-error']) {
      expect(vars[name], name).toBeDefined();
    }
  });

  it('keeps every text colour at 4.5:1 or more on white', () => {
    const text = Object.entries(vars).filter(([name]) =>
      /^color-(text-|accent|success|warning|error)/.test(name));
    expect(text.length).toBeGreaterThanOrEqual(8);
    for (const [name, colour] of text) {
      expect(contrastRatio(colour, '#ffffff'), name).toBeGreaterThanOrEqual(4.5);
    }
    // Colours written straight into rules, such as body text and links.
    const literal = new Set(PRINT_OVERRIDE_CSS.match(/(?<=color:\s*)#[0-9a-f]{3,6}/gi));
    expect(literal.size).toBeGreaterThan(0);
    for (const colour of literal) {
      expect(contrastRatio(colour, '#ffffff'), colour).toBeGreaterThanOrEqual(4.5);
    }
  });

  it('drops the code text shadow, which would blur text on paper', () => {
    expect(vars['code-text-shadow']).toBe('none');
  });
});
