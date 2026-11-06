import { fireEvent, render, screen } from '@testing-library/react';
import { TaskLog } from './TaskLog';
import type { ToolCall } from '../lib/types';

describe('TaskLog result summary', () => {
  const row = (result: unknown) => {
    const call: ToolCall = { id: 't', name: 'web_fetch', arguments: {}, result } as ToolCall;
    render(<TaskLog toolCalls={[call]} />);
    return screen.getByRole('button');
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
    const button = row({ error: 'x' });
    fireEvent.click(button);
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
  'remember', 'forget', 'recall', 'list_projects', 'get_current_project',
  'search_past_conversations', 'invoke_agent', 'calculate', 'export_citations',
  'deep_research', 'edit_python', 'run_python', 'sandbox_reset',
  'compile_latex', 'ask_clarification', 'faq', 'tool_search', 'set_plan',
  'update_plan_item',
];

// Retired tools that persisted conversations can still carry in tool_calls.
const HISTORY_TOOLS = ['read_paper', 'compare_papers'];

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
  it('are real buttons that report whether they are expanded', () => {
    render(<TaskLog thinking="Mulling it over" toolCalls={[{ id: 't1', name: 'web_search', arguments: {} }]} />);
    const rows = screen.getAllByRole('button');
    expect(rows).toHaveLength(2);
    for (const row of rows) {
      expect(row).toHaveAttribute('type', 'button');
      expect(row).toHaveAttribute('aria-expanded', 'false');
    }

    fireEvent.click(rows[0]);
    expect(rows[0]).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('Mulling it over')).toBeInTheDocument();
  });
});
