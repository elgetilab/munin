// ── Personas ─────────────────────────────────────────────────────────────────

export interface Persona {
  id: string;
  name: string;
  description: string;
  icon_url: string;
  tags: string[];
  capabilities: Record<string, boolean>;
  prompt_suggestions: PromptSuggestion[];
}

export interface PromptSuggestion {
  title: string;
  subtitle: string;
  content: string;
}

// ── Conversations ────────────────────────────────────────────────────────────

export interface ConversationSummary {
  id: string;
  title: string;
  persona: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  preview: string;
  pinned: boolean;
  pinned_at: string | null;
}

export interface Conversation {
  id: string;
  title: string;
  persona: string;
  created_at: string;
  updated_at: string;
  summary: string | null;
  messages: Message[];
}

export interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  thinking?: string | null;
  tool_calls?: ToolCall[] | null;
  rag_context?: RagContext | null;
  clarification?: Clarification | null;
  delegations?: Delegation[] | null;
  // True when the assistant turn was cut short by a stream error or
  // client disconnect. The content carries an inline `_(stream
  // interrupted: <reason>)_` marker matching the backend's
  // apply_stream_error_marker output. Set by useChat on the SSE
  // 'error' event so the partial response stays visible in the
  // message list before page reload (chat 3951063c, 2026-05-08).
  interrupted?: boolean;
  // P2 #25: auto-extracted memory candidates from this turn. Rendered
  // as accept/reject pills below the assistant bubble. Cleared once
  // the user acts on each one.
  memory_proposals?: MemoryProposal[] | null;
  // P2 #22: when this assistant turn triggered compaction, the
  // summary boundary is attached so a transcript reload still shows
  // the "earlier N messages summarised" divider above the bubble.
  compact_boundary?: CompactBoundary | null;
  // P2 #24 Phase 1: when this assistant turn invoked set_plan or
  // update_plan_item, the resulting plan snapshot is latched here
  // so the PlanCard divider renders above the bubble even after
  // transcript reload. Only the assistant message that LAST touched
  // the plan within a turn carries the snapshot.
  plan_snapshot?: Plan | null;
  // Authoring persona for this turn (persona-switch tracking). Set by
  // the backend per message; drives the PersonaDivider in the message
  // list. Null/undefined on legacy rows predating the column — treated
  // as "unattributed" so no spurious divider is drawn.
  persona?: string | null;
  created_at: string;
}

export interface Delegation {
  from_persona: string;
  to_persona: string;
  reason: string;
}

export interface ToolCall {
  id?: string;
  name: string;
  arguments: Record<string, unknown>;
  result?: unknown;
  duration_ms?: number;
  // Populated for invoke_agent tool calls
  agent?: AgentState | null;
}

export interface AgentState {
  name: string;
  query: string;
  thinking: string;
  toolCalls: ToolCall[];
  stoppedReason?: string;
  durationSeconds?: number;
}

export interface RagContext {
  sources_used: string[];
  documents: RagDocument[];
}

export interface RagDocument {
  title: string;
  source: string;
  score: number;
  doi?: string;
  content?: string;
}

// ── Artifacts ───────────────────────────────────────────────────────────────

export interface ArtifactSummary {
  id: string;
  title: string;
  content_type: string;
  language?: string;
  latest_version: number;
  word_count: number;
  byte_size: number;
  source: 'model_written' | 'sandbox_generated';
  filename?: string;
  size_bytes?: number;
  external_url?: string;
  created_at: string;
  updated_at: string;
}

export interface ArtifactFull extends ArtifactSummary {
  content: string;
  version: number;
  change_summary?: string;
  created_by?: 'assistant' | 'user';
}

export interface ArtifactCreatedEvent {
  id: string;
  source: 'model_written' | 'sandbox_generated';
  title: string;
  content_type: string;
  version: number;
  conversation_id: string;
  tool_call_id: string;
  language?: string;
  filename?: string;
  size_bytes?: number;
  external_url?: string;
}

export interface ArtifactUpdatedEvent {
  id: string;
  source: string;
  title: string;
  version: number;
  change_summary: string;
  created_by: 'assistant' | 'user';
  conversation_id: string;
  tool_call_id: string;
  applied_hunks: number | null;
  lines_added: number;
  lines_removed: number;
  base_version: number;
}

// ── Clarification ───────────────────────────────────────────────────────────

export interface ClarificationQuestion {
  id: string;
  text: string;
  options: string[];
  allow_custom: boolean;
}

export interface Clarification {
  tool_call_id: string;
  conversation_id: string;
  what_i_understood: string;
  questions: ClarificationQuestion[];
}

// ── SSE Events ───────────────────────────────────────────────────────────────

