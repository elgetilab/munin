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
