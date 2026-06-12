import { useAuthStore } from './store/auth'
import Login from './components/Login'
import WorkflowLauncher from './components/WorkflowLauncher'
import WorkflowStream from './components/WorkflowStream'
import HitlPanel from './components/HitlPanel'
import ArtifactViewer from './components/ArtifactViewer'
import RunSummary from './components/RunSummary'

function Header() {
  const { user, token, logout } = useAuthStore()
  if (!token) return null
  return (
    <header className="border-b border-gray-800 px-6 py-3 flex items-center justify-between">
      <div className="flex items-center gap-3">
        <span className="text-brand font-bold text-lg">KIO1</span>
        <span className="text-gray-500 text-sm">AI Engineering Platform</span>
      </div>
      <div className="flex items-center gap-4">
        {user && <span className="text-xs text-gray-400">@{user.username}</span>}
        <button
          onClick={logout}
          className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
        >
          Sign out
        </button>
      </div>
    </header>
  )
}

export default function App() {
  const { token } = useAuthStore()

  if (!token) return <Login />

  return (
    <div className="min-h-screen flex flex-col">
      <Header />
      <main className="flex-1 flex gap-0">
        {/* Left panel — workflow launcher */}
        <aside className="w-96 border-r border-gray-800 p-6 overflow-y-auto">
          <h2 className="text-sm font-semibold text-gray-300 mb-4 uppercase tracking-wider">
            New Workflow
          </h2>
          <WorkflowLauncher />
          <RunSummary />
        </aside>

        {/* Right panel — live stream + HITL + artifacts */}
        <section className="flex-1 p-6 overflow-y-auto">
          <HitlPanel />
          <WorkflowStream />
          <ArtifactViewer />
        </section>
      </main>
    </div>
  )
}
