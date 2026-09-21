import { render } from '@testing-library/react';
import { Markdown } from './Markdown';

/**
 * P1 #18: XSS regression suite for Markdown.tsx.
 *
 * Treats `content` as hostile model output. Every test below asserts
 * the rendered DOM does NOT contain an exploitable artefact:
 *  - no `<script>`, `<iframe>`, `<object>`, `<embed>`, `<svg>` from source
 *  - no anchor with a `javascript:` / `data:` / `vbscript:` href
 *  - no image with a dangerous src
 *  - no inline event handlers from source
 *
 * If a future change adds `rehype-raw` (HTML pass-through) or removes
 * the explicit `urlTransform`, this suite is what catches it.
 */

function renderMd(content: string) {
  return render(<Markdown content={content} />);
}

// ────────────────────────────────────────────────────────────────────
// A. URL-scheme stripping on links and images
// ────────────────────────────────────────────────────────────────────

describe('Markdown — dangerous URL schemes are stripped', () => {
  it('javascript: link renders the anchor but strips the href', () => {
    const { container } = renderMd('[click me](javascript:alert(1))');
    const a = container.querySelector('a');
    expect(a).not.toBeNull();
    // urlTransform replaces the scheme with '' so the anchor is inert.
    expect(a!.getAttribute('href') || '').toBe('');
    expect(a!.textContent).toBe('click me');
  });

  it('data:text/html link is stripped', () => {
    const payload = 'data:text/html,<script>alert(1)</script>';
    const { container } = renderMd(`[x](${payload})`);
    const a = container.querySelector('a')!;
    expect(a.getAttribute('href') || '').toBe('');
    // The literal string never lands in the DOM as a usable href.
    expect(a.outerHTML).not.toContain('data:text/html');
  });

  it('vbscript: link is stripped', () => {
    const { container } = renderMd('[x](vbscript:msgbox(1))');
    const a = container.querySelector('a')!;
    expect(a.getAttribute('href') || '').toBe('');
  });

  it('mixed-case JaVaScRiPt: link is stripped (case-insensitive match)', () => {
    const { container } = renderMd('[x](JaVaScRiPt:alert(1))');
    const a = container.querySelector('a')!;
    expect(a.getAttribute('href') || '').toBe('');
  });

  it('javascript: image src is stripped', () => {
    const { container } = renderMd('![evil](javascript:alert(1))');
    const img = container.querySelector('img');
    // The img tag may exist with an empty src, or be absent entirely;
    // either way it must not load a javascript: URL.
    if (img) {
      expect(img.getAttribute('src') || '').toBe('');
    }
  });
});

// ────────────────────────────────────────────────────────────────────
// B. Raw HTML in source is not rendered as live HTML
// ────────────────────────────────────────────────────────────────────

describe('Markdown — raw HTML in source does not execute', () => {
  it('<script> tag is rendered as text, not a script element', () => {
    const { container } = renderMd('hi <script>window.__x=1</script> bye');
    expect(container.querySelector('script')).toBeNull();
    // The text body should contain the literal characters.
    expect(container.textContent).toContain('<script>');
  });

  it('<img onerror> is not produced as a live element', () => {
    const { container } = renderMd('<img src=x onerror=alert(1)>');
    // No image tag is created from the raw HTML source.
    expect(container.querySelector('img')).toBeNull();
  });

  it('<iframe> from raw HTML is not produced', () => {
    const { container } = renderMd('<iframe src="javascript:alert(1)"></iframe>');
    expect(container.querySelector('iframe')).toBeNull();
  });

  it('<iframe srcdoc="..."> from raw HTML is not produced', () => {
    const { container } = renderMd(
      '<iframe srcdoc="<script>alert(1)</script>"></iframe>',
    );
    expect(container.querySelector('iframe')).toBeNull();
  });

  it('<a href="javascript:" onclick=...> from raw HTML is not produced as a live anchor', () => {
    const { container } = renderMd('<a href="javascript:alert(1)" onclick="alert(2)">x</a>');
    // No anchor was synthesised from the raw HTML; the source becomes text.
    // (A separate markdown-link test confirms anchors made via [text](url)
    //  do exist but with hrefs filtered.)
    const anchors = container.querySelectorAll('a');
    anchors.forEach(a => {
      expect(a.getAttribute('href') || '').toBe('');
      expect(a.getAttribute('onclick')).toBeNull();
    });
  });
});

