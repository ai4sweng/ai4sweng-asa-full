import { useState } from 'react'
import { useWorkflowStore } from '../store/workflow'

const KIO_OPTIONS = [
  'kio2', 'kio3', 'kio4', 'kio5', 'kio6',
  'kio7', 'kio8', 'kio9', 'kio10', 'kio11', 'kio12', 'kio13',
]

export default function WorkflowLauncher() {
  const { startWorkflow, loading, error, reset, sessionId } = useWorkflowStore()
  const [description, setDescription] = useState('')
  const [workingDir, setWorkingDir] = useState('')
  const [selectedKios, setSelectedKios] = useState<string[]>([])
  const [hitlAfter, setHitlAfter] = useState<string[]>(['kio3', 'kio5'])
  const [autoKios, setAutoKios] = useState(true)

  const toggleKio = (kio: string, setter: React.Dispatch<React.SetStateAction<string[]>>) =>
    setter((prev) => (prev.includes(kio) ? prev.filter((k) => k !== kio) : [...prev, kio]))

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    startWorkflow({
      description,
      kio_sequence: autoKios ? [] : selectedKios,
      hitl_after: hitlAfter,
      working_directory: workingDir || undefined,
    })
  }

  if (sessionId) {
    return (
      <div className="text-center py-4">
        <p className="text-green-400 text-sm mb-3">
          Workflow running — session <code className="text-xs">{sessionId.slice(0, 12)}…</code>
        </p>
        <button
          onClick={reset}
          className="text-xs text-gray-500 hover:text-gray-300 underline"
        >
          Start new workflow
        </button>
      </div>
    )
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div>
        <label className="text-xs text-gray-400 block mb-1">Task description</label>
        <textarea
          className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm focus:outline-none focus:border-brand resize-none"
          rows={3}
          placeholder="e.g. Find and fix bugs in the bundled FastAPI example repo"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          required
        />
      </div>

      <div>
        <label className="text-xs text-gray-400 block mb-1">
          Working directory (optional) — container path
        </label>
        <input
          className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm focus:outline-none focus:border-brand"
          placeholder="/repos/target/buggy_fastapi_repo"
          value={workingDir}
          onChange={(e) => setWorkingDir(e.target.value)}
        />
        <p className="text-[10px] text-gray-500 mt-1">
          Leave blank to use the bundled example. Paths are inside the KIO containers
          (host <code>./examples</code> is mounted at <code>/repos/target</code>).
        </p>
      </div>

      <div>
        <label className="flex items-center gap-2 text-sm cursor-pointer">
          <input
            type="checkbox"
            checked={autoKios}
            onChange={(e) => setAutoKios(e.target.checked)}
            className="accent-brand"
          />
          <span className="text-gray-300">Auto-plan KIO sequence via LM Engine</span>
        </label>
      </div>

      {!autoKios && (
        <div>
          <label className="text-xs text-gray-400 block mb-2">KIO sequence (in order)</label>
          <div className="flex flex-wrap gap-2">
            {KIO_OPTIONS.map((k) => (
              <button
                key={k}
                type="button"
                onClick={() => toggleKio(k, setSelectedKios)}
                className={`px-2 py-1 rounded text-xs border transition-colors ${
                  selectedKios.includes(k)
                    ? 'bg-brand border-brand text-white'
                    : 'border-gray-700 text-gray-400 hover:border-gray-500'
                }`}
              >
                {k.toUpperCase()}
              </button>
            ))}
          </div>
        </div>
      )}

      <div>
        <label className="text-xs text-gray-400 block mb-2">HITL checkpoints after</label>
        <div className="flex flex-wrap gap-2">
          {KIO_OPTIONS.map((k) => (
            <button
              key={k}
              type="button"
              onClick={() => toggleKio(k, setHitlAfter)}
              className={`px-2 py-1 rounded text-xs border transition-colors ${
                hitlAfter.includes(k)
                  ? 'bg-yellow-600 border-yellow-500 text-white'
                  : 'border-gray-700 text-gray-400 hover:border-gray-500'
              }`}
            >
              {k.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      {error && <p className="text-red-400 text-xs">{error}</p>}

      <button
        type="submit"
        disabled={loading || !description.trim()}
        className="w-full bg-brand hover:bg-brand-dark disabled:opacity-40 py-2.5 rounded font-medium text-sm transition-colors"
      >
        {loading ? 'Starting…' : 'Run Workflow'}
      </button>
    </form>
  )
}
