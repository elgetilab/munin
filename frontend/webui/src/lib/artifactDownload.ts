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
  'application/vnd.munin.widget+json': '.json',
  'image/svg+xml': '.svg',
  'text/css': '.css',
  'application/javascript': '.js',
  'text/javascript': '.js',
  'text/plain': '.txt',
  'application/pdf': '.pdf',
  'text/csv': '.csv',
  'image/png': '.png',
  'image/jpeg': '.jpg',
  'image/webp': '.webp',
  'image/gif': '.gif',
};

// Other spellings of an extension above: report.yml is not missing its .yaml.
const SAME_EXTENSION: Record<string, string[]> = {
  '.yaml': ['.yml'],
  '.jpg': ['.jpeg'],
  '.html': ['.htm'],
};

function knownExtension(contentType: string, language?: string): string | null {
  const lang = language?.trim().toLowerCase();
  if (lang && LANGUAGE_EXT[lang]) return LANGUAGE_EXT[lang];
  if (contentType && CONTENT_TYPE_EXT[contentType]) return CONTENT_TYPE_EXT[contentType];
  return null;
}

export function getExtension(contentType: string, language?: string): string {
  return knownExtension(contentType, language) ?? '.txt';
}

/**
 * The name to save an artifact under. A sandbox file's title already is its
 * filename, and its type may be unknown (model.ckpt), so it only gains an
 * extension if it has none and its type names one. A model-written title is a
 * label, so it gains one unless it already ends in it.
 */
export function downloadFilename(artifact: {
  title: string;
  filename?: string | null;
  content_type: string;
  language?: string | null;
  source?: string;
}): string {
  const name = artifact.filename || artifact.title;
  const ext = knownExtension(artifact.content_type, artifact.language ?? undefined);
  if (artifact.source === 'sandbox_generated') {
    return ext && !/\.[A-Za-z0-9]{1,8}$/.test(name) ? `${name}${ext}` : name;
  }
  const wanted = ext ?? '.txt';
  const lower = name.toLowerCase();
  const done = [wanted, ...(SAME_EXTENSION[wanted] ?? [])].some(e => lower.endsWith(e));
  return done ? name : `${name}${wanted}`;
}

export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
