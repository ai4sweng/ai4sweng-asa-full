import { useState } from 'react'
import { useWorkflowStore } from '../store/workflow'
import { ArtifactInfo } from '../api/client'

function ArtifactCard({ artifact }: { artifact: ArtifactInfo }) {
  const [expanded, setExpanded] = useState(false)
  const data = artifact.artifact_data

  const findingCount =
    (data.finding_count as number | undefined) ??
    (Array.isArray(data.findings) ? (data.findings as unknown[]).length : null)
  const bugCount =
    (data.bug_count as number | undefined) ??
    (Array.isArray(data.bugs) ? (data.bugs as unknown[]).length : null)

  return (
    <div className="border border-gray-700 rounded-lg overflow-hidden">
      <button
        className="w-full flex items-center justify-between px-3 py-2 bg-gray-800 hover:bg-gray-750 transition-colors text-left"
        onClick={() => setExpanded((p) => !p)}
      >
        <div className="flex items-center gap-2">
          <span className="text-xs font-bold text-cyan-400">{artifact.producer_kio.toUpperCase()}</span>
          <span className="text-xs text-gray-400">{artifact.artifact_id.slice(0, 12)}…</span>
          {findingCount !== null && (
            <span className="text-xs bg-orange-900 text-orange-300 px-1.5 rounded">
              {findingCount} findings
            </span>
          )}
          {bugCount !== null && (
            <span className="text-xs bg-red-900 text-red-300 px-1.5 rounded">
              {bugCount} bugs
            </span>
          )}
        </div>
        <span className="text-gray-500 text-xs">{expanded ? '▲' : '▼'}</span>
      </button>

      {expanded && (
        <div className="bg-gray-900 p-3">
          <pre className="text-xs text-gray-300 overflow-auto max-h-64 whitespace-pre-wrap break-all">
            {JSON.stringify(data, null, 2)}
          </pre>
        </div>
      )}
    </div>
  )
}

export default function ArtifactViewer() {
  const { artifacts } = useWorkflowStore()

  if (artifacts.length === 0) return null

  return (
    <div className="mt-4">
      <h3 className="text-xs text-gray-400 mb-2 uppercase tracking-wider">
        Artifacts ({artifacts.length})
      </h3>
      <div className="space-y-2">
        {artifacts.map((a) => (
          <ArtifactCard key={a.artifact_id} artifact={a} />
        ))}
      </div>
    </div>
  )
}
