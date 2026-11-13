import { act, render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../test/msw-server';
import { MessageList } from './MessageList';
import { detectPhase } from '../lib/streamPhase';
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

describe('MessageList long unbroken text', () => {
  it('lets a user bubble break a long sequence instead of overflowing', () => {
    const content = 'MKT'.repeat(200);
    const msg: Message = { id: 'u', role: 'user', content, created_at: '' } as Message;
    render(<MessageList messages={[msg]} streaming={makeStreaming()} />);
    expect(screen.getByText(content).className).toContain('wrap-anywhere');
  });
});

describe('MessageList wording', () => {
  it('describes a retry in plain words and keeps the raw reason in a title', () => {
    render(
      <MessageList
        messages={[]}
        streaming={makeStreaming({
          phase: 'thinking',
          retrying: { attempt: 2, maxAttempts: 5, reason: 'upstream 503' },
        } as never)}
      />,
    );
    const line = screen.getByText(/Munin is having trouble reaching the model/);
    expect(line).toHaveTextContent('Retrying (2 of 5)');
    expect(line).not.toHaveTextContent('vLLM');
    expect(line).not.toHaveTextContent('503');
    expect(line.parentElement).toHaveAttribute('title', 'upstream 503');
  });

  it('rewords the stream interrupted marker on a saved message', () => {
    const msg = {
      id: 'a', role: 'assistant', created_at: '',
      content: 'Partial\n\n_(stream interrupted: vLLM unavailable)_',
    } as Message;
    render(<MessageList messages={[msg]} streaming={makeStreaming()} />);
    const body = screen.getByTestId('markdown');
    expect(body).toHaveTextContent('This answer was cut off. The model is unavailable right now.');
    expect(body).not.toHaveTextContent('vLLM');
  });
});

describe('MessageList jump button while content grows', () => {
  const original = globalThis.ResizeObserver;
  let notify: () => void = () => {};

  beforeEach(() => {
    globalThis.ResizeObserver = class {
      constructor(cb: ResizeObserverCallback) { notify = () => cb([], this as unknown as ResizeObserver); }
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver;
  });
  afterEach(() => { globalThis.ResizeObserver = original; });

  it('appears when an answer grows past the bottom, with no scroll event or auto-scroll', () => {
    const spy = vi.spyOn(Element.prototype, 'scrollIntoView');
    const { container } = render(<MessageList messages={[]} streaming={makeStreaming({ phase: 'generating' } as never)} />);
    expect(screen.queryByRole('button', { name: 'Scroll to bottom' })).not.toBeInTheDocument();

    const scroller = container.querySelector('.overflow-y-auto') as HTMLElement;
    Object.defineProperty(scroller, 'scrollHeight', { configurable: true, value: 2000 });
    Object.defineProperty(scroller, 'clientHeight', { configurable: true, value: 500 });
    act(() => notify());

    expect(screen.getByRole('button', { name: 'Scroll to bottom' })).toBeInTheDocument();
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });
});

describe('MessageList plan card wiring', () => {
  it('revoking auto-approve fires none of the callbacks that send a message', async () => {
    let body: unknown = null;
    server.use(
      http.post('/api/chats/:id/plan/approve', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ approved: true });
      }),
    );
    const onPlanApproved = vi.fn();
    const onPlanRejected = vi.fn();
    const onPlanEdited = vi.fn();
    const onContinue = vi.fn();
    const msg = {
      id: 'a', role: 'assistant', content: 'Working', created_at: '',
      plan_snapshot: {
        conversation_id: 'c1',
        items: [{ id: 's1', title: 'Search', status: 'pending' }],
        requires_approval: true, approved_at: '2026-01-01', approval_mode: 'auto',
        created_at: '', updated_at: '',
      },
    } as unknown as Message;
    render(
      <MessageList
        messages={[msg]}
        streaming={makeStreaming()}
        conversationId="c1"
        onPlanApproved={onPlanApproved}
        onPlanRejected={onPlanRejected}
        onPlanEdited={onPlanEdited}
        onContinue={onContinue}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Revoke' }));
    await screen.findByText(/asking before each step/i);
    expect(body).toEqual({ mode: 'each' });
    expect(onPlanEdited).not.toHaveBeenCalled();
    expect(onPlanApproved).not.toHaveBeenCalled();
    expect(onPlanRejected).not.toHaveBeenCalled();
    expect(onContinue).not.toHaveBeenCalled();
  });
});

