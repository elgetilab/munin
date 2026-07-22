import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';

// Deep Research runs as a long (many-minute) detached backend job, not a chat
// turn. This store tracks the single active job so the composer can show its
// live status and App can poll it. The finished report is delivered as a
// markdown artifact and appears in the artifact panel (App refetches on done).
export type DeepResearchStatus =
  | 'queued' | 'running' | 'done' | 'error' | 'cancelled';

export interface DeepResearchJob {
  id: string;
  question: string;
  status: DeepResearchStatus;
  conversationId: string;
  startedAt: number;     // client epoch ms, for the elapsed-time display
  step: string;          // latest human-readable progress step
}

interface DeepResearchState {
  job: DeepResearchJob | null;
  startJob: (id: string, question: string, conversationId: string, startedAt: number) => void;
  setStatus: (status: DeepResearchStatus) => void;
  setProgress: (step: string) => void;
  clear: () => void;
}

export const useDeepResearchStore = create<DeepResearchState>()(
  immer((set) => ({
    job: null,
    startJob: (id, question, conversationId, startedAt) =>
      set((state) => {
        state.job = { id, question, status: 'queued', conversationId, startedAt, step: 'Starting' };
      }),
    setStatus: (status) =>
      set((state) => {
        if (state.job) state.job.status = status;
      }),
    setProgress: (step) =>
      set((state) => {
        if (state.job && step) state.job.step = step;
      }),
    clear: () =>
      set((state) => {
        state.job = null;
      }),
  })),
);