export type SSEEvent =
  | { type: 'metadata'; data: { persona: string; model: string; rag_sources?: string[] } }
  | { type: 'conversation'; data: { id: string; title: string; is_new: boolean; stream_id?: string | null; ephemeral?: boolean } }
  | { type: 'rag_context'; data: RagContext }
  | { type: 'thinking'; data: { content: string } }
  | { type: 'token'; data: { content: string } }
  | { type: 'tool_call'; data: { id: string; name: string; arguments: Record<string, unknown> } }
  | { type: 'tool_result'; data: { id: string; name: string; result: unknown; duration_ms?: number } }
  | { type: 'clarification'; data: Clarification }
  | { type: 'artifact_created'; data: ArtifactCreatedEvent }
  | { type: 'artifact_updated'; data: ArtifactUpdatedEvent }
  | { type: 'agent_start'; data: { agent: string; query: string } }
  | { type: 'agent_thinking'; data: { content: string } }
  | { type: 'agent_tool_call'; data: { id: string; name: string; arguments: Record<string, unknown> } }
  | { type: 'agent_tool_result'; data: { id: string; name: string; result: unknown; duration_ms?: number } }
  | { type: 'agent_done'; data: { agent: string; tool_calls: number; duration_seconds: number; stopped_reason: string } }
  | { type: 'delegated'; data: Delegation }
  | { type: 'persona_changed'; data: { id: string; persona: string } }
  | { type: 'retrying'; data: { attempt: number; max_attempts: number; delay_s: number; reason: string } }
  | { type: 'reconnecting'; data: { attempt: number; max_attempts: number; delay_s: number } }
  | { type: 'memory_proposed'; data: MemoryProposal }
  | { type: 'compact_boundary'; data: CompactBoundary }
  | { type: 'plan_updated'; data: Plan }
  | { type: 'plan_approval_required'; data: PlanApprovalRequired }
  | { type: 'error'; data: { message: string } }
  | { type: 'done'; data: { usage?: { prompt_tokens: number; completion_tokens: number }; finish_reason: string } };

// ── Compaction (P2 #22) ──────────────────────────────────────────────────────

export interface CompactBoundary {
  summary_through_index: number;
  dropped_messages: number;
  summary: string;
  is_fresh: boolean;
}

// ── Plan mode (P2 #24 Phase 1) ──────────────────────────────────────────────

export type PlanItemStatus = 'pending' | 'in_progress' | 'done' | 'cancelled';

export interface PlanItem {
  id: string;
  title: string;
  status: PlanItemStatus;
  notes?: string | null;
  updated_at?: string;
}

export interface Plan {
  conversation_id: string;
  items: PlanItem[];
  // Phase 2 fields, always present on the wire (defaults below):
  requires_approval: boolean;
  approved_at: string | null;
  approval_mode: 'each' | 'auto';
  created_at: string;
  updated_at: string;
}

// P2 #24 Phase 2: payload of the plan_approval_required SSE event.
// Fired by the preToolUse gate when a gated tool short-circuits.
export interface PlanApprovalRequired {
  tool: string;
  arguments: Record<string, unknown>;
  plan: Plan;
}

// ── Memory (P2 #25) ──────────────────────────────────────────────────────────

export interface MemoryProposal {
  id: string;
  key: string;
  value: string;
  reason?: string | null;
}

// ── Status ───────────────────────────────────────────────────────────────────

export interface SystemStatus {
  vllm: {
    status: 'running' | 'offline' | 'starting';
    model: string;
    next_start?: string;
  };
  // Set by the `munin-maintenance` cluster toggle. When active, the UI
  // shows the maintenance page instead of the sleeping page regardless
  // of vLLM status.
  maintenance?: {
    active: boolean;
    message?: string;
    since?: string;
  };
  services: Record<string, 'ok' | 'error' | 'unavailable'>;
  timestamp: string;
}

// ── Projects ─────────────────────────────────────────────────────────────────

export interface Project {
  id: string;
  user_email: string;
  name: string;
  description: string;
  instructions: string;
  default_persona: string | null;
  archived: boolean;
  created_at: string;
  updated_at: string;
  conversation_count: number;
}

// ── User Profile ─────────────────────────────────────────────────────────────

export interface MuninProfile {
  user_email: string;
  about_me: string | null;
  response_format: string | null;
  default_persona: string | null;
  default_rag_sources: string[] | null;
  timezone: string | null;
  created_at: string;
  updated_at: string;
}

// ── Knowledge / Tags ────────────────────────────────────────────────────────

export interface TagChip {
  kind: 'topic' | 'group' | 'contributor';
  value: string;
}

export interface TopicTag {
  slug: string;
  label: string;
  paper_count: number;
}

export interface GroupTag {
  slug: string;
  display_name: string;
  paper_count: number;
}

export interface ContributorTag {
  username: string;
  display_name: string;
  group_slug: string;
  paper_count: number;
}

export interface TagCatalog {
  topics: TopicTag[];
  groups: GroupTag[];
  contributors: ContributorTag[];
}

export interface TagPaper {
  title: string;
  doi: string;
  year: number;
  authors: string[];
  journal: string;
  contributors: {
    display_name: string;
    group_slug: string;
    group_display_name: string;
    upload_time: string;
  }[];
  topic?: {
    label: string;
    slug: string;
  };
  download_url?: string;
}

export interface TagPapersResponse {
  kind: string;
  slug: string;
  total: number;
  offset: number;
  limit: number;
  sort: string;
  papers: TagPaper[];
}

export interface EmbeddingMapCluster {
  id: number;
  label: string;
  slug: string;
  size: number;
  centroid: [number, number];
}

export interface EmbeddingMapPoint {
  id: string;
  doi: string;
  title: string;
  year: number;
  x: number;
  y: number;
  cluster: number;
}

export interface EmbeddingMap {
  generated_at: string;
  paper_count: number;
  cluster_count: number;
  points: EmbeddingMapPoint[];
  clusters: EmbeddingMapCluster[];
}

// ── Chat Request ─────────────────────────────────────────────────────────────

export type MessageContent = string | Array<{ type: string; text?: string; image_url?: { url: string } }>;

export interface ChatRequest {
  persona: string;
  conversation_id?: string | null;
  project_id?: string;
  messages: { role: string; content: MessageContent }[];
  rag?: { enabled: boolean; sources?: string[] };
  tags?: TagChip[];
  ephemeral?: boolean;
  stream: boolean;
}