// ── detectPhase tests ───────────────────────────────────────────────────────

describe('detectPhase', () => {
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

  it('renders the research timeline inside the scroll container, after the messages', () => {
    render(
      <MessageList
        messages={[assistantMsg]}
        streaming={makeStreaming()}
        researchTimeline={<div data-testid="dr-timeline-stub">timeline</div>}
      />,
    );

    const timeline = screen.getByTestId('dr-timeline-stub');
    // Must live inside the scrollable transcript so it scrolls with the chat,
    // not as a sibling pinned above the composer.
    const scroll = timeline.closest('.overflow-y-auto');
    expect(scroll).not.toBeNull();
  });

  it('omits the research timeline when none is passed', () => {
    render(<MessageList messages={[assistantMsg]} streaming={makeStreaming()} />);
    expect(screen.queryByTestId('dr-timeline-stub')).toBeNull();
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

    // Motion means work is happening, so a finished turn shows no vortex at all.
    expect(screen.queryAllByTestId('feather-vortex')).toHaveLength(0);
  });

  it('left-aligns the working state', () => {
    render(
      <MessageList messages={[userMsg]} streaming={makeStreaming({ phase: 'thinking', content: '' })} />,
    );

    const wrapper = screen.getByTestId('feather-vortex').parentElement;
    expect(wrapper).toHaveClass('justify-start');
    expect(wrapper).not.toHaveClass('justify-center');
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

    // A just-sent message goes to the top of the view, as in Claude, and the
    // reply streams into the space under it (2026-11). jsdom has no layout,
    // so the rectangles are stubbed: the scroll area starts at y=100, the
    // transcript ends at y=760 and the sent message starts at y=700.
    describe('a sent message goes to the top', () => {
      const rectAt = (top: number, bottom = top) => ({ top, bottom, left: 0, right: 0, width: 0, height: bottom - top, x: 0, y: top, toJSON: () => ({}) }) as DOMRect;
      let scrollToSpy: ReturnType<typeof vi.fn>;
      let rectSpy: ReturnType<typeof vi.spyOn>;

      beforeEach(() => {
        scrollToSpy = vi.fn();
        (Element.prototype as unknown as { scrollTo: unknown }).scrollTo = scrollToSpy;
        rectSpy = vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
          if (this.classList.contains('overflow-y-auto')) return rectAt(100, 900);
          if (this.getAttribute('data-message-id') === userMsg.id) return rectAt(700, 740);
          if (this.classList.contains('space-y-6')) return rectAt(100, 760);
          return rectAt(0);
        });
      });
      afterEach(() => {
        rectSpy.mockRestore();
        delete (Element.prototype as unknown as { scrollTo?: unknown }).scrollTo;
      });

      const geometry = (container: HTMLElement) => {
        const el = findScrollContainer(container);
        setScrollGeometry(el, { scrollTop: 300, clientHeight: 800, scrollHeight: 1100 });
        return el;
      };

      it('scrolls the new user message to the top of the view, once and smoothly', () => {
        const { container, rerender } = render(
          <MessageList messages={[assistantMsg]} streaming={makeStreaming()} conversationId="c1" />,
        );
        geometry(container);
        scrollSpy.mockClear();

        rerender(<MessageList messages={[assistantMsg, userMsg]} streaming={makeStreaming()} conversationId="c1" />);
        // 300 scrolled + (700 - 100) down the view - the 24px gap above it.
        expect(scrollToSpy).toHaveBeenCalledTimes(1);
        expect(scrollToSpy).toHaveBeenCalledWith({ top: 876, behavior: 'smooth' });
        expect(scrollSpy).not.toHaveBeenCalled();
      });

      it('leaves room below so the message can reach the top', () => {
        const { container, rerender } = render(
          <MessageList messages={[assistantMsg]} streaming={makeStreaming()} conversationId="c1" />,
        );
        geometry(container);
        rerender(<MessageList messages={[assistantMsg, userMsg]} streaming={makeStreaming()} conversationId="c1" />);
        // 800 tall - 24 gap - 60 from the message to the end of the transcript.
        expect(screen.getByTestId('sent-message-spacer').style.height).toBe('716px');
      });

      it('does not scroll again while the reply streams in', () => {
        const { container, rerender } = render(
          <MessageList messages={[assistantMsg]} streaming={makeStreaming()} conversationId="c1" />,
        );
        geometry(container);
        rerender(<MessageList messages={[assistantMsg, userMsg]} streaming={makeStreaming({ phase: 'generating', content: 'a' })} conversationId="c1" />);
        scrollToSpy.mockClear();
        scrollSpy.mockClear();
        rerender(<MessageList messages={[assistantMsg, userMsg]} streaming={makeStreaming({ phase: 'generating', content: 'ab' })} conversationId="c1" />);
        rerender(<MessageList messages={[assistantMsg, userMsg]} streaming={makeStreaming({ phase: 'generating', content: 'abc' })} conversationId="c1" />);
        expect(scrollToSpy).not.toHaveBeenCalled();
        expect(scrollSpy).not.toHaveBeenCalled();
      });

      it("pins a new chat's first message, and keeps it there when the chat gets its id", () => {
        const { container, rerender } = render(
          <MessageList messages={[userMsg]} streaming={makeStreaming({ phase: 'thinking' })} conversationId={null} />,
        );
        expect(scrollToSpy).toHaveBeenCalledTimes(1);
        const el = geometry(container);
        rerender(<MessageList messages={[userMsg]} streaming={makeStreaming({ phase: 'thinking' })} conversationId="new-id" />);
        // No jump to the bottom: the message stays where it was put.
        expect(el.scrollTop).toBe(300);
      });

      it('opens another conversation at the bottom with no room left below', () => {
        const { container, rerender } = render(
          <MessageList messages={[assistantMsg]} streaming={makeStreaming()} conversationId="c1" />,
        );
        const el = geometry(container);
        rerender(<MessageList messages={[assistantMsg, userMsg]} streaming={makeStreaming()} conversationId="c1" />);
        rerender(<MessageList messages={[assistantMsg]} streaming={makeStreaming()} conversationId="c2" />);
        expect(el.scrollTop).toBe(1100);
        expect(screen.getByTestId('sent-message-spacer').style.height).toBe('0px');
      });
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
      expect(scrollSpy).toHaveBeenCalledWith({ behavior: 'smooth', block: 'end' });
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
      expect(vortexes()).toHaveLength(0);
      expect(screen.queryByText('Building your game...')).not.toBeInTheDocument();
    });
  });
});

