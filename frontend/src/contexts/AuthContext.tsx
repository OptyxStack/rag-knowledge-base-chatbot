import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react'

const STORAGE_KEY = 'support_ai_token'

export interface User {
  id: string
  username: string
  email: string | null
  role: string
}

interface AuthState {
  user: User | null
  token: string | null
  loading: boolean
}

interface AuthContextValue extends AuthState {
  login: (username: string, password: string) => Promise<void>
  logout: () => void
  setToken: (token: string | null) => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

const API_BASE =
  import.meta.env.VITE_API_BASE ||
  (import.meta.env.DEV ? 'http://localhost:8000/v1' : '/v1')
const AUTH_REQUIRED = import.meta.env.VITE_AUTH_REQUIRED !== 'false'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({
    user: null,
    token: localStorage.getItem(STORAGE_KEY),
    loading: true,
  })

  const fetchMe = useCallback(async (token: string) => {
    const res = await fetch(`${API_BASE}/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (!res.ok) return null
    return res.json() as Promise<User>
  }, [])

  useEffect(() => {
    let cancelled = false
    const token = state.token
    if (!token) {
      setState((s) => ({ ...s, loading: false }))
      return
    }
    fetchMe(token).then((user) => {
      if (cancelled) return
      if (!user) {
        localStorage.removeItem(STORAGE_KEY)
        setState((s) => ({ ...s, user: null, token: null, loading: false }))
      } else {
        setState((s) => ({ ...s, user, loading: false }))
      }
    })
    return () => { cancelled = true }
  }, [state.token, fetchMe])

  const login = useCallback(async (username: string, password: string) => {
    const res = await fetch(`${API_BASE}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || 'Login failed')
    }
    const data = (await res.json()) as { access_token: string; user: User }
    localStorage.setItem(STORAGE_KEY, data.access_token)
    setState({
      user: data.user,
      token: data.access_token,
      loading: false,
    })
  }, [])

  const logout = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY)
    setState({ user: null, token: null, loading: false })
  }, [])

  const setToken = useCallback((token: string | null) => {
    if (token) localStorage.setItem(STORAGE_KEY, token)
    else localStorage.removeItem(STORAGE_KEY)
    setState((s) => ({ ...s, token }))
  }, [])

  const value: AuthContextValue = {
    ...state,
    login,
    logout,
    setToken,
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}

export { AUTH_REQUIRED }
