import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../test/msw-server';
import { ReplyActions } from './ReplyActions';
import { ReportDialog } from './ReportDialog';
import { MessageList } from './MessageList';
import { useUiStore, _resetUiStoreForTests } from '../stores/uiStore';
import { replyExcerpt, replyReportPrefix } from '../lib/replyReport';
import type { Message } from '../lib/types';

vi.mock('./FeatherVortex', () => ({ FeatherVortex: () => <div /> }));

const REPLY = '## Folding\n\nProteins **fold** into their native structure.\n\n```python\nprint(1)\n```';

describe('ReplyActions', () => {
  beforeEach(() => _resetUiStoreForTests());

  it('copies the reply as markdown and says so', async () => {
    const user = userEvent.setup();
    const write = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue(undefined);
    render(<ReplyActions content={REPLY} replyNumber={2} latest canReport />);
    await user.click(screen.getByRole('button', { name: 'Copy reply' }));
    expect(write).toHaveBeenCalledWith(REPLY);
    expect(screen.getByRole('status')).toHaveTextContent('Copied');
    write.mockRestore();
  });

  it("says when the browser refuses the copy", async () => {
    const user = userEvent.setup();
    const write = vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValue(new Error('denied'));
    render(<ReplyActions content={REPLY} replyNumber={2} latest canReport />);
    await user.click(screen.getByRole('button', { name: 'Copy reply' }));
    expect(screen.getByRole('status')).toHaveTextContent("Couldn't copy");
    write.mockRestore();
  });

  it('opens the report dialog about this reply', async () => {
    const user = userEvent.setup();
    render(<ReplyActions content={REPLY} replyNumber={2} latest canReport />);
    await user.click(screen.getByRole('button', { name: 'Report this reply' }));
    const s = useUiStore.getState();
    expect(s.showReportDialog).toBe(true);
    expect(s.reportReply).toEqual({ number: 2, excerpt: 'Folding Proteins fold into their native structure.' });
  });

  it('has no Report button where the chat cannot be reported', () => {
    render(<ReplyActions content={REPLY} replyNumber={1} latest canReport={false} />);
    expect(screen.queryByRole('button', { name: 'Report this reply' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Copy reply' })).toBeInTheDocument();
  });

  it('stays visible on the latest reply and waits for hover or focus on older ones', () => {
    const { container, rerender } = render(<ReplyActions content={REPLY} replyNumber={1} latest canReport />);
    expect(container.firstElementChild).not.toHaveClass('opacity-0');
    rerender(<ReplyActions content={REPLY} replyNumber={1} latest={false} canReport />);
    expect(container.firstElementChild).toHaveClass('opacity-0', 'group-hover/reply:opacity-100', 'focus-within:opacity-100');
  });
});

describe('ReplyActions in the message list', () => {
  const mk = (id: string, role: Message['role'], content: string): Message => ({ id, role, content, created_at: new Date().toISOString() });
  const idle = { phase: 'idle', content: '', thinking: '', toolCalls: [], ragContext: null } as never;

  it('numbers the replies and marks only the last as latest', () => {
    render(<MessageList messages={[mk('u1', 'user', 'a'), mk('a1', 'assistant', 'first'), mk('u2', 'user', 'b'), mk('a2', 'assistant', 'second')]} streaming={idle} canReport />);
    const copies = screen.getAllByRole('button', { name: 'Copy reply' });
    expect(copies).toHaveLength(2);
    expect(copies[0].parentElement).toHaveClass('opacity-0');
    expect(copies[1].parentElement).not.toHaveClass('opacity-0');
    fireEvent.click(screen.getAllByRole('button', { name: 'Report this reply' })[0]);
    expect(useUiStore.getState().reportReply).toEqual({ number: 1, excerpt: 'first' });
  });

  it('gives a user message no actions', () => {
    render(<MessageList messages={[mk('u1', 'user', 'hello')]} streaming={idle} canReport />);
    expect(screen.queryByRole('button', { name: 'Copy reply' })).not.toBeInTheDocument();
  });
});

describe('ReportDialog about one reply', () => {
  beforeEach(() => _resetUiStoreForTests());

  it('names the reply in its title, quotes it, and leads the reason with it', async () => {
    const user = userEvent.setup();
    let body: { reason?: string } = {};
    server.use(http.post('*/api/chats/c1/report', async ({ request }) => { body = await request.json() as { reason?: string }; return HttpResponse.json({ ok: true }); }));
    useUiStore.getState().openReportForReply({ number: 3, excerpt: 'Proteins fold' });
    const onReported = vi.fn();
    render(<ReportDialog conversationId="c1" onReported={onReported} />);
    expect(screen.getByText('Report this reply')).toBeInTheDocument();
    expect(screen.getByText('Proteins fold')).toBeInTheDocument();
    await user.type(screen.getByRole('textbox'), 'Wrong citation');
    await user.click(screen.getByRole('button', { name: 'Send report' }));
    await waitFor(() => expect(onReported).toHaveBeenCalled());
    expect(body.reason).toBe('About reply 3: "Proteins fold"\n\nWrong citation');
  });

  it('keeps the whole reason within the backend limit of 2000', () => {
    useUiStore.getState().openReportForReply({ number: 3, excerpt: 'Proteins fold' });
    render(<ReportDialog conversationId="c1" onReported={vi.fn()} />);
    const max = 2000 - replyReportPrefix({ number: 3, excerpt: 'Proteins fold' }).length;
    expect(screen.getByText(`0/${max}`)).toBeInTheDocument();
  });

  it('is about the whole chat again when opened from the header', () => {
    useUiStore.getState().openReportForReply({ number: 3, excerpt: 'x' });
    useUiStore.getState().setShowReportDialog(true);
    expect(useUiStore.getState().reportReply).toBeNull();
  });
});

describe('replyExcerpt', () => {
  it('drops code blocks and markdown marks and shortens long replies', () => {
    expect(replyExcerpt(REPLY)).toBe('Folding Proteins fold into their native structure.');
    expect(replyExcerpt('word '.repeat(50), 20)).toBe('word word word word…');
  });
});

describe('Retry and Edit in the message list', () => {
  const mk = (id: string, role: Message['role'], content: string): Message => ({ id, role, content, created_at: new Date().toISOString() });
  const idle = { phase: 'idle', content: '', thinking: '', toolCalls: [], ragContext: null } as never;
  const busy = { phase: 'generating', content: 'x', thinking: '', toolCalls: [], ragContext: null } as never;
  const convo = [mk('u1', 'user', 'first'), mk('a1', 'assistant', 'one'), mk('u2', 'user', 'second'), mk('a2', 'assistant', 'two')];

  it('offers Retry on the latest reply only, sending its question in its place', async () => {
    const user = userEvent.setup();
    const onResend = vi.fn();
    render(<MessageList messages={convo} streaming={idle} onResend={onResend} />);
    const retries = screen.getAllByRole('button', { name: 'Retry' });
    expect(retries).toHaveLength(1);
    await user.click(retries[0]);
    expect(onResend).toHaveBeenCalledWith('u2', 'second');
  });

  it('edits a message in place: Enter sends the new text, Escape keeps the old', async () => {
    const user = userEvent.setup();
    const onResend = vi.fn();
    render(<MessageList messages={convo} streaming={idle} onResend={onResend} />);
    const pencils = screen.getAllByRole('button', { name: 'Edit message' });
    expect(pencils).toHaveLength(2);

    await user.click(pencils[1]);
    const box = screen.getByRole('textbox', { name: 'Edit your message' });
    expect(box).toHaveValue('second');
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('textbox', { name: 'Edit your message' })).not.toBeInTheDocument();
    expect(onResend).not.toHaveBeenCalled();

    await user.click(screen.getAllByRole('button', { name: 'Edit message' })[1]);
    await user.clear(screen.getByRole('textbox', { name: 'Edit your message' }));
    await user.type(screen.getByRole('textbox', { name: 'Edit your message' }), 'second, better{Enter}');
    expect(onResend).toHaveBeenCalledWith('u2', 'second, better');
  });

  it('warns when an edit would drop later turns, and not for the last message', async () => {
    const user = userEvent.setup();
    render(<MessageList messages={convo} streaming={idle} onResend={vi.fn()} />);
    await user.click(screen.getAllByRole('button', { name: 'Edit message' })[0]);
    expect(screen.getByText('Sending replaces this message and everything after it.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    await user.click(screen.getAllByRole('button', { name: 'Edit message' })[1]);
    expect(screen.queryByText('Sending replaces this message and everything after it.')).not.toBeInTheDocument();
  });

  it('offers neither while a reply streams, in a temporary chat, or for unsaved or attachment messages', () => {
    const { rerender } = render(<MessageList messages={convo} streaming={busy} onResend={vi.fn()} />);
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Edit message' })).not.toBeInTheDocument();

    rerender(<MessageList messages={convo} streaming={idle} />);
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Edit message' })).not.toBeInTheDocument();

    const odd = [mk('temp-1', 'user', 'unsaved'), mk('a1', 'assistant', 'one'), { ...mk('u2', 'user', 'see file'), attachments: [{ type: 'document' }] }, mk('a2', 'assistant', 'two')];
    rerender(<MessageList messages={odd} streaming={idle} onResend={vi.fn()} />);
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Edit message' })).not.toBeInTheDocument();
  });
});

describe('modes in the message list', () => {
  const mk = (id: string, role: Message['role'], content: string, persona?: string): Message => ({ id, role, content, persona, created_at: new Date().toISOString() });
  const idle = { phase: 'idle', content: '', thinking: '', toolCalls: [], ragContext: null } as never;

  it('shows a sent command as a label over the bubble, not in the text', () => {
    render(<MessageList messages={[mk('u1', 'user', '/research GPCR desensitisation')]} streaming={idle} />);
    expect(screen.getByText('GPCR desensitisation')).toBeInTheDocument();
    expect(screen.queryByText(/\/research/)).not.toBeInTheDocument();
    expect(screen.getByTestId('sent-mode')).toHaveTextContent('Research');
  });

  it("labels a reply's routed mode in its action row, and plain chat not at all", () => {
    render(<MessageList messages={[mk('u1', 'user', 'q'), mk('a1', 'assistant', 'one', 'code'), mk('u2', 'user', 'q2'), mk('a2', 'assistant', 'two', 'chat')]} streaming={idle} />);
    const labels = screen.getAllByTestId('mode-label');
    expect(labels).toHaveLength(1);
    expect(labels[0]).toHaveTextContent('Code');
  });
});
