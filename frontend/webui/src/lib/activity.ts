// The one line a reply's tool activity collapses to, as in Claude:
// "Searched the web · 1 result", "Searching papers…". The detailed rows
// stay behind it (TaskLog).
import type { RagContext, ToolCall } from './types';

// [while it runs, once it is done]
const PHRASES: Record<string, [string, string]> = {
  paper_search: ['Searching papers', 'Searched papers'],
  semantic_scholar_search: ['Searching papers', 'Searched papers'],
  browse_tag_papers: ['Browsing papers', 'Browsed papers'],
  paper_lookup: ['Looking up papers', 'Looked up papers'],
  check_papers_availability: ['Checking papers', 'Checked papers'],
  source: ['Reading a paper', 'Read papers'],
  read_paper: ['Reading a paper', 'Read papers'],
  compare_papers: ['Comparing papers', 'Compared papers'],
  s2_get_citations: ['Finding citations', 'Found citations'],
  get_citations: ['Finding citations', 'Found citations'],
  s2_get_references: ['Finding references', 'Found references'],
  get_references: ['Finding references', 'Found references'],
  get_author_papers: ["Finding an author's papers", "Found an author's papers"],
  export_citations: ['Exporting citations', 'Exported citations'],
  web_search: ['Searching the web', 'Searched the web'],
  web_fetch: ['Reading a web page', 'Read web pages'],
  search: ['Searching', 'Searched'],
  tool_search: ['Looking for a tool', 'Looked for tools'],
  llm_summarize: ['Summarising', 'Summarised'],
  get_paper_pdf: ['Fetching a PDF', 'Fetched PDFs'],
  search_user_docs: ['Searching your documents', 'Searched your documents'],
  list_documents: ['Listing your documents', 'Listed your documents'],
  run_python: ['Running code', 'Ran code'],
  compute: ['Running code', 'Ran code'],
  edit_python: ['Editing code', 'Edited code'],
  sandbox_reset: ['Resetting the sandbox', 'Reset the sandbox'],
  compile_latex: ['Compiling LaTeX', 'Compiled LaTeX'],
  ask_clarification: ['Asking a question', 'Asked a question'],
  set_plan: ['Making a plan', 'Made a plan'],
  update_plan_item: ['Updating the plan', 'Updated the plan'],
  calculate: ['Calculating', 'Calculated'],
  faq: ['Checking the FAQ', 'Checked the FAQ'],
  recall: ['Checking memory', 'Checked memory'],
  remember: ['Saving to memory', 'Saved to memory'],
  forget: ['Forgetting', 'Forgot'],
  create_artifact: ['Writing an artifact', 'Wrote an artifact'],
  read_artifact: ['Reading an artifact', 'Read an artifact'],
  update_artifact: ['Updating an artifact', 'Updated an artifact'],
  list_artifacts: ['Listing artifacts', 'Listed artifacts'],
  show_protein: ['Showing a structure', 'Showed a structure'],
  save_artifact_to_documents: ['Saving to your documents', 'Saved to your documents'],
  list_projects: ['Checking projects', 'Checked projects'],
  get_current_project: ['Checking projects', 'Checked projects'],
  search_past_conversations: ['Searching past chats', 'Searched past chats'],
  view_attachment: ['Viewing an attachment', 'Viewed an attachment'],
  transcribe_equation: ['Reading an equation', 'Read an equation'],
  invoke_agent: ['Asking an agent', 'Asked an agent'],
  deep_research: ['Researching', 'Researched'],
};

/** What a tool is doing, or did. Unknown tools fall back to their name. */
export function toolPhrase(name: string, done: boolean): string {
  const known = PHRASES[name];
  if (known) return known[done ? 1 : 0];
  const spaced = name.replace(/_/g, ' ');
  return done ? `Used ${spaced}` : `Using ${spaced}`;
}

const lower = (s: string) => s.charAt(0).toLowerCase() + s.slice(1);

/** The steps as one phrase: "Searched the web, read web pages and 2 more". */
function joinSteps(parts: string[]): string {
  const shown = parts.length > 3 ? [...parts.slice(0, 2), `${parts.length - 2} more`] : parts;
  return shown.map((p, i) => (i === 0 ? p : lower(p))).join(', ').replace(/, ([^,]*)$/, shown.length > 1 ? ' and $1' : ', $1');
}

export interface ActivityLine {
  text: string;
  /** Tool calls that returned an error, shown apart in the warning colour. */
  failed: number;
  /** True while something is still running. */
  active: boolean;
}

export function activityLine({ thinking, toolCalls, ragContext, thinkingDuration, isStreaming, summarise }: {
  thinking?: string | null;
  toolCalls?: ToolCall[] | null;
  ragContext?: RagContext | null;
  thinkingDuration?: number;
  isStreaming?: boolean;
  /** One tool's result as a short count ("3 papers"), from TaskLog. */
  summarise: (result: unknown) => string;
}): ActivityLine {
  const calls = toolCalls ?? [];
  const failed = calls.filter(c => c.result && typeof c.result === 'object' && (c.result as { error?: unknown }).error).length;
  const running = isStreaming ? calls.find(c => c.result == null) : undefined;
  if (running) return { text: `${toolPhrase(running.name, false)}…`, failed, active: true };
  if (isStreaming && calls.length === 0 && thinking) return { text: 'Thinking…', failed, active: true };

  const parts: string[] = [];
  if (ragContext && ragContext.documents.length > 0) parts.push('Searched the knowledge base');
  if (thinking && calls.length === 0) parts.push(thinkingDuration ? `Thought for ${(thinkingDuration / 1000).toFixed(1)}s` : 'Thought it through');
  for (const c of calls) {
    const p = toolPhrase(c.name, true);
    if (!parts.includes(p)) parts.push(p);
  }
  let text = joinSteps(parts);
  if (calls.length === 1 && !failed) {
    const summary = summarise(calls[0].result);
    if (summary) text += ` · ${summary}`;
  } else if (calls.length > 1) {
    text += ` · ${calls.length} steps`;
  }
  return { text, failed, active: false };
}
