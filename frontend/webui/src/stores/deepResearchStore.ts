import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';

// Deep Research runs as a detached background job whose progress is a durable,
// ordered EVENT LOG (see backend research_store). The frontend renders that log
// inline in the conversation - plan checklist + a tool card per search/read +
// notes + the final report - exactly like normal tool use. This store holds the
// current conversation's job; App loads/polls it and clears on switch.
export type DeepResearchStatus =
  | 'queued' | 'running' | 'done' | 'error' | 'cancelled';

export interface ResearchEvent {
  t: number;
  type: string;
  [k: string]: unknown;
}

export interface DeepResearchJob {
  id: string;
  conversationId: string;
  question: string;
  status: DeepResearchStatus;
  events: ResearchEvent[];
  artifactId?: string | null;
  startedAt: number;      // client epoch ms, for the elapsed display
}

export function isActive(status?: string): boolean {
  return status === 'queued' || status === 'running';
}

interface DeepResearchState {
  job: DeepResearchJob | null;
  // Optimistically start (before the first poll): question + conversation known.
  startJob: (id: string, question: string, conversationId: string, startedAt: number) => void;
  // Merge a server job snapshot (status + events + artifact) into the store.
  setJob: (job: {
    job_id?: string; id?: string; conversation_id?: string; question?: string;
    status?: string; events?: ResearchEvent[]; artifact_id?: string | null;
  }) => void;
  clear: () => void;
}

export const useDeepResearchStore = create<DeepResearchState>()(
  immer((set) => ({
    job: null,
    startJob: (id, question, conversationId, startedAt) =>
      set((state) => {
        state.job = { id, conversationId, question, status: 'queued', events: [], startedAt };
      }),
    setJob: (j) =>
      set((state) => {
        const id = j.id || j.job_id;
        if (!id) return;
        const startedAt = state.job?.id === id ? state.job.startedAt : Date.now();
        state.job = {
          id,
          conversationId: j.conversation_id || state.job?.conversationId || '',
          question: j.question || state.job?.question || '',
          status: (j.status as DeepResearchStatus) || state.job?.status || 'running',
          events: j.events || state.job?.events || [],
          artifactId: j.artifact_id ?? state.job?.artifactId ?? null,
          startedAt,
        };
      }),
    clear: () => set((state) => { state.job = null; }),
  })),
);
