import { printArtifactAsPdf } from './printPdf';

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
