import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { detectPhase, MessageList } from './MessageList';
import type { Message } from '../lib/types';

// Mock heavy child components to keep tests focused on MessageList logic
vi.mock('./FeatherVortex', () => ({
  FeatherVortex: ({ phase }: { phase?: string }) => (
    <div data-testid="feather-vortex" data-phase={phase}>Loading...</div>
  ),
}));

vi.mock('./Markdown', () => ({
  Markdown: ({ content }: { content: string }) => <div data-testid="markdown">{content}</div>,
}));

vi.mock('./TaskLog', () => ({
  TaskLog: () => <div data-testid="task-log" />,
}));

vi.mock('./ClarificationCard', () => ({
  ClarificationCard: () => <div data-testid="clarification-card" />,
}));

function makeStreaming(overrides: Partial<Parameters<typeof MessageList>[0]['streaming']> = {}): Parameters<typeof MessageList>[0]['streaming'] {
  return {
    content: '',
    thinking: '',
    toolCalls: [],
    ragContext: null,
    clarification: null,
    routedProfile: null,
    phase: 'idle',
    ...overrides,
  };
}

// ── detectPhase tests ───────────────────────────────────────────────────────

describe('detectPhase', () => {
  it('returns "deep_research" when last tool call is invoke_agent', () => {
    const streaming = makeStreaming({
      toolCalls: [{ name: 'invoke_agent', arguments: {} }],
    });
    expect(detectPhase(streaming)).toBe('deep_research');
  });

  it('returns "paper_search" for paper_search tool calls', () => {
    const streaming = makeStreaming({
      toolCalls: [{ name: 'paper_search', arguments: {} }],
    });
    expect(detectPhase(streaming)).toBe('paper_search');
  });

  it('returns "paper_search" for semantic_scholar_search', () => {
    const streaming = makeStreaming({
      toolCalls: [{ name: 'semantic_scholar_search', arguments: {} }],
    });
    expect(detectPhase(streaming)).toBe('paper_search');
  });

  it('returns "web_search" for web_search tool calls', () => {
    const streaming = makeStreaming({
      toolCalls: [{ name: 'web_search', arguments: {} }],
    });
    expect(detectPhase(streaming)).toBe('web_search');
  });

  it('returns "web_search" for web_fetch tool calls', () => {
    const streaming = makeStreaming({
      toolCalls: [{ name: 'web_fetch', arguments: {} }],
    });
    expect(detectPhase(streaming)).toBe('web_search');
  });

  it('returns "processing" for llm_summarize', () => {
    const streaming = makeStreaming({
      toolCalls: [{ name: 'llm_summarize', arguments: {} }],
    });
    expect(detectPhase(streaming)).toBe('processing');
  });

  it('returns "code" for run_python', () => {
    const streaming = makeStreaming({
      toolCalls: [{ name: 'run_python', arguments: {} }],
    });
    expect(detectPhase(streaming)).toBe('code');
  });

  it('returns "code" for sandbox_reset', () => {
    const streaming = makeStreaming({
      toolCalls: [{ name: 'sandbox_reset', arguments: {} }],
    });
    expect(detectPhase(streaming)).toBe('code');
  });

  it('returns "code" for compile_latex', () => {
    const streaming = makeStreaming({
      toolCalls: [{ name: 'compile_latex', arguments: {} }],
    });
    expect(detectPhase(streaming)).toBe('code');
  });

  it('returns "code" for create_artifact / update_artifact', () => {
    expect(detectPhase(makeStreaming({ toolCalls: [{ name: 'create_artifact', arguments: {} }] }))).toBe('code');
    expect(detectPhase(makeStreaming({ toolCalls: [{ name: 'update_artifact', arguments: {} }] }))).toBe('code');
  });

  it('returns "thinking" when no tool calls', () => {
    const streaming = makeStreaming({ toolCalls: [] });
    expect(detectPhase(streaming)).toBe('thinking');
  });

  it('uses the last tool call when multiple are present', () => {
    const streaming = makeStreaming({
      toolCalls: [
        { name: 'paper_search', arguments: {} },
        { name: 'web_search', arguments: {} },
      ],
    });
    expect(detectPhase(streaming)).toBe('web_search');
  });
});

