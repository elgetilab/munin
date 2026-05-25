/**
 * Markdown renderer for model-written assistant content (and artifacts).
 *
 * THREAT MODEL (P1 #18, audit 2026-05-25)
 * ----------------------------------------
 * The model writes arbitrary markdown that we render verbatim. Treat
 * `content` as hostile input. We rely on three guarantees, all
 * enforced below:
 *
 *  1. No raw HTML execution. We do NOT pass `rehype-raw`; with
 *     react-markdown 10+ this means `<script>`, `<iframe>`,
 *     `onerror=` etc. in the source are rendered as literal text,
 *     not as DOM elements. Do not add `rehype-raw` without a
 *     sanitiser (e.g. `rehype-sanitize`) in front of it.
 *
 *  2. No dangerous URL schemes. The `urlTransform` below is an
 *     explicit copy of react-markdown's `defaultUrlTransform`
 *     allowlist (http, https, ircs?, mailto, xmpp). Anything else
 *     — `javascript:`, `data:`, `vbscript:`, `file:`, custom
 *     schemes — is replaced with `''`. We re-declare it here so a
 *     future react-markdown default change cannot silently widen
 *     the allowlist.
 *
 *  3. No inline event handlers. The components map below never
 *     uses `dangerouslySetInnerHTML`. The `{...props}` spreads only
 *     forward attributes that react-markdown's HAST→JSX pipeline
 *     has already filtered (className, title, id, etc. — no
 *     `onClick`, no `style` strings from source).
 *
 * Regression coverage in `Markdown.test.tsx` — adding `rehype-raw`,
 * removing `urlTransform`, or relaxing the protocol allowlist will
 * turn that suite red.
 */
