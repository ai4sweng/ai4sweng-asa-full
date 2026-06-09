import { create } from 'zustand'
import { login as apiLogin, register as apiRegister, UserInfo } from '../api/client'

interface AuthState {
  user: UserInfo | null
  token: string | null
  error: string | null
  loading: boolean
  login: (username: string, password: string) => Promise<void>
  register: (username: string, password: string, email?: string) => Promise<void>
  logout: () => void
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  token: sessionStorage.getItem('kio1_token'),
  error: null,
  loading: false,

  login: async (username, password) => {
    set({ loading: true, error: null })
    try {
      const resp = await apiLogin(username, password)
      sessionStorage.setItem('kio1_token', resp.access_token)
      set({ token: resp.access_token, user: { user_id: '', username }, loading: false })
    } catch (e) {
      set({ error: (e as Error).message, loading: false })
    }
  },

  register: async (username, password, email) => {
    set({ loading: true, error: null })
    try {
      const user = await apiRegister(username, password, email)
      // Auto-login after register
      const resp = await apiLogin(username, password)
      sessionStorage.setItem('kio1_token', resp.access_token)
      set({ token: resp.access_token, user, loading: false })
    } catch (e) {
      set({ error: (e as Error).message, loading: false })
    }
  },

  logout: () => {
    sessionStorage.removeItem('kio1_token')
    set({ user: null, token: null, error: null })
  },
}))
