// The HTML document a widget runs in. It is rendered in an iframe with only
// allow-scripts, which gives it an opaque origin: no cookies, no storage and no
// reach into the app. This policy also cuts it off from the network, so its code
// has to arrive inside the document and its data by postMessage.
export const WIDGET_CSP = [
  "default-src 'none'",
  "script-src 'unsafe-inline'",
  "style-src 'unsafe-inline'",
  'img-src data: blob:',
  'worker-src blob:',
  "base-uri 'none'",
  "form-action 'none'",
].join('; ');

// Keeps script text from ending its <script> element or opening an HTML comment.
// Both replacements mean the same thing inside a JavaScript string or regex.
function escapeInlineScript(code: string): string {
  return code.replace(/<\/(script)/gi, '<\\/$1').replace(/<!--/g, '<\\!--');
}

export function buildWidgetDocument({ scripts, css = '', body = '' }: { scripts: string[]; css?: string; body?: string }): string {
  return [
    '<!doctype html><html><head><meta charset="utf-8">',
    `<meta http-equiv="Content-Security-Policy" content="${WIDGET_CSP}">`,
    `<style>${css}</style></head><body>${body}`,
    ...scripts.map(code => `<script>${escapeInlineScript(code)}</script>`),
    '</body></html>',
  ].join('');
}
