import { getExtension } from './artifactDownload';

/**
 * "Pong test" — artifact download extension regression.
 *
 * From chat d28ef78e (2026-06-10, "code me a pong game"): the model
 * produced an HTML/JS Pong game as an artifact (content_type
 * 'text/html', sometimes with language 'html', sometimes with language
 * unset), but the download button saved it as a `.txt` file because
 * getExtension() had no case for text/html and fell through to the
 * `.txt` default. An HTML game saved as .txt won't open in a browser.
 *
 * The model's other failures in that chat (no persona switch, duplicate
 * answer, multiple redundant artifacts, hallucinated download URLs) are
 * non-deterministic model behaviour and belong in an eval, not here.
 * This file pins the deterministic property: a given (content_type,
 * language) always maps to the correct file extension.
 */

describe('getExtension — Pong / HTML download regression', () => {
  it('maps an HTML artifact to .html (language=html)', () => {
    expect(getExtension('text/html', 'html')).toBe('.html');
  });

  it('maps an HTML artifact to .html when language is missing (the bug case)', () => {
    // The turn-1 Pong artifacts had content_type text/html but no language.
    expect(getExtension('text/html', undefined)).toBe('.html');
    expect(getExtension('text/html', '')).toBe('.html');
  });

  it('is case-insensitive on language', () => {
    expect(getExtension('text/html', 'HTML')).toBe('.html');
  });

  it('maps common code languages by language field', () => {
    expect(getExtension('text/plain', 'python')).toBe('.py');
    expect(getExtension('text/plain', 'javascript')).toBe('.js');
    expect(getExtension('text/plain', 'typescript')).toBe('.ts');
    expect(getExtension('text/plain', 'css')).toBe('.css');
  });

  it('falls back to content_type when language is absent', () => {
    expect(getExtension('application/json')).toBe('.json');
    expect(getExtension('text/markdown')).toBe('.md');
    expect(getExtension('text/latex')).toBe('.tex');
    expect(getExtension('image/svg+xml')).toBe('.svg');
    expect(getExtension('application/pdf')).toBe('.pdf');
    expect(getExtension('text/plain')).toBe('.txt');
  });

  it('prefers language over content_type when both are present', () => {
    // A python snippet stored with a generic text/plain content_type
    // should still download as .py.
    expect(getExtension('text/plain', 'python')).toBe('.py');
  });

  it('defaults to .txt only for genuinely unknown types', () => {
    expect(getExtension('application/octet-stream')).toBe('.txt');
    expect(getExtension('', undefined)).toBe('.txt');
  });
});
