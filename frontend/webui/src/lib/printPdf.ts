// Print-to-PDF for text artifacts (Deep Research reports, notes, etc.).
//
// We deliberately do NOT ship a client-side PDF library: the browser's own
// print engine produces a smaller, text-selectable, well-paginated PDF and
// costs zero bundle weight. This opens a fresh window with the artifact's
// ALREADY-RENDERED HTML (so markdown headings, tables, KaTeX math and inline
// syntax-highlight styles all survive), clones the app's stylesheets so
// `.prose-munin` + Tailwind utilities resolve, then layers a light-theme
// print override on top by redefining the theme `--color-*` variables. The
// user picks "Save as PDF" in the print dialog.

// Layered last so it wins ties against the cloned app CSS. Flipping the theme
// variables to light values recolours every rule that reads them (prose,
// utilities) without having to restyle each element.
const PRINT_OVERRIDE_CSS = `
@page { margin: 18mm 16mm; }
:root {
  --color-bg-primary: #ffffff;
  --color-bg-secondary: #ffffff;
  --color-bg-tertiary: #f4f4f5;
  --color-text-primary: #1a1a1a;
  --color-text-secondary: #444444;
  --color-border: #cccccc;
  --color-accent: #0b62c4;
  --color-accent-hover: #0b62c4;
}
html, body {
  background: #ffffff !important;
  color: #1a1a1a !important;
  margin: 0;
  -webkit-print-color-adjust: exact;
  print-color-adjust: exact;
}
.pdf-report {
  max-width: 190mm;
  margin: 0 auto;
  font-size: 12pt;
  line-height: 1.6;
  color: #1a1a1a;
}
.pdf-report h1 { font-size: 20pt; }
.pdf-report h2 { font-size: 16pt; }
.pdf-report h3 { font-size: 13pt; }
.pdf-report h1, .pdf-report h2, .pdf-report h3, .pdf-report h4 { break-after: avoid; }
.pdf-report pre, .pdf-report table, .pdf-report blockquote, .pdf-report img { break-inside: avoid; }
.pdf-report table { border-collapse: collapse; width: 100%; }
.pdf-report th, .pdf-report td { border: 1px solid #cccccc; padding: 4px 8px; text-align: left; }
.pdf-report pre { white-space: pre-wrap; word-wrap: break-word; }
.pdf-report a { color: #0b62c4; text-decoration: underline; }
.pdf-report img { max-width: 100%; }
`;

// Give cloned <link> stylesheets a chance to load before printing so the PDF
// isn't rendered unstyled. They point at the SAME already-cached CSS the app
// is using, so this resolves near-instantly; the timeout is only a backstop.
const STYLESHEET_WAIT_MS = 1500;

/**
 * Open a print window for the given already-rendered HTML and trigger the
 * browser's print dialog (where the user chooses "Save as PDF").
 *
 * Returns false if the window could not be opened (e.g. popup blocked); the
 * caller should surface a hint to the user in that case. Must be called
 * directly from a user gesture (click) so the popup is allowed.
 */
export function printArtifactAsPdf(contentHtml: string, title: string): boolean {
  const win = window.open('', '_blank', 'width=820,height=1000');
  if (!win) return false;

  const doc = win.document;
  doc.open();
  doc.write('<!doctype html><html><head><meta charset="utf-8"></head><body></body></html>');
  doc.close();
  doc.title = title || 'Report';

  // Clone the app's stylesheets so prose/utility/KaTeX rules apply.
  const pendingLinks: HTMLLinkElement[] = [];
  document.querySelectorAll('style, link[rel="stylesheet"]').forEach((node) => {
    const clone = node.cloneNode(true) as HTMLElement;
    doc.head.appendChild(clone);
    if (clone.tagName === 'LINK') pendingLinks.push(clone as HTMLLinkElement);
  });

  const override = doc.createElement('style');
  override.textContent = PRINT_OVERRIDE_CSS;
  doc.head.appendChild(override);

  const main = doc.createElement('main');
  main.className = 'pdf-report';
  main.innerHTML = contentHtml;
  doc.body.appendChild(main);

  const triggerPrint = () => {
    win.focus();
    win.print();
  };
  // Auto-close after the dialog is dismissed (saved or cancelled).
  win.onafterprint = () => win.close();

  const waits = pendingLinks.map(
    (link) =>
      new Promise<void>((resolve) => {
        if (link.sheet) return resolve();
        link.addEventListener('load', () => resolve(), { once: true });
        link.addEventListener('error', () => resolve(), { once: true });
      })
  );
  Promise.race([
    Promise.all(waits),
    new Promise<void>((resolve) => setTimeout(resolve, STYLESHEET_WAIT_MS)),
  ]).then(triggerPrint);

  return true;
}
