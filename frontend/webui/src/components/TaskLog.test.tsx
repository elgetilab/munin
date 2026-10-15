import { render } from '@testing-library/react';
import { TaskLog } from './TaskLog';
import type { ToolCall } from '../lib/types';

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
  'update_artifact', 'list_artifacts', 'save_artifact_to_documents',
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
