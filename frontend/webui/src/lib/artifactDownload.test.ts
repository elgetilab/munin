import { downloadFilename, getExtension } from './artifactDownload';

describe('downloadFilename', () => {
  const sandbox = (title: string, content_type: string) => ({ title, filename: title, content_type, source: 'sandbox_generated' });
  const written = (title: string, content_type: string, language?: string) => ({ title, content_type, language, source: 'model_written' });

  it('keeps a sandbox filename as it is, whatever its type', () => {
    expect(downloadFilename(sandbox('plot.png', 'image/png'))).toBe('plot.png');
    expect(downloadFilename(sandbox('model.ckpt', 'application/octet-stream'))).toBe('model.ckpt');
    expect(downloadFilename(sandbox('data.csv', 'text/csv'))).toBe('data.csv');
    expect(downloadFilename(sandbox('paper.pdf', 'application/pdf'))).toBe('paper.pdf');
  });

  it('never invents .txt for a sandbox file of unknown type', () => {
    expect(downloadFilename(sandbox('unnamed', 'application/octet-stream'))).toBe('unnamed');
  });

  it('gives an extensionless sandbox file the one its type names', () => {
    expect(downloadFilename(sandbox('unnamed', 'image/png'))).toBe('unnamed.png');
  });

  it('prefers the full filename over a title the store may have cut short', () => {
    expect(downloadFilename({ title: 'long-na', filename: 'long-name.csv', content_type: 'text/csv', source: 'sandbox_generated' })).toBe('long-name.csv');
  });

  it('does not double the extension on a model-written title', () => {
    expect(downloadFilename(written('analysis.py', 'application/python'))).toBe('analysis.py');
    expect(downloadFilename(written('ANALYSIS.PY', 'text/plain', 'python'))).toBe('ANALYSIS.PY');
    expect(downloadFilename(written('config.yml', 'text/plain', 'yaml'))).toBe('config.yml');
  });

  it('appends the extension to a model-written title without one', () => {
    expect(downloadFilename(written('Analysis Report', 'text/markdown'))).toBe('Analysis Report.md');
    expect(downloadFilename(written('Version 1.5 notes', 'text/plain'))).toBe('Version 1.5 notes.txt');
  });
});

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
