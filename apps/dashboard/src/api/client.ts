/** Thin API client — all requests go through the Vite proxy at /api → localhost:8000. */

const BASE = '/api'

function getToken(): string | null {
  return sessionStorage.getItem('kio1_token')
}

function authHeaders(): HeadersInit {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail ?? res.statusText)
  }
  return res.json() as Promise<T>
}

// --- Auth ---

export interface TokenResponse {
  access_token: string
  token_type: string
  expires_in: number
}

export interface UserInfo {
  user_id: string
  username: string
}

export async function login(username: string, password: string): Promise<TokenResponse> {
  return request('POST', '/auth/login', { username, password })
}

export async function register(
  username: string,
  password: string,
  email?: string,
): Promise<UserInfo> {
  return request('POST', '/auth/register', { username, password, email })
}

export async function getMe(): Promise<UserInfo> {
  return request('GET', '/auth/me')
}

// --- Workflow ---

export interface RunWorkflowRequest {
  description: string
  kio_sequence?: string[]
  hitl_after?: string[]
  working_directory?: string
}

export interface WorkflowStarted {
  session_id: string
  workflow_id: string
  status: string
  message: string
}

export interface WorkflowStatus {
  session_id: string
  workflow_id: string
  status: string
  progress_current: number
  progress_total: number
  active_kio: string | null
  pending_checkpoint_id: string | null
  artifacts: ArtifactInfo[]
  log: string[]
}

export interface ArtifactInfo {
  artifact_id: string
  producer_kio: string
  artifact_type: string
  artifact_data: Record<string, unknown>
  state: string
}

export async function runWorkflow(req: RunWorkflowRequest): Promise<WorkflowStarted> {
  return request('POST', '/workflow/run', req)
}

export async function getWorkflowStatus(sessionId: string): Promise<WorkflowStatus> {
  return request('GET', `/workflow/${sessionId}/status`)
}

export async function approveHitl(
  sessionId: string,
  feedback: string,
): Promise<{ status: string; message: string }> {
  return request('POST', `/workflow/${sessionId}/approve`, { feedback })
}

// --- SSE ---

export function openEventStream(
  onEvent: (data: Record<string, unknown>) => void,
  onError?: () => void,
): EventSource {
  const token = getToken()
  // EventSource doesn't support custom headers; pass token as query param
  const src = new EventSource(`${BASE}/workflow/events?token=${token ?? ''}`)
  src.onmessage = (e) => {
    try {
      onEvent(JSON.parse(e.data) as Record<string, unknown>)
    } catch {
      // ignore parse errors
    }
  }
  if (onError) src.onerror = onError
  return src
}