import { useState, type ComponentPropsWithoutRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { PrismLight as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import python from 'react-syntax-highlighter/dist/esm/languages/prism/python';
import javascript from 'react-syntax-highlighter/dist/esm/languages/prism/javascript';
import typescript from 'react-syntax-highlighter/dist/esm/languages/prism/typescript';
import bash from 'react-syntax-highlighter/dist/esm/languages/prism/bash';
import json from 'react-syntax-highlighter/dist/esm/languages/prism/json';
import yaml from 'react-syntax-highlighter/dist/esm/languages/prism/yaml';
import markdown from 'react-syntax-highlighter/dist/esm/languages/prism/markdown';
import css from 'react-syntax-highlighter/dist/esm/languages/prism/css';
import sql from 'react-syntax-highlighter/dist/esm/languages/prism/sql';
import jsx from 'react-syntax-highlighter/dist/esm/languages/prism/jsx';
import tsx from 'react-syntax-highlighter/dist/esm/languages/prism/tsx';
import cpp from 'react-syntax-highlighter/dist/esm/languages/prism/cpp';
import java from 'react-syntax-highlighter/dist/esm/languages/prism/java';
import rust from 'react-syntax-highlighter/dist/esm/languages/prism/rust';
import go from 'react-syntax-highlighter/dist/esm/languages/prism/go';
import docker from 'react-syntax-highlighter/dist/esm/languages/prism/docker';
import toml from 'react-syntax-highlighter/dist/esm/languages/prism/toml';

SyntaxHighlighter.registerLanguage('python', python);
SyntaxHighlighter.registerLanguage('py', python);
SyntaxHighlighter.registerLanguage('javascript', javascript);
SyntaxHighlighter.registerLanguage('js', javascript);
SyntaxHighlighter.registerLanguage('typescript', typescript);
SyntaxHighlighter.registerLanguage('ts', typescript);
SyntaxHighlighter.registerLanguage('bash', bash);
SyntaxHighlighter.registerLanguage('sh', bash);
SyntaxHighlighter.registerLanguage('shell', bash);
SyntaxHighlighter.registerLanguage('json', json);
SyntaxHighlighter.registerLanguage('yaml', yaml);
SyntaxHighlighter.registerLanguage('yml', yaml);
SyntaxHighlighter.registerLanguage('markdown', markdown);
SyntaxHighlighter.registerLanguage('md', markdown);
SyntaxHighlighter.registerLanguage('css', css);
SyntaxHighlighter.registerLanguage('sql', sql);
SyntaxHighlighter.registerLanguage('jsx', jsx);
SyntaxHighlighter.registerLanguage('tsx', tsx);
SyntaxHighlighter.registerLanguage('cpp', cpp);
SyntaxHighlighter.registerLanguage('c', cpp);
SyntaxHighlighter.registerLanguage('java', java);
SyntaxHighlighter.registerLanguage('rust', rust);
SyntaxHighlighter.registerLanguage('rs', rust);
SyntaxHighlighter.registerLanguage('go', go);
SyntaxHighlighter.registerLanguage('docker', docker);
SyntaxHighlighter.registerLanguage('dockerfile', docker);
SyntaxHighlighter.registerLanguage('toml', toml);

/** Customise oneDark to blend with Munin's bg-secondary */
const highlightStyle: Record<string, React.CSSProperties> = {
  ...oneDark,
  'pre[class*="language-"]': {
    ...(oneDark['pre[class*="language-"]'] as React.CSSProperties),
    background: 'var(--color-bg-secondary)',
    margin: 0,
    padding: '0.75rem 1rem',
    fontSize: '13px',
    lineHeight: '1.6',
  },
  'code[class*="language-"]': {
    ...(oneDark['code[class*="language-"]'] as React.CSSProperties),
    background: 'none',
    fontSize: '13px',
  },
};

function CodeBlock({ className, children }: ComponentPropsWithoutRef<'code'>) {
  const [copied, setCopied] = useState(false);
  const match = /language-(\w+)/.exec(className || '');
  const lang = match ? match[1] : '';
  const code = String(children).replace(/\n$/, '');

  const handleCopy = () => {
    navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="relative group my-3 rounded-lg overflow-hidden border border-border">
      {/* Header bar */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-bg-tertiary text-[11px] text-text-secondary">
        <span>{lang || 'code'}</span>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1 hover:text-text-primary transition-colors cursor-pointer"
        >
          {copied ? (
            <>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
              Copied
            </>
          ) : (
            <>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
              Copy
            </>
          )}
        </button>
      </div>
      {lang ? (
        <SyntaxHighlighter
          style={highlightStyle}
          language={lang}
          PreTag="div"
          customStyle={{ background: 'var(--color-bg-secondary)', margin: 0, borderRadius: 0 }}
        >
          {code}
        </SyntaxHighlighter>
      ) : (
        <pre className="px-4 py-3 overflow-x-auto bg-bg-secondary text-[13px] leading-relaxed">
          <code>{code}</code>
        </pre>
      )}
    </div>
  );
}

function InlineCode({ children, ...props }: ComponentPropsWithoutRef<'code'>) {
  return (
    <code className="px-1.5 py-0.5 bg-bg-tertiary border border-border rounded text-[13px]" {...props}>
      {children}
    </code>
  );
}

/**
 * URL allowlist for `href` / `src` attributes. Mirror of
 * react-markdown's defaultUrlTransform so a future upstream default
 * change cannot widen what we accept. Anything not matching
 * `safeProtocol` returns '' which renders as a no-op anchor.
 */
const safeProtocol = /^(https?|ircs?|mailto|xmpp)$/i;
function safeUrlTransform(value: string): string {
  // Adapted from react-markdown's defaultUrlTransform.
  const colon = value.indexOf(':');
  const questionMark = value.indexOf('?');
  const numberSign = value.indexOf('#');
  const slash = value.indexOf('/');
  if (
    colon === -1 ||
    (slash !== -1 && colon > slash) ||
    (questionMark !== -1 && colon > questionMark) ||
    (numberSign !== -1 && colon > numberSign) ||
    safeProtocol.test(value.slice(0, colon))
  ) {
    return value;
  }
  return '';
}

export function Markdown({ content }: { content: string }) {
  return (
    <div className="prose-munin">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={safeUrlTransform}
        components={{
          code({ className, children, ...props }) {
            const isBlock = /language-/.test(className || '') ||
              (typeof children === 'string' && children.includes('\n'));
            if (isBlock) {
              return <CodeBlock className={className} {...props}>{children}</CodeBlock>;
            }
            return <InlineCode {...props}>{children}</InlineCode>;
          },
          pre({ children }) {
            return <>{children}</>;
          },
          a({ href, children, ...props }) {
            return <a href={href} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline" {...props}>{children}</a>;
          },
          table({ children, ...props }) {
            return (
              <div className="overflow-x-auto my-3">
                <table className="min-w-full text-sm border border-border" {...props}>{children}</table>
              </div>
            );
          },
          th({ children, ...props }) {
            return <th className="px-3 py-2 bg-bg-tertiary border border-border text-left font-semibold" {...props}>{children}</th>;
          },
          td({ children, ...props }) {
            return <td className="px-3 py-2 border border-border" {...props}>{children}</td>;
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
