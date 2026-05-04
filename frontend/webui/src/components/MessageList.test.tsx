import { render, screen } from '@testing-library/react';
import { detectPhase, MessageList } from './MessageList';
import type { Message, ToolCall } from '../lib/types';

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
    delegations: [],
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
});
