import { create } from 'zustand'
import {
  runWorkflow,
  getWorkflowStatus,
  approveHitl,
  openEventStream,
  WorkflowStatus,
  ArtifactInfo,
  RunWorkflowRequest,
} from '../api/client'

interface WorkflowEvent {
  event_type: string
  session_id?: string
  message: string
  data?: Record<string, unknown>
  timestamp?: string
  // HITL_CHECKPOINT carries these at the top level (orchestrator flattens event.data)
  checkpoint_id?: string
  kio?: string
  hitl_question?: string
}

interface PendingHitl {
  checkpoint_id: string
  kio: string
  question: string
}

interface WorkflowStore {
  sessionId: string | null
  status: WorkflowStatus | null
  events: WorkflowEvent[]
  artifacts: ArtifactInfo[]
  pendingHitl: PendingHitl | null
  loading: boolean
  error: string | null
  eventSource: EventSource | null

  startWorkflow: (req: RunWorkflowRequest) => Promise<void>
  refreshStatus: () => Promise<void>
  approve: (feedback: string) => Promise<void>
  connectStream: () => void
  disconnectStream: () => void
  clearError: () => void
  reset: () => void
}

export const useWorkflowStore = create<WorkflowStore>((set, get) => ({
  sessionId: null,
  status: null,
  events: [],
  artifacts: [],
  pendingHitl: null,
  loading: false,
  error: null,
  eventSource: null,

  startWorkflow: async (req) => {
    set({ loading: true, error: null, events: [], artifacts: [], status: null, pendingHitl: null })
    try {
      const started = await runWorkflow(req)
      set({ sessionId: started.session_id, loading: false })
      get().connectStream()
      // Poll initial status
      await get().refreshStatus()
    } catch (e) {
      set({ error: (e as Error).message, loading: false })
    }
  },

  refreshStatus: async () => {
    const { sessionId } = get()
    if (!sessionId) return
    try {
      const status = await getWorkflowStatus(sessionId)
      set({ status, artifacts: status.artifacts })
    } catch {
      // ignore transient poll errors
    }
  },

  approve: async (feedback) => {
    const { sessionId } = get()
    if (!sessionId) return
    set({ loading: true, error: null })
    try {
      await approveHitl(sessionId, feedback)
      set({ loading: false, pendingHitl: null })
    } catch (e) {
      set({ error: (e as Error).message, loading: false })
    }
  },

  connectStream: () => {
    const existing = get().eventSource
    if (existing) existing.close()

    const src = openEventStream(
      (data) => {
        const evt = data as unknown as WorkflowEvent
        set((s) => ({ events: [...s.events.slice(-199), evt] }))

        // Drive the HITL panel straight off the event stream — do NOT depend on a
        // status poll succeeding (that path can race or hit an id/auth hiccup and
        // then the approve button never appears).
        if (evt.event_type === 'HITL_CHECKPOINT' && evt.checkpoint_id) {
          set({
            pendingHitl: {
              checkpoint_id: evt.checkpoint_id,
              kio: evt.kio ?? '',
              question: evt.hitl_question ?? evt.message,
            },
          })
        }
        // Any forward progress or terminal state clears the pending checkpoint.
        if (
          evt.event_type === 'HITL_APPROVED' ||
          evt.event_type === 'KIO_DONE' ||
          evt.event_type === 'WORKFLOW_COMPLETED' ||
          evt.event_type === 'WORKFLOW_FAILED'
        ) {
          set({ pendingHitl: null })
        }

        // Auto-refresh status on significant events (best-effort; panel no longer relies on it)
        if (
          evt.event_type === 'KIO_DONE' ||
          evt.event_type === 'HITL_CHECKPOINT' ||
          evt.event_type === 'WORKFLOW_COMPLETED' ||
          evt.event_type === 'WORKFLOW_FAILED'
        ) {
          get().refreshStatus()
        }
      },
      () => set({ error: 'SSE connection lost — refresh to reconnect.' }),
    )
    set({ eventSource: src })
  },

  disconnectStream: () => {
    get().eventSource?.close()
    set({ eventSource: null })
  },

  clearError: () => set({ error: null }),

  reset: () => {
    get().disconnectStream()
    set({
      sessionId: null,
      status: null,
      events: [],
      artifacts: [],
      pendingHitl: null,
      loading: false,
      error: null,
    })
  },
}))
