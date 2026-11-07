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

// The frame cannot read the theme cookie or the app's CSS, so WidgetFrame sends
// the theme with each state message. This runs before the widget's own scripts
// and puts it on <html> as data-theme, the attribute the app uses, so a widget's
// CSS can follow it with :root[data-theme="light"] rules. Until the first
// message there is no data-theme, which a widget should treat as dark.
export const WIDGET_THEME_SCRIPT = [
  '(function () {',
  '  var root = document.documentElement;',
  "  window.addEventListener('message', function (event) {",
  "    if (event.source !== window.parent || !event.data || event.data.type !== 'state') return;",
  '    var theme = event.data.theme;',
  "    if (theme !== 'light' && theme !== 'dark') return;",
  "    root.setAttribute('data-theme', theme);",
  '    root.style.colorScheme = theme;',
  '  });',
  '})();',
].join('\n');

export function buildWidgetDocument({ scripts, css = '', body = '' }: { scripts: string[]; css?: string; body?: string }): string {
  return [
    '<!doctype html><html><head><meta charset="utf-8">',
    `<meta http-equiv="Content-Security-Policy" content="${WIDGET_CSP}">`,
    `<script>${WIDGET_THEME_SCRIPT}</script>`,
    `<style>${css}</style></head><body>${body}`,
    ...scripts.map(code => `<script>${escapeInlineScript(code)}</script>`),
    '</body></html>',
  ].join('');
}
