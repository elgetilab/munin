import { fireEvent, render, screen } from '@testing-library/react';
import { TaskLog } from './TaskLog';
import type { ToolCall } from '../lib/types';

describe('TaskLog result summary', () => {
  const row = (result: unknown) => {
    const call: ToolCall = { id: 't', name: 'web_fetch', arguments: {}, result } as ToolCall;
    render(<TaskLog toolCalls={[call]} />);
    fireEvent.click(screen.getByTestId('activity-line'));
    return screen.getAllByRole('button')[1];
  };

  it('shows a tool that returned an error as failed, not done', () => {
    const button = row({ error: 'Timed out after 30s' });
    expect(button).toHaveTextContent('failed');
    expect(button).not.toHaveTextContent('done');
    expect(button.querySelector('.text-warning svg')).not.toBeNull();
  });

  it('still shows a recognised or unrecognised success normally', () => {
    expect(row({ ok: true })).toHaveTextContent('done');
  });

  it('does not treat an empty error field as a failure', () => {
    expect(row({ papers: [{}, {}], error: null })).toHaveTextContent('2 papers');
  });

  it.each([
    [{ papers: [{}] }, '1 paper'],
    [{ results: [{}] }, '1 result'],
    [{ results: [{}, {}] }, '2 results'],
  ])('pluralises counts: %j', (result, text) => {
    const button = row(result);
    expect(button.textContent).toContain(text);
    expect(button.textContent).not.toMatch(/\b1 (papers|results)\b/);
  });

  it('lets long unbroken values wrap in the expanded view', () => {
    row({ error: 'x' });
    const pres = document.querySelectorAll('pre');
    expect(pres.length).toBeGreaterThan(0);
    pres.forEach(p => expect(p.className).toContain('wrap-anywhere'));
  });
});

// Mirrors the keys of MCP_TOOLS in backend/retrieval/mcp/schemas.py, the
// live tool registry. Hand-copied: when a tool is added there, add it here
// and give it an icon in TaskLog.tsx's TOOL_ICON_MAP.
const REGISTRY_TOOLS = [
  'web_search', 'paper_search', 'semantic_scholar_search', 'paper_lookup',
  'llm_summarize', 'web_fetch', 'source', 'search', 'compute',
  'get_citations', 'get_references', 's2_get_citations', 's2_get_references',
  'get_author_papers', 'get_paper_pdf', 'check_papers_availability',
  'search_user_docs', 'list_documents', 'browse_tag_papers',
  'transcribe_equation', 'view_attachment', 'create_artifact', 'read_artifact',
  'update_artifact', 'list_artifacts', 'save_artifact_to_documents', 'show_protein',
  'recall', 'list_projects', 'get_current_project',
  'search_past_conversations', 'calculate', 'export_citations',
  'edit_python', 'run_python', 'sandbox_reset',
  'compile_latex', 'ask_clarification', 'faq', 'tool_search', 'set_plan',
  'update_plan_item',
];

// Retired tools that persisted conversations can still carry in tool_calls.
const HISTORY_TOOLS = [
  'read_paper', 'compare_papers', 'invoke_agent', 'deep_research', 'remember',
  'forget',
];

// Rendered markup of the row's leading icon span for a single tool call.
function iconMarkup(name: string): string {
  const call: ToolCall = { id: name, name, arguments: {} };
  const { container, unmount } = render(<TaskLog toolCalls={[call]} />);
  const span = container.querySelector('span.flex-shrink-0');
  const html = span?.innerHTML ?? '';
  unmount();
  return html;
}

describe('TaskLog tool icons', () => {
  it('gives every registry and history tool a specific icon, not the gear fallback', () => {
    const fallback = iconMarkup('__not_a_real_tool__');
    expect(fallback).toContain('<svg');
    const missing = [...REGISTRY_TOOLS, ...HISTORY_TOOLS].filter(
      (name) => iconMarkup(name) === fallback,
    );
    expect(missing).toEqual([]);
  });
});

describe('TaskLog rows', () => {
  // Opening the activity line shows everything; each part can still be closed.
  it('are real buttons, all open once the line is opened, each closable', () => {
    render(<TaskLog thinking="Mulling it over" toolCalls={[{ id: 't1', name: 'web_search', arguments: {} }]} />);
    fireEvent.click(screen.getByTestId('activity-line'));
    const rows = screen.getAllByRole('button').slice(1);
    expect(rows).toHaveLength(2);
    for (const row of rows) {
      expect(row).toHaveAttribute('type', 'button');
      expect(row).toHaveAttribute('aria-expanded', 'true');
    }
    expect(screen.getByText('Mulling it over')).toBeInTheDocument();

    fireEvent.click(rows[0]);
    expect(rows[0]).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('Mulling it over')).not.toBeInTheDocument();
  });
});