// ────────────────────────────────────────────────────────────────────
// C. HTML-entity encoding and other obfuscation
// ────────────────────────────────────────────────────────────────────

describe('Markdown — encoded / obfuscated payloads', () => {
  it('HTML-entity-encoded javascript: scheme is not decoded into a live link', () => {
    // Source: `[x](&#106;avascript:alert(1))` — `&#106;` is `j`. If
    // react-markdown decoded the entity *before* urlTransform, the
    // anchor could carry a javascript: URL. Asserts we don't.
    const { container } = renderMd('[x](&#106;avascript:alert(1))');
    const a = container.querySelector('a');
    if (a) {
      const href = a.getAttribute('href') || '';
      expect(href.toLowerCase()).not.toMatch(/^javascript:/);
    }
  });

  it('gfm autolink of a bare javascript: URL does not produce a live anchor', () => {
    // remark-gfm auto-links recognisable URLs in text. The autolinker
    // typically restricts itself to http/https, but assert that even
    // if it picks this up, urlTransform strips the href.
    const { container } = renderMd('see this: javascript:alert(1) ok?');
    const anchors = Array.from(container.querySelectorAll('a'));
    anchors.forEach(a => {
      expect((a.getAttribute('href') || '').toLowerCase()).not.toMatch(/^javascript:/);
    });
  });
});

// ────────────────────────────────────────────────────────────────────
// D. Reference-style links + title attribute
// ────────────────────────────────────────────────────────────────────

describe('Markdown — reference-style + title injection', () => {
  it('reference-style link with javascript: destination is stripped', () => {
    const md = 'See [click][evil]\n\n[evil]: javascript:alert(1)';
    const { container } = renderMd(md);
    const a = container.querySelector('a');
    if (a) {
      expect((a.getAttribute('href') || '').toLowerCase()).not.toMatch(/^javascript:/);
    }
  });

  it('reference-style link with data: destination is stripped', () => {
    const md = 'See [click][evil]\n\n[evil]: data:text/html,<script>alert(1)</script>';
    const { container } = renderMd(md);
    const a = container.querySelector('a');
    if (a) {
      expect((a.getAttribute('href') || '').toLowerCase()).not.toMatch(/^data:/);
    }
  });

  it('title attribute is rendered as plain text, not nested HTML', () => {
    // Markdown title: `[x](http://example.com "T")`. Try to smuggle an
    // <img onerror> via the title slot.
    const md = '[x](http://example.com "<img src=x onerror=alert(1)>")';
    const { container } = renderMd(md);
    // No img element from the title content.
    expect(container.querySelector('img')).toBeNull();
    const a = container.querySelector('a')!;
    // The title attribute is set, but as a plain string. React serialises
    // it without re-parsing HTML, so an attacker cannot escape out of it.
    expect(a.title || '').toContain('<img');
  });
});

// ────────────────────────────────────────────────────────────────────
// E. Sanity — legitimate markdown still works
// ────────────────────────────────────────────────────────────────────

describe('Markdown — legitimate content still renders', () => {
  it('safe http link renders with href and noopener', () => {
    const { container } = renderMd('[Anthropic](https://anthropic.com)');
    const a = container.querySelector('a')!;
    expect(a.getAttribute('href')).toBe('https://anthropic.com');
    expect(a.getAttribute('rel')).toContain('noopener');
    expect(a.getAttribute('target')).toBe('_blank');
  });

  it('mailto: link renders (mailto is in the allowlist)', () => {
    const { container } = renderMd('[contact](mailto:hi@example.com)');
    const a = container.querySelector('a')!;
    expect(a.getAttribute('href')).toBe('mailto:hi@example.com');
  });
});