// ── Inline artifacts ────────────────────────────────────────────────────────

describe('MessageList inline artifacts', () => {
  const runPython = {
    name: 'run_python',
    arguments: {},
    result: {
      artifacts: [{
        registered_artifact_id: 'art_1',
        filename: 'plot.png',
        content_type: 'image/png',
        size_bytes: 2048,
        external_url: '/api/artifacts/c1/hash',
      }],
    },
  };

  it('shows a plot from a finished message, as after a reload', () => {
    const messages: Message[] = [{
      id: 'm1', role: 'assistant', content: 'Here it is.', tool_calls: [runPython],
      created_at: new Date().toISOString(),
    }];
    render(<MessageList messages={messages} streaming={makeStreaming()} conversationId="c1" />);
    expect(screen.getByRole('img', { name: 'plot.png' })).toBeInTheDocument();
  });

  it('shows a plot while the turn is still streaming', () => {
    render(
      <MessageList
        messages={[]}
        streaming={makeStreaming({ phase: 'tool_call', toolCalls: [runPython] })}
        conversationId="c1"
      />,
    );
    expect(screen.getByRole('img', { name: 'plot.png' })).toBeInTheDocument();
  });
});

describe('MessageList Continue', () => {
  it('is a primary button, since it is the one action at that point', () => {
    const onContinue = vi.fn();
    const msg = {
      id: 'a', role: 'assistant', content: 'Halfway', created_at: '', paused_at_budget: true,
    } as unknown as Message;
    render(<MessageList messages={[msg]} streaming={makeStreaming()} onContinue={onContinue} />);
    const button = screen.getByRole('button', { name: 'Continue' });
    expect(button.className).toContain('bg-accent');
    expect(button.className).toContain('cursor-pointer');
    expect(button.className).toContain('pointer-coarse:min-h-9');
    fireEvent.click(button);
    expect(onContinue).toHaveBeenCalledTimes(1);
  });
});
