import { useState } from 'react'
import { useWorkflowStore } from '../store/workflow'

export default function HitlPanel() {
  const { status, approve, loading } = useWorkflowStore()
  const [feedback, setFeedback] = useState('')

  if (!status || status.status !== 'PENDING_REVIEW') return null

  const handleApprove = () => {
    approve(feedback.trim())
    setFeedback('')
  }

  return (
    <div className="mt-4 p-4 bg-yellow-950 border border-yellow-700 rounded-xl">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-yellow-400 text-lg">⏸</span>
        <h3 className="text-yellow-300 font-semibold text-sm">Human Review Required</h3>
      </div>
      <p className="text-yellow-200 text-xs mb-4">
        Checkpoint: <code className="bg-yellow-900 px-1 rounded">
          {status.pending_checkpoint_id?.slice(0, 16)}…
        </code>
      </p>

      <textarea
        className="w-full bg-yellow-900/30 border border-yellow-700 rounded px-3 py-2 text-sm text-yellow-100 placeholder-yellow-700 focus:outline-none focus:border-yellow-500 resize-none"
        rows={3}
        placeholder="Optional feedback or revision instructions…"
        value={feedback}
        onChange={(e) => setFeedback(e.target.value)}
      />

      <div className="flex gap-3 mt-3">
        <button
          onClick={handleApprove}
          disabled={loading}
          className="flex-1 bg-green-700 hover:bg-green-600 disabled:opacity-40 py-2 rounded text-sm font-medium transition-colors"
        >
          {loading ? 'Approving…' : feedback.trim() ? 'Approve with Feedback' : 'Approve'}
        </button>
      </div>
    </div>
  )
}