// ────────────────────────────────────────────────────────────────────
// F. KaTeX math rendering
// ────────────────────────────────────────────────────────────────────
//
// Chat 2ab70e98 (2026-06-03): the model emitted `$\delta_1$` inline and
// `$$\delta_n \approx 1 - (1 - \delta_1)^{nN}$$` display math, both of
// which previously rendered as literal dollar-sign text because
// Markdown.tsx had no math plugin. After wiring `remark-math` +
// `rehype-katex` both forms render as KaTeX-styled DOM.

describe('Markdown — KaTeX math', () => {
  it('inline `$a^2$` renders as a .katex span', () => {
    const { container } = renderMd('The formula $a^2 + b^2 = c^2$ holds.');
    const katex = container.querySelector('.katex');
    expect(katex).not.toBeNull();
    // KaTeX rewrites `^2` as a sup; presence of a `.msupsub` or sup-styled
    // element confirms the math was actually parsed (and not just dropped
    // through as raw text).
    expect(container.textContent).not.toContain('$a^2');
  });

  it('`$$E = mc^2$$` on one line still renders as KaTeX (not raw text)', () => {
    // This is the shape the chat persona actually emits (chat
    // 2ab70e98 had `$$\delta_{n} \approx 1 - (1 - \delta_1)^{nN}$$`
    // on its own paragraph). remark-math renders single-line `$$`
    // as INLINE katex rather than a centred display block; that's
    // still a huge improvement over raw text and matches the user's
    // requested behaviour ("render latex equations inline").
    const { container } = renderMd('Einstein: $$E = mc^2$$ holds.');
    expect(container.querySelector('.katex')).not.toBeNull();
    expect(container.textContent).not.toContain('$$E');
  });

  it('multi-line `$$\\n...\\n$$` renders as a .katex-display block', () => {
    // The strictly-correct display syntax (delimiters on their own
    // lines) DOES trigger the .katex-display block. Worth pinning
    // so we know when remark-math behaviour drifts.
    const { container } = renderMd('Einstein:\n\n$$\nE = mc^2\n$$\n\nQ.E.D.');
    expect(container.querySelector('.katex-display')).not.toBeNull();
    expect(container.textContent).not.toContain('$$');
  });

  it('non-math `$` (with letters touching) does NOT trigger KaTeX', () => {
    // `$5` in prose is currency, not math. remark-math is conservative
    // about single-`$` triggers — confirm prose stays prose.
    const { container } = renderMd('The price is $5 today.');
    expect(container.querySelector('.katex')).toBeNull();
    expect(container.textContent).toContain('$5');
  });

  it('math next to prose does not break following text', () => {
    const { container } = renderMd('Compute $x^2$ then continue.');
    // Both the rendered KaTeX and the trailing prose should be there.
    expect(container.querySelector('.katex')).not.toBeNull();
    expect(container.textContent).toContain('then continue');
  });
});

// ────────────────────────────────────────────────────────────────────
// G. LaTeX-style delimiters \( \) and \[ \]
// ────────────────────────────────────────────────────────────────────
//
// Chat b4813f40 (2026-06-03, Schrödinger Gleichung): the model emits
// display math as `$$...$$` (works) but inline math as `\(...\)`
// (doesn't work — remark-math only recognises `$...$`). The
// `normalizeMathDelimiters` preprocessor rewrites the LaTeX forms to
// dollar-sign forms BEFORE remark-math sees them, except inside code
// blocks where we want the literal characters preserved.

import { normalizeMathDelimiters } from '../lib/mathDelimiters';

