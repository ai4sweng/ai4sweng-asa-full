import { useEffect, useRef } from 'react'
import { useWorkflowStore } from '../store/workflow'

const EVENT_COLORS: Record<string, string> = {
  SESSION_CREATED: 'text-blue-400',
  PLANNING_STARTED: 'text-purple-400',
  PLANNING_DONE: 'text-purple-300',
  KIO_STARTED: 'text-cyan-400',
  KIO_DONE: 'text-green-400',
  HITL_CHECKPOINT: 'text-yellow-400',
  HITL_APPROVED: 'text-green-300',
  HITL_FEEDBACK_REVISION: 'text-orange-400',
  WORKFLOW_COMPLETED: 'text-emerald-400',
  WORKFLOW_FAILED: 'text-red-400',
  CONNECTED: 'text-gray-500',
}

function ProgressBar({ current, total }: { current: number; total: number }) {
  const pct = total > 0 ? Math.round((current / total) * 100) : 0
  return (
    <div className="mb-3">
      <div className="flex justify-between text-xs text-gray-400 mb-1">
        <span>Progress</span>
        <span>{current}/{total} KIOs</span>
      </div>
      <div className="bg-gray-700 rounded-full h-1.5">
        <div
          className="bg-brand rounded-full h-1.5 transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

export default function WorkflowStream() {
  const { status, events, sessionId } = useWorkflowStore()
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events.length])

  if (!sessionId) return null

  return (
    <div className="mt-6">
      {status && (
        <div className="mb-4 p-3 bg-gray-900 border border-gray-700 rounded-lg">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs text-gray-400">Session</span>
            <code className="text-xs text-gray-300">{sessionId.slice(0, 16)}…</code>
          </div>
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs text-gray-400">Status</span>
            <StatusBadge status={status.status} />
          </div>
          {status.active_kio && (
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs text-gray-400">Active KIO</span>
              <span className="text-xs text-cyan-400 font-bold">{status.active_kio.toUpperCase()}</span>
            </div>
          )}
          <ProgressBar current={status.progress_current} total={status.progress_total} />
        </div>
      )}

      <div className="bg-gray-900 border border-gray-700 rounded-lg overflow-hidden">
        <div className="px-3 py-2 border-b border-gray-700 flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
          <span className="text-xs text-gray-400">Live event stream</span>
        </div>
        <div className="h-64 overflow-y-auto p-3 space-y-1 font-mono text-xs">
          {events.map((evt, i) => (
            <div key={i} className="flex gap-2">
              <span className="text-gray-600 shrink-0">
                {evt.timestamp ? new Date(evt.timestamp).toLocaleTimeString() : '--:--:--'}
              </span>
              <span className={`${EVENT_COLORS[evt.event_type] ?? 'text-gray-300'} shrink-0`}>
                [{evt.event_type}]
              </span>
              <span className="text-gray-300 break-all">{evt.message}</span>
            </div>
          ))}
          <div ref={bottomRef} />
        </div>
      </div>
    </div>
  )
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    ACTIVE: 'bg-blue-900 text-blue-300',
    PENDING_REVIEW: 'bg-yellow-900 text-yellow-300',
    COMPLETED: 'bg-green-900 text-green-300',
    FAILED: 'bg-red-900 text-red-300',
  }
  return (
    <span className={`text-xs px-2 py-0.5 rounded ${colors[status] ?? 'bg-gray-800 text-gray-300'}`}>
      {status}
    </span>
  )
}