describe('TaskLog expanded rows', () => {
  const open = (call: ToolCall) => {
    const view = render(<TaskLog toolCalls={[call]} />);
    fireEvent.click(screen.getByTestId('activity-line'));
    return view;
  };

  it('lists paper titles linked to doi.org, with the JSON behind Show raw', () => {
    open({
      id: 'p', name: 'paper_search', arguments: { query: 'kinase' },
      result: { papers: [
        { title: 'Kinase atlas', doi: '10.1000/abc', year: 2021 },
        { title: 'Web only', url: 'https://example.org/paper' },
        { title: 'No link' },
        { title: 'Bad link', url: 'javascript:alert(1)' },
      ] },
    });
    expect(screen.getByRole('link', { name: 'Kinase atlas' })).toHaveAttribute('href', 'https://doi.org/10.1000/abc');
    expect(screen.getByText('(2021)')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Web only' })).toHaveAttribute('href', 'https://example.org/paper');
    expect(screen.queryByRole('link', { name: 'No link' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'Bad link' })).toBeNull();
    expect(screen.getByText('Bad link')).toBeInTheDocument();

    expect(screen.queryByText(/"doi": "10.1000\/abc"/)).toBeNull();
    const raw = screen.getByRole('button', { name: 'Show raw' });
    fireEvent.click(raw);
    expect(raw).toHaveTextContent('Hide raw');
    expect(screen.getByText(/"doi": "10.1000\/abc"/)).toBeInTheDocument();
  });

  it('falls back to raw JSON when results have no titles', () => {
    open({ id: 'r', name: 'calculate', arguments: {}, result: { results: [{ value: 4 }] } });
    expect(screen.queryByRole('button', { name: 'Show raw' })).toBeNull();
    expect(screen.getByText(/"value": 4/)).toBeInTheDocument();
  });

  it('shows run_python code as a code block, not an escaped string', () => {
    const code = 'import numpy as np\nprint(np.pi)';
    const { container } = open({ id: 'c', name: 'run_python', arguments: { code, timeout: 60 } });
    const block = container.querySelector('pre code');
    expect(block?.textContent).toBe(code);
    expect(container.textContent).not.toContain('\\n');
    expect(screen.getByText(/"timeout": 60/)).toBeInTheDocument();
  });

  it('caps the Arguments box height like the Result box', () => {
    open({ id: 'a', name: 'web_fetch', arguments: { url: 'https://x.org' }, result: { ok: true } });
    const boxes = screen.getAllByText(/^(Arguments|Result)$/).map(label => label.parentElement!);
    expect(boxes).toHaveLength(2);
    boxes.forEach(box => expect(box.className).toContain('max-h-48'));
    boxes.forEach(box => expect(box.className).toContain('overflow-y-auto'));
  });

  it('links a retrieved document DOI to doi.org', () => {
    render(<TaskLog ragContext={{
      sources_used: ['papers'],
      documents: [
        { title: 'Doc A', source: 'papers', score: 0.9, doi: '10.5555/xyz' },
        { title: 'Doc B', source: 'papers', score: 0.8, doi: 'not-a-doi' },
      ],
    }} />);
    fireEvent.click(screen.getByTestId('activity-line'));
    expect(screen.getByRole('link', { name: '10.5555/xyz' })).toHaveAttribute('href', 'https://doi.org/10.5555/xyz');
    expect(screen.queryByRole('link', { name: 'not-a-doi' })).toBeNull();
    expect(screen.getByText(/not-a-doi/)).toBeInTheDocument();
  });
});

describe('TaskLog activity line', () => {
  const call = (name: string, result?: unknown): ToolCall => ({ id: name + String(Math.random()), name, arguments: {}, result } as ToolCall);

  it('sums a finished reply up in one line, closed until asked', () => {
    render(<TaskLog toolCalls={[call('web_search', { results: [{}] })]} />);
    const line = screen.getByTestId('activity-line');
    expect(line).toHaveTextContent('Searched the web · 1 result');
    expect(line).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText(/web search/)).not.toBeInTheDocument();
    fireEvent.click(line);
    expect(line).toHaveAttribute('aria-expanded', 'true');
    expect(document.getElementById(line.getAttribute('aria-controls')!)).toHaveTextContent('web search');
  });

  it('says what is running while the reply streams', () => {
    render(<TaskLog isStreaming toolCalls={[call('paper_search', { papers: [] }), call('web_fetch')]} />);
    expect(screen.getByTestId('activity-line')).toHaveTextContent('Reading a web page…');
  });

  it('counts the steps of a longer turn and names failures apart', () => {
    render(<TaskLog toolCalls={[call('web_search', { results: [] }), call('web_fetch', { error: 'timeout' }), call('web_fetch', { ok: true })]} />);
    const line = screen.getByTestId('activity-line');
    expect(line).toHaveTextContent('Searched the web and read web pages · 3 steps');
    expect(line).toHaveTextContent('1 failed');
  });
});