describe('normalizeMathDelimiters', () => {
  it('rewrites `\\(x\\)` to `$x$`', () => {
    expect(normalizeMathDelimiters('Let \\(x\\) be small.'))
      .toBe('Let $x$ be small.');
  });

  it('rewrites `\\[E=mc^2\\]` to `$$E=mc^2$$`', () => {
    expect(normalizeMathDelimiters('Einstein: \\[E=mc^2\\] holds.'))
      .toBe('Einstein: $$E=mc^2$$ holds.');
  });

  it('handles multiple inline math spans on one line', () => {
    expect(normalizeMathDelimiters('\\(a\\) and \\(b\\) and \\(c\\).'))
      .toBe('$a$ and $b$ and $c$.');
  });

  it('handles nested parens inside \\(...\\) via lazy matching', () => {
    // From chat b4813f40: `(\(i\) is the imaginary unit (\(i^2 = -1\)))`
    const input = '(\\(i\\) is the imaginary unit (\\(i^2 = -1\\)))';
    const out = normalizeMathDelimiters(input);
    expect(out).toBe('($i$ is the imaginary unit ($i^2 = -1$))');
  });

  it('handles multi-line display \\[...\\]', () => {
    const input = 'Before\n\n\\[\n\\sum_{i=1}^n a_i\n\\]\n\nAfter';
    const out = normalizeMathDelimiters(input);
    expect(out).toBe('Before\n\n$$\n\\sum_{i=1}^n a_i\n$$\n\nAfter');
  });

  it('leaves literal `\\(foo\\)` inside fenced code blocks untouched', () => {
    const input = 'In LaTeX you write\n```\n\\(x^2\\)\n```\nfor inline math.';
    expect(normalizeMathDelimiters(input)).toBe(input);
  });

  it('leaves literal `\\(foo\\)` inside inline `code` spans untouched', () => {
    const input = 'Type `\\(x\\)` to get inline math.';
    expect(normalizeMathDelimiters(input)).toBe(input);
  });

  it('rewrites math OUTSIDE a code block while leaving the code intact', () => {
    const input = 'Outside: \\(x\\)\n```\n\\(y\\)\n```\nAnd more \\(z\\).';
    expect(normalizeMathDelimiters(input))
      .toBe('Outside: $x$\n```\n\\(y\\)\n```\nAnd more $z$.');
  });

  it('is a no-op when no LaTeX delimiters appear', () => {
    const input = 'Plain prose with $a^2$ and `code` only.';
    expect(normalizeMathDelimiters(input)).toBe(input);
  });
});

describe('Markdown — LaTeX-style delimiters render via KaTeX', () => {
  it('inline `\\(i\\)` renders as a .katex span', () => {
    const { container } = renderMd('Let \\(i\\) be the imaginary unit.');
    expect(container.querySelector('.katex')).not.toBeNull();
    expect(container.textContent).not.toContain('\\(i\\)');
    expect(container.textContent).not.toContain('$i$');
  });

  it('display `\\[E=mc^2\\]` on its own paragraph renders as KaTeX', () => {
    // Single-line `$$...$$` (the form `\[...\]` rewrites to) renders
    // as inline katex, not as .katex-display -- but it IS rendered
    // and the literal delimiters disappear, which is the user-visible
    // fix that matters.
    const { container } = renderMd('Eq:\n\n\\[E=mc^2\\]\n\nDone.');
    expect(container.querySelector('.katex')).not.toBeNull();
    expect(container.textContent).not.toContain('\\[');
    expect(container.textContent).not.toContain('\\]');
  });

  it('the chat b4813f40 fragment renders without raw \\( escaping through', () => {
    // Direct excerpt from the broken chat.
    const fragment =
      '- **\\(i\\)** is the imaginary unit (\\(i^2 = -1\\))\n' +
      '- **\\(\\hbar\\)** is the reduced Planck constant';
    const { container } = renderMd(fragment);
    // At least one .katex span should appear, and no raw `\(` should
    // remain in the rendered text.
    expect(container.querySelector('.katex')).not.toBeNull();
    expect(container.textContent).not.toContain('\\(');
    expect(container.textContent).not.toContain('\\)');
  });
});
