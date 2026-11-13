import { useEffect, useState } from 'react';
import { copyText } from '../lib/copyText';
import { useUiStore } from '../stores/uiStore';
import { replyExcerpt } from '../lib/replyReport';
import { CheckIcon, CopyIcon, FlagIcon } from './ui/icons';

const BUTTON =
  'inline-flex items-center justify-center w-8 h-8 rounded-md text-text-secondary hover:text-text-primary hover:bg-bg-tertiary transition-colors cursor-pointer';

/**
 * The quiet row under a finished reply: Copy, and Report when the chat can be
 * reported. Always shown on the latest reply; on older ones it appears on hover
 * or keyboard focus, and always where there is no hover (touch screens).
 */
export function ReplyActions({ content, replyNumber, latest, canReport }: {
  /** The reply's markdown, as copied. */
  content: string;
  replyNumber: number;
  latest: boolean;
  canReport: boolean;
}) {
  const openReportForReply = useUiStore(s => s.openReportForReply);
  const [copy, setCopy] = useState<'idle' | 'copied' | 'failed'>('idle');

  useEffect(() => {
    if (copy === 'idle') return;
    const t = setTimeout(() => setCopy('idle'), 2000);
    return () => clearTimeout(t);
  }, [copy]);

  const onCopy = async () => setCopy((await copyText(content)) ? 'copied' : 'failed');

  return (
    <div
      className={`flex items-center gap-0.5 -ml-1.5 ${latest ? '' : 'opacity-0 group-hover/reply:opacity-100 focus-within:opacity-100 [@media(hover:none)]:opacity-100 transition-opacity'}`}
    >
      <button type="button" onClick={onCopy} className={BUTTON} aria-label="Copy reply" title={copy === 'failed' ? "Couldn't copy" : 'Copy'}>
        {copy === 'copied' ? <CheckIcon size={15} /> : <CopyIcon size={15} />}
      </button>
      {canReport && (
        <button
          type="button"
          onClick={() => openReportForReply({ number: replyNumber, excerpt: replyExcerpt(content) })}
          className={BUTTON}
          aria-label="Report this reply"
          title="Report this reply"
        >
          <FlagIcon size={15} />
        </button>
      )}
      <span role="status" className={copy === 'failed' ? 'text-xs text-error ml-1' : 'sr-only'}>
        {copy === 'copied' ? 'Copied' : copy === 'failed' ? "Couldn't copy" : ''}
      </span>
    </div>
  );
}
