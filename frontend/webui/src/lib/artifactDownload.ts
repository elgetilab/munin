// Download-extension resolution for artifacts.
//
// `language` (the model-declared code language) is the most specific
// signal, so check it first; fall back to `content_type`. Before
// 2026-06, text/html artifacts (i.e. every HTML/JS game like Pong) hit
// the `.txt` default because neither `text/html` nor `language='html'`
// was mapped, so they downloaded as .txt and wouldn't open in a
// browser (chat d28ef78e). Keep both maps in sync as new artifact kinds
// are added.

const LANGUAGE_EXT: Record<string, string> = {
  python: '.py',
  html: '.html',
  javascript: '.js',
  typescript: '.ts',
  css: '.css',
  json: '.json',
  markdown: '.md',
  latex: '.tex',
  tex: '.tex',
  bash: '.sh',
  sh: '.sh',
  sql: '.sql',
  yaml: '.yaml',
  yml: '.yaml',
  svg: '.svg',
};

const CONTENT_TYPE_EXT: Record<string, string> = {
  'text/html': '.html',
  'application/python': '.py',
  'text/markdown': '.md',
  'text/latex': '.tex',
  'application/json': '.json',
  'image/svg+xml': '.svg',
  'text/css': '.css',
  'application/javascript': '.js',
  'text/javascript': '.js',
  'text/plain': '.txt',
  'application/pdf': '.pdf',
};

export function getExtension(contentType: string, language?: string): string {
  const lang = language?.trim().toLowerCase();
  if (lang && LANGUAGE_EXT[lang]) return LANGUAGE_EXT[lang];
  if (contentType && CONTENT_TYPE_EXT[contentType]) return CONTENT_TYPE_EXT[contentType];
  return '.txt';
}
