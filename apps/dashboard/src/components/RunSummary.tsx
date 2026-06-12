import { useWorkflowStore } from '../store/workflow'
import { ArtifactInfo } from '../api/client'

/** Human-readable one-liner (or code block) for the latest artifact. */
function latestResult(artifact: ArtifactInfo): { label: string; body: string; code?: boolean } {
  const d = artifact.artifact_data
  const kio = artifact.producer_kio.toLowerCase()

  if (kio === 'kio9' && typeof d.code === 'string') {
    return { label: 'Generated code', body: d.code as string, code: true }
  }
  if (typeof d.verdict === 'string') {
    return { label: 'Verdict', body: d.verdict as string }
  }
  const bugCount = (d.bug_count as number | undefined) ??
    (Array.isArray(d.bugs) ? (d.bugs as unknown[]).length : undefined)
  if (bugCount !== undefined) {
    return { label: 'Bugs found', body: `${bugCount} bug(s)` }
  }
  const findingCount = (d.finding_count as number | undefined) ??
    (Array.isArray(d.findings) ? (d.findings as unknown[]).length : undefined)
  if (findingCount !== undefined) {
    return { label: 'Findings', body: `${findingCount} finding(s)` }
  }
  if (typeof d.summary === 'string' && d.summary) {
    return { label: 'Summary', body: d.summary as string }
  }
  return { label: 'Result', body: artifact.artifact_id.slice(0, 16) + '…' }
}

function fmtDuration(ms: number): string {
  if (ms <= 0) return '—'
  if (ms < 1000) return `${ms} ms`
  const s = ms / 1000
  return s < 60 ? `${s.toFixed(1)} s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`
}

export default function RunSummary() {
  const { sessionId, description, artifacts, events } = useWorkflowStore()
  if (!sessionId) return null

  const last = artifacts.length > 0 ? artifacts[artifacts.length - 1] : null
  const result = last ? latestResult(last) : null

  // Total elapsed = sum of orchestrator-measured KIO durations.
  const totalMs = events.reduce((acc, e) => acc + (e.execution_time_ms ?? 0), 0)
  // Total tokens = sum of per-KIO LLM usage carried on KIO_DONE events.
  const totalTokens = events.reduce(
    (acc, e) => acc + (e.tokens_in ?? 0) + (e.tokens_out ?? 0),
    0,
  )

  return (
    <div className="mt-6 space-y-4 text-sm">
      {/* Prompt */}
      <div>
        <h3 className="text-xs text-gray-400 mb-1 uppercase tracking-wider">Prompt</h3>
        <p className="text-gray-200 bg-gray-800/60 border border-gray-700 rounded px-3 py-2 break-words">
          {description || '—'}
        </p>
      </div>

      {/* Latest result */}
      <div>
        <h3 className="text-xs text-gray-400 mb-1 uppercase tracking-wider">Latest result</h3>
        {result ? (
          <div className="bg-gray-800/60 border border-gray-700 rounded px-3 py-2">
            <div className="flex items-center gap-2 mb-1">
              {last && (
                <span className="text-xs font-bold text-cyan-400">
                  {last.producer_kio.toUpperCase()}
                </span>
              )}
              <span className="text-xs text-gray-400">{result.label}</span>
            </div>
            {result.code ? (
              <pre className="text-xs text-green-300 overflow-auto max-h-72 whitespace-pre-wrap break-all font-mono">
                {result.body}
              </pre>
            ) : (
              <p className="text-gray-200 break-words">{result.body}</p>
            )}
          </div>
        ) : (
          <p className="text-gray-500 text-xs italic">Waiting for first result…</p>
        )}
      </div>

      {/* Footer: elapsed time + tokens */}
      <div className="flex items-center justify-between border-t border-gray-800 pt-3 text-xs">
        <div className="flex items-center gap-1.5 text-gray-400">
          <span>⏱</span>
          <span className="text-gray-200">{fmtDuration(totalMs)}</span>
          <span className="text-gray-600">elapsed</span>
        </div>
        <div className="flex items-center gap-1.5 text-gray-400">
          <span>🔢</span>
          <span className="text-gray-200">{totalTokens > 0 ? totalTokens.toLocaleString() : '—'}</span>
          <span className="text-gray-600">tokens</span>
        </div>
      </div>
    </div>
  )
}