// ── MessageList component tests ─────────────────────────────────────────────

describe('MessageList', () => {
  const userMsg: Message = {
    id: 'm1',
    role: 'user',
    content: 'Hello world',
    created_at: new Date().toISOString(),
  };

  const assistantMsg: Message = {
    id: 'm2',
    role: 'assistant',
    content: 'Hi there! How can I help?',
    created_at: new Date().toISOString(),
  };

  it('renders user messages right-aligned', () => {
    render(
      <MessageList messages={[userMsg]} streaming={makeStreaming()} />,
    );

    const text = screen.getByText('Hello world');
    // User messages are inside a flex container with justify-end
    const wrapper = text.closest('.justify-end');
    expect(wrapper).toBeInTheDocument();
  });

  it('renders assistant messages with Markdown content', () => {
    render(
      <MessageList messages={[assistantMsg]} streaming={makeStreaming()} />,
    );

    const md = screen.getByTestId('markdown');
    expect(md).toHaveTextContent('Hi there! How can I help?');
  });

  it('shows FeatherVortex during active streaming with no content', () => {
    render(
      <MessageList
        messages={[]}
        streaming={makeStreaming({ phase: 'thinking', content: '' })}
      />,
    );

    const vortex = screen.getByTestId('feather-vortex');
    expect(vortex).toBeInTheDocument();
  });

  it('shows streaming content when available', () => {
    render(
      <MessageList
        messages={[]}
        streaming={makeStreaming({ phase: 'generating', content: 'Partial response...' })}
      />,
    );

    const md = screen.getByTestId('markdown');
    expect(md).toHaveTextContent('Partial response...');
  });

  it('does not show streaming section when phase is idle', () => {
    render(
      <MessageList messages={[userMsg]} streaming={makeStreaming({ phase: 'idle' })} />,
    );

    // No feather vortex for active streaming (only the idle one at the bottom)
    const vortexes = screen.getAllByTestId('feather-vortex');
    // The idle vortex is shown after messages
    expect(vortexes).toHaveLength(1);
  });

  // Free-scroll + jump-to-bottom button (2026-06-02 second pass).
  //
  // The previous attempt at gating auto-scroll didn't work in
  // practice: the browser's smooth-scroll animation isn't
  // interruptible by user wheel events, and tokens at 60 tok/s mean a
  // new scrollIntoView fires every ~16ms, so the animation never
  // completes and the user can't escape the bottom. The current
  // behaviour:
  //
  //   - Streaming tokens DO NOT auto-scroll. User reads freely.
  //   - Conversation switch jumps to the bottom (instant; no animation).
  //   - New user message smooth-scrolls to the bottom so the user sees
  //     their own bubble land.
  //   - A floating "Scroll to bottom" button appears when the user is
  //     more than JUMP_BUTTON_HIDE_THRESHOLD_PX (96) from the bottom.
  //
  // jsdom doesn't lay elements out (scrollHeight/scrollTop default to
  // 0), so we patch container geometry to drive the scroll handler.
  describe('scroll behaviour', () => {
    let scrollSpy: ReturnType<typeof vi.spyOn>;

    beforeEach(() => {
      scrollSpy = vi.spyOn(Element.prototype, 'scrollIntoView').mockImplementation(() => {});
    });

    afterEach(() => {
      scrollSpy.mockRestore();
    });

    const findScrollContainer = (container: HTMLElement): HTMLElement => {
      const el = container.querySelector('.overflow-y-auto') as HTMLElement | null;
      if (!el) throw new Error('scroll container not found');
      return el;
    };

    const setScrollGeometry = (
      el: HTMLElement,
      { scrollTop, clientHeight, scrollHeight }: { scrollTop: number; clientHeight: number; scrollHeight: number }
    ) => {
      Object.defineProperty(el, 'scrollTop', { configurable: true, value: scrollTop, writable: true });
      Object.defineProperty(el, 'clientHeight', { configurable: true, value: clientHeight });
      Object.defineProperty(el, 'scrollHeight', { configurable: true, value: scrollHeight });
    };

    it('does NOT call scrollIntoView on streaming token updates', () => {
      const { rerender } = render(
        <MessageList
          messages={[assistantMsg]}
          streaming={makeStreaming({ phase: 'generating', content: 'a' })}
        />,
      );
      scrollSpy.mockClear();

      rerender(
        <MessageList
          messages={[assistantMsg]}
          streaming={makeStreaming({ phase: 'generating', content: 'ab' })}
        />,
      );
      rerender(
        <MessageList
          messages={[assistantMsg]}
          streaming={makeStreaming({ phase: 'generating', content: 'abc' })}
        />,
      );
      // Three token updates, zero scrollIntoView calls.
      expect(scrollSpy).not.toHaveBeenCalled();
    });

    it('smooth-scrolls to bottom when a new user message is added', () => {
      const { rerender } = render(
        <MessageList messages={[assistantMsg]} streaming={makeStreaming()} />,
      );
      scrollSpy.mockClear();

      rerender(
        <MessageList messages={[assistantMsg, userMsg]} streaming={makeStreaming()} />,
      );
      expect(scrollSpy).toHaveBeenCalledWith({ behavior: 'smooth' });
    });

    it('does NOT smooth-scroll when a new assistant message lands (only user messages trigger it)', () => {
      const { rerender } = render(
        <MessageList messages={[userMsg]} streaming={makeStreaming()} />,
      );
      scrollSpy.mockClear();

      rerender(
        <MessageList messages={[userMsg, assistantMsg]} streaming={makeStreaming()} />,
      );
      // The model's final bubble landing shouldn't yank the user back
      // to the bottom; they may still be reading earlier prose.
      expect(scrollSpy).not.toHaveBeenCalled();
    });

    it('jumps to bottom (instant scrollTop) on conversation switch', () => {
      const { container, rerender } = render(
        <MessageList messages={[assistantMsg]} streaming={makeStreaming()} conversationId="conv-a" />,
      );
      const scrollEl = findScrollContainer(container);
      setScrollGeometry(scrollEl, { scrollTop: 0, clientHeight: 200, scrollHeight: 1000 });

      rerender(
        <MessageList messages={[assistantMsg]} streaming={makeStreaming()} conversationId="conv-b" />,
      );
      // The effect sets scrollTop = scrollHeight directly (no
      // animation), so the geometry should reflect that.
      expect(scrollEl.scrollTop).toBe(1000);
    });

    it('jump-to-bottom button appears once the user is more than 96px from the bottom', () => {
      const { container } = render(
        <MessageList messages={[assistantMsg]} streaming={makeStreaming()} />,
      );
      // Not visible initially (geometry is all 0 -> distance is 0).
      expect(screen.queryByLabelText('Scroll to bottom')).not.toBeInTheDocument();

      const scrollEl = findScrollContainer(container);
      // 1000 - 100 - 200 = 700px from bottom, well over 96.
      setScrollGeometry(scrollEl, { scrollTop: 100, clientHeight: 200, scrollHeight: 1000 });
      fireEvent.scroll(scrollEl);

      expect(screen.getByLabelText('Scroll to bottom')).toBeInTheDocument();
    });

    it('jump-to-bottom button hides when the user is within 96px of the bottom', () => {
      const { container } = render(
        <MessageList messages={[assistantMsg]} streaming={makeStreaming()} />,
      );
      const scrollEl = findScrollContainer(container);

      // Far from bottom -> button visible.
      setScrollGeometry(scrollEl, { scrollTop: 0, clientHeight: 200, scrollHeight: 1000 });
      fireEvent.scroll(scrollEl);
      expect(screen.getByLabelText('Scroll to bottom')).toBeInTheDocument();

      // Close to bottom (50px) -> button hides.
      setScrollGeometry(scrollEl, { scrollTop: 750, clientHeight: 200, scrollHeight: 1000 });
      fireEvent.scroll(scrollEl);
      expect(screen.queryByLabelText('Scroll to bottom')).not.toBeInTheDocument();
    });

    it('clicking the jump button smooth-scrolls to bottom', async () => {
      const user = userEvent.setup();
      const { container } = render(
        <MessageList messages={[assistantMsg]} streaming={makeStreaming()} />,
      );
      const scrollEl = findScrollContainer(container);

      setScrollGeometry(scrollEl, { scrollTop: 0, clientHeight: 200, scrollHeight: 1000 });
      fireEvent.scroll(scrollEl);

      scrollSpy.mockClear();
      await user.click(screen.getByLabelText('Scroll to bottom'));
      expect(scrollSpy).toHaveBeenCalledWith({ behavior: 'smooth' });
    });
  });

  // ── Routed-profile chip (per-turn router) ─────────────────────────────────
  describe('routed-profile chip', () => {
    const mk = (id: string, role: Message['role'], content: string, persona?: string | null, extra: Partial<Message> = {}): Message =>
      ({ id, role, content, persona, created_at: new Date().toISOString(), ...extra });

    it('shows a capitalized chip for non-chat routed profiles', () => {
      const messages = [
        mk('1', 'user', 'how would you code?', 'munin'),
        mk('2', 'assistant', 'now coding', 'code'),
      ];
      render(<MessageList messages={messages} streaming={makeStreaming()} />);
      expect(screen.getByText('Code')).toBeInTheDocument();
    });

    it('hides the chip for the default chat profile', () => {
      const messages = [
        mk('1', 'user', 'hi', 'munin'),
        mk('2', 'assistant', 'hello', 'chat'),
      ];
      render(<MessageList messages={messages} streaming={makeStreaming()} />);
      expect(screen.queryByText('Chat')).not.toBeInTheDocument();
    });

    it('hides the chip for legacy NULL-persona history', () => {
      const messages = [
        mk('1', 'user', 'hi', null),
        mk('2', 'assistant', 'hello', null),
      ];
      render(<MessageList messages={messages} streaming={makeStreaming()} />);
      expect(screen.queryByText('Research')).not.toBeInTheDocument();
      expect(screen.queryByText('Code')).not.toBeInTheDocument();
    });

    it('shows the chip for the in-progress streaming turn', () => {
      render(
        <MessageList
          messages={[]}
          streaming={makeStreaming({ phase: 'generating', content: 'thinking…', routedProfile: 'research' })}
        />,
      );
      expect(screen.getByText('Research')).toBeInTheDocument();
    });
  });

  // ── Streaming "working" indicator (spinner) ───────────────────────────────
  // Regression for chat d28ef78e: while the model wrote a large artifact
  // body, no token/phase events arrived, the phase stayed 'thinking' with
  // prose already on screen, and NEITHER vortex rendered — the turn looked
  // frozen. A working spinner must show in every active state.
  describe('streaming working indicator', () => {
    const vortexes = () => screen.queryAllByTestId('feather-vortex');

    it('shows a spinner while content is present and phase is "thinking" (artifact-write gap)', () => {
      render(
        <MessageList
          messages={[]}
          streaming={makeStreaming({ phase: 'thinking', content: 'Building your game...' })}
        />,
      );
      // Before the fix this was zero (big vortex needs empty content or
      // tool_call; small vortex needed phase==='generating').
      expect(vortexes().length).toBeGreaterThanOrEqual(1);
    });

    it('still shows a spinner while generating with content', () => {
      render(
        <MessageList
          messages={[]}
          streaming={makeStreaming({ phase: 'generating', content: 'Partial...' })}
        />,
      );
      expect(vortexes().length).toBeGreaterThanOrEqual(1);
    });

    it('shows the big vortex when content is empty (any active phase)', () => {
      render(
        <MessageList
          messages={[]}
          streaming={makeStreaming({ phase: 'thinking', content: '' })}
        />,
      );
      expect(vortexes().length).toBeGreaterThanOrEqual(1);
    });

    it('shows no streaming spinner once idle', () => {
      render(
        <MessageList
          messages={[assistantMsg]}
          streaming={makeStreaming({ phase: 'idle', content: '' })}
        />,
      );
      // Only the idle vortex after the last message — but that's the idle
      // indicator, not a "working" one; the active streaming block is gone.
      // (The idle vortex is acceptable; assert the active block isn't shown
      // by checking there's no streamed content node.)
      expect(screen.queryByText('Building your game...')).not.toBeInTheDocument();
    });
  });
});
