/**
 * Maps the tool calls of an in-flight turn to a vortex phase name.
 *
 * Lives here rather than beside the component that uses it because a
 * module that exports both a component and a plain function breaks React
 * Fast Refresh (react-refresh/only-export-components). It is a pure
 * function with its own tests, so a lib module is where it belongs.
 */

import type { ToolCall } from './types';


export function detectPhase(streaming: { toolCalls: ToolCall[] }): string {
  if (streaming.toolCalls.length > 0) {
    const last = streaming.toolCalls[streaming.toolCalls.length - 1];
    if (last.name === 'invoke_agent') return 'deep_research';
    if (['paper_search', 'semantic_scholar_search', 'paper_lookup', 'get_citations', 'get_references', 'get_author_papers', 'check_papers_availability'].includes(last.name)) {
      return 'paper_search';
    }
    if (['web_search', 'web_fetch'].includes(last.name)) return 'web_search';
    if (['run_python', 'sandbox_reset', 'compile_latex'].includes(last.name)) return 'code';
    if (['create_artifact', 'update_artifact'].includes(last.name)) return 'code';
    if (last.name === 'llm_summarize') return 'processing';
  }
  return 'thinking';
}
